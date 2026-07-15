# -*- coding: utf-8 -*-
"""Moteur de comparaison avant (maquette Excel) / après (plan PDF modificatif).

Clé de comparaison : (pièce normalisée, catégorie, matériel rapproché).
Le rapprochement des noms de matériel se fait en 3 étages :
  1. correspondance exacte (noms normalisés)
  2. fuzzy matching local (difflib)
  3. LLM (endpoint LIHA) pour les libellés restants — optionnel
"""
import json
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import pandas as pd

from .. import config, llm
from ..extract.pdf_reader import PdfExtraction

# cache persistant article plan -> matériel maquette (éditable à la main ;
# une valeur null = « pas d'équivalent, ne plus demander au LLM »)
MAP_CACHE_PATH = config.DATA_DIR / "material_map_cache.json"
MATERIAL_RULES_PATH = config.DATA_DIR / "material_rules.json"

FUZZY_ROOM_THRESHOLD = 0.62
FUZZY_MATERIAL_THRESHOLD = 0.65

STATUT_INCHANGE = "INCHANGÉ"
STATUT_MODIFIE = "MODIFIÉ"
STATUT_AJOUT = "AJOUT"
STATUT_NON_DETECTE = "NON DÉTECTÉ SUR PLAN (à vérifier)"
STATUT_A_VALIDER = "À VALIDER (article inconnu de la maquette)"


@dataclass
class CompareResult:
    table: pd.DataFrame = None            # comparatif détaillé
    room_matches: list = field(default_factory=list)   # [(pdf_room, excel_piece, score)]
    unmatched_rooms: list = field(default_factory=list)
    material_mapping: dict = field(default_factory=dict)  # {article_pdf: (materiel_excel, méthode, score)}
    remarks: list = field(default_factory=list)
    niveau: str = ""
    llm_used: bool = False


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _match_rooms(pdf_rooms: list[str], excel_pieces: list[str], overrides: dict | None = None) -> tuple[dict, list]:
    """Associe chaque pièce du PDF à une pièce de la maquette.
    fuzzy d'abord, puis règle de contention (« Attente 1 8 PLACES » ⊃ « Attente 1 »).
    Les pièces sans correspondance sont des pièces créées par les travaux."""
    matches, unmatched = {}, []
    overrides = overrides or {}
    for room in pdf_rooms:
        if room in overrides:
            manual = str(overrides.get(room) or "").strip()
            if manual:
                matches[room] = (manual, 1.0)
            else:
                unmatched.append(room)
            continue
        rn = _norm(room)
        best, best_score = None, 0.0
        for piece in excel_pieces:
            score = _ratio(room, piece)
            if score > best_score:
                best, best_score = piece, score
        if best is not None and best_score >= FUZZY_ROOM_THRESHOLD:
            matches[room] = (best, round(best_score, 2))
            continue
        contained = [p for p in excel_pieces
                     if len(_norm(p)) >= 4 and (_contains_words(rn, _norm(p)) or _contains_words(_norm(p), rn))]
        if contained:
            matches[room] = (max(contained, key=lambda p: len(_norm(p))), 0.75)
        else:
            unmatched.append(room)
    return matches, unmatched


def _contains_words(haystack: str, needle: str) -> bool:
    """Contention sur limites de mots (évite « ns f » ⊂ « praticie·ns f »)."""
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def _load_map_cache() -> dict:
    if MAP_CACHE_PATH.exists():
        try:
            return json.loads(MAP_CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _load_material_rules() -> dict:
    if MATERIAL_RULES_PATH.exists():
        try:
            return json.loads(MATERIAL_RULES_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _save_map_cache(cache: dict) -> None:
    MAP_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )


def _alias_candidates(article: str, rules: dict) -> list[str]:
    candidates: list[str] = []
    article_norm = _norm(article)
    for rule in rules.get("aliases", []):
        if _norm(rule.get("article", "")) != article_norm:
            continue
        for material in rule.get("materials", []):
            if material and material not in candidates:
                candidates.append(str(material))
    return candidates


def _expand_symbol_rows(symbols, rules: dict) -> list[dict]:
    components = {
        _norm(rule.get("article", "")): rule.get("items", [])
        for rule in rules.get("components", [])
        if rule.get("article")
    }
    rows = []
    for symbol in symbols:
        items = components.get(_norm(symbol.article))
        if not items:
            rows.append({
                "room": symbol.room, "article": symbol.article, "categorie": symbol.categorie,
                "page": symbol.page, "page_type": symbol.page_type, "label": symbol.label,
            })
            continue
        for item in items:
            quantity = max(1, int(item.get("quantity") or 1))
            for _ in range(quantity):
                rows.append({
                    "room": symbol.room,
                    "article": str(item.get("article") or symbol.article),
                    "categorie": str(item.get("categorie") or symbol.categorie),
                    "page": symbol.page,
                    "page_type": symbol.page_type,
                    "label": f"{symbol.label} · {symbol.article}",
                })
    return rows


def _excel_category_maps(scope: pd.DataFrame) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    by_piece_material: dict[tuple[str, str], str] = {}
    by_material: dict[str, str] = {}
    usable = scope[(scope["materiel"] != "") & (scope["categorie"] != "")]
    for (piece, material), group in usable.groupby(["piece", "materiel"], sort=False):
        category = str(group["categorie"].mode().iat[0] if not group["categorie"].mode().empty else group["categorie"].iat[0])
        by_piece_material[(str(piece), str(material))] = category
    for material, group in usable.groupby("materiel", sort=False):
        category = str(group["categorie"].mode().iat[0] if not group["categorie"].mode().empty else group["categorie"].iat[0])
        by_material[str(material)] = category
    return by_piece_material, by_material


def _match_materials(pdf_articles: list[str], excel_materials: list[str], overrides: dict | None = None,
                     validated_articles: list[str] | None = None) -> tuple[dict, bool]:
    """Rapproche les articles du plan des matériels maquette.
    4 étages : cache manuel/persisté -> exact -> fuzzy -> LLM."""
    mapping: dict[str, tuple[str, str, float]] = {}
    cache = _load_map_cache()
    rules = _load_material_rules()
    norm_index = {_norm(m): m for m in excel_materials}
    valid = set(excel_materials)
    overrides = overrides or {}
    validated = {_norm(article) for article in (validated_articles or []) if str(article).strip()}

    remaining = []
    for art in pdf_articles:
        if art in overrides:
            manual = str(overrides.get(art) or "").strip()
            if manual:
                mapping[art] = (manual, "correspondance utilisateur", 1.0)
            continue
        if _norm(art) in validated:
            mapping[art] = (art, "validé sans équivalent Excel", 1.0)
            continue
        exact = norm_index.get(_norm(art))
        if exact:
            mapping[art] = (exact, "exact", 1.0)
            continue
        alias_match = None
        for candidate in _alias_candidates(art, rules):
            alias_match = norm_index.get(_norm(candidate))
            if alias_match:
                mapping[art] = (alias_match, "règle", 1.0)
                break
        if alias_match:
            continue
        if art in cache:
            cached = cache[art]
            if cached is None:
                continue                    # pas d'équivalent connu, ne pas re-demander
            if cached in valid:
                mapping[art] = (cached, "cache", 1.0)
                continue
        remaining.append(art)

    still = []
    for art in remaining:
        best, best_score = None, 0.0
        for candidate in [art] + _alias_candidates(art, rules):
            for mat in excel_materials:
                score = _ratio(candidate, mat)
                if score > best_score:
                    best, best_score = mat, score
        if best is not None and best_score >= FUZZY_MATERIAL_THRESHOLD:
            mapping[art] = (best, "fuzzy", round(best_score, 2))
        else:
            still.append(art)

    llm_used = False
    if still:
        suggested = llm.suggest_material_mapping(still, excel_materials)
        if suggested is not None:       # None = appel échoué, on ne mémorise rien
            llm_used = True
            for art, mat in suggested.items():
                mapping[art] = (mat, "llm", 0.9)
            # mémoriser aussi les non-correspondances (null) pour ne pas re-payer
            for art in still:
                cache[art] = suggested.get(art)
            _save_map_cache(cache)
    return mapping, llm_used


def compare(excel_df: pd.DataFrame, pdf: PdfExtraction,
            room_overrides: dict | None = None, material_overrides: dict | None = None,
            niveau_excel: str | None = None, nom_niveau: str | None = None,
            validated_articles: list[str] | None = None) -> CompareResult:
    display_level = (nom_niveau or "").strip() or (niveau_excel or "").strip() \
        or (f"R{pdf.niveau_hint}" if pdf.niveau_hint else "")
    res = CompareResult(remarks=list(pdf.remarks), niveau=display_level)

    # 1. restreindre la maquette au niveau du plan si détecté (ex: R2 -> "Niveau 2")
    scope = excel_df
    if niveau_excel:
        selected = scope[scope["niveau"].str.casefold() == niveau_excel.strip().casefold()]
        if selected.empty:
            raise ValueError(f"Le niveau Excel sélectionné est introuvable : {niveau_excel}")
        scope = selected
    elif pdf.niveau_hint:
        lvl = scope[scope["niveau"].str.contains(rf"Niveau {pdf.niveau_hint}", case=False, na=False)]
        if not lvl.empty:
            scope = lvl

    # 2. rapprocher les pièces PDF <-> maquette
    pdf_rooms = [r.name for r in pdf.rooms]
    excel_pieces = sorted(scope["piece"].unique())
    room_map, unmatched = _match_rooms(pdf_rooms, excel_pieces, room_overrides)
    res.room_matches = [(k, v[0], v[1]) for k, v in room_map.items()]
    res.unmatched_rooms = unmatched

    matched_pieces = {v[0] for v in room_map.values()}
    avant = scope[scope["piece"].isin(matched_pieces) & (scope["materiel"] != "")].copy()

    # 3. table "après" : comptage des symboles par pièce/catégorie/article
    material_rules = _load_material_rules()
    sym_rows = _expand_symbol_rows(pdf.symbols, material_rules)
    apres = pd.DataFrame(sym_rows)
    if apres.empty:
        apres = pd.DataFrame(columns=["room", "article", "categorie", "page", "page_type", "label"])
    # pièce non trouvée dans la maquette = pièce créée par les travaux modificatifs :
    # on la conserve, ses équipements sont des ajouts
    apres["piece"] = apres["room"].map(
        lambda r: room_map[r][0] if r in room_map else f"{r} [nouvelle pièce]"
    )
    apres = apres[apres["room"] != ""]

    # 4. rapprocher les noms d'articles PDF <-> matériel maquette
    # vocabulaire = tout le niveau (les pièces nouvelles utilisent le même matériel)
    pdf_articles = sorted(apres["article"].unique())
    excel_materials = sorted(m for m in scope["materiel"].unique() if m)
    mat_map, res.llm_used = _match_materials(pdf_articles, excel_materials, material_overrides, validated_articles)
    res.material_mapping = mat_map
    apres["materiel"] = apres["article"].map(lambda a: mat_map.get(a, (a,))[0])
    category_by_piece_material, category_by_material = _excel_category_maps(scope)
    apres["categorie"] = apres.apply(
        lambda row: category_by_piece_material.get((str(row["piece"]), str(row["materiel"])))
        or category_by_material.get(str(row["materiel"]))
        or row["categorie"],
        axis=1,
    )

    apres_grp = (
        apres.groupby(["piece", "categorie", "materiel"])
        .agg(quantite_apres=("article", "size"),
             pages=("page", lambda s: ",".join(map(str, sorted(set(s))))),
             labels=("label", lambda s: ", ".join(sorted(set(s)))))
        .reset_index()
    )

    # groupement par NOM de pièce : des pièces homonymes (ex. 7 « Consultation »
    # sur le niveau) sont agrégées, l'ambiguïté est signalée dans la colonne N°
    def _join_numeros(s):
        uniq = sorted({str(v) for v in s if str(v).strip()})
        return uniq[0] if len(uniq) == 1 else f"{len(uniq)} pièces homonymes"

    avant_grp = (
        avant.groupby(["piece", "categorie", "materiel"])
        .agg(quantite_avant=("quantite", "sum"),
             numero=("numero", _join_numeros),
             niveau=("niveau", "first"))
        .reset_index()
    )

    # 5. fusion avant/après (outer) et statut
    merged = avant_grp.merge(apres_grp, on=["piece", "categorie", "materiel"], how="outer")
    merged["quantite_avant"] = merged["quantite_avant"].fillna(0).astype(int)
    merged["quantite_apres"] = merged["quantite_apres"].fillna(0).astype(int)
    merged["ecart"] = merged["quantite_apres"] - merged["quantite_avant"]

    known = {v[0] for v in mat_map.values()}

    def statut(row):
        if row["quantite_avant"] and not row["quantite_apres"]:
            return STATUT_NON_DETECTE
        if not row["quantite_avant"] and row["quantite_apres"]:
            # « known » = article rapproché d'un matériel maquette, quelle que soit
            # la méthode (cache/exact/fuzzy/llm) ; sinon l'article est inconnu
            return STATUT_AJOUT if row["materiel"] in known else STATUT_A_VALIDER
        return STATUT_INCHANGE if row["ecart"] == 0 else STATUT_MODIFIE

    merged["statut"] = merged.apply(statut, axis=1)

    # méthode de rapprochement pour la traçabilité
    method_by_mat = {v[0]: f"{v[1]} ({v[2]})" for v in mat_map.values()}
    merged["rapprochement"] = merged["materiel"].map(method_by_mat).fillna("maquette seule")

    piece_meta = avant_grp.drop_duplicates("piece").set_index("piece")[["numero", "niveau"]]
    merged["numero"] = merged["numero"].fillna(merged["piece"].map(piece_meta["numero"])).fillna("")
    merged["niveau"] = merged["niveau"].fillna(merged["piece"].map(piece_meta["niveau"])).fillna("")
    if display_level:
        merged["niveau"] = display_level
    merged["pages"] = merged["pages"].fillna("")
    merged["labels"] = merged["labels"].fillna("")

    merged = merged.sort_values(["piece", "categorie", "materiel"]).reset_index(drop=True)
    res.table = merged
    return res
