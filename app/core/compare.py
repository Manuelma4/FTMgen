# -*- coding: utf-8 -*-
"""Moteur de comparaison avant (maquette Excel) / après (plan PDF modificatif).

Clé de comparaison : (pièce normalisée, catégorie, matériel rapproché).
Le rapprochement des noms de matériel se fait en 3 étages :
  1. correspondance exacte (noms normalisés)
  2. fuzzy matching local (difflib)
  3. LLM (endpoint LIHA) pour les libellés restants — optionnel
"""
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import pandas as pd

from .. import config, llm
from ..extract.pdf_reader import PdfExtraction
from .relations import (
    excel_scope_options,
    filter_excel_scope,
    infer_excel_scope,
    excel_room_id,
    excel_room_label,
    excel_room_options,
    object_relation_key,
    resolve_excel_room,
    resolve_excel_scope,
)

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
    room_match_details: dict = field(default_factory=dict)  # {piece_pdf: option Excel + score}
    object_mapping: dict = field(default_factory=dict)  # {mapping_key: relation effective}
    excluded_relations: set = field(default_factory=set)
    remarks: list = field(default_factory=list)
    niveau: str = ""
    excel_scope_id: str = ""
    selected_scope: dict = field(default_factory=dict)
    scope_options: list = field(default_factory=list)
    scope_selection_method: str = ""
    llm_used: bool = False


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _match_rooms(pdf_rooms: list[str], options: list[dict], overrides: dict | None = None) -> tuple[dict, list]:
    """Associe une pièce PDF à une *instance* Excel non ambiguë.

    Un libellé homonyme n'est jamais choisi automatiquement. Par exemple cinq
    ``Secrétariat`` sur R+2 donnent cinq options physiques différentes : il faut
    alors une relation utilisateur contenant l'identifiant Excel exact.
    """
    matches: dict[str, tuple[dict, float, str]] = {}
    unmatched: list[str] = []
    overrides = overrides or {}
    overrides_by_norm = {_norm(source): target for source, target in overrides.items()}
    by_piece: dict[str, list[dict]] = {}
    for option in options:
        by_piece.setdefault(_norm(option.get("piece", "")), []).append(option)

    for room in pdf_rooms:
        room_norm = _norm(room)
        if room in overrides or room_norm in overrides_by_norm:
            target = overrides.get(room, overrides_by_norm.get(room_norm, ""))
            resolved = resolve_excel_room(target, options)
            if resolved is not None:
                matches[room] = (resolved, 1.0, "correspondance utilisateur")
            else:
                unmatched.append(room)
            continue

        exact = by_piece.get(room_norm, [])
        if len(exact) == 1:
            matches[room] = (exact[0], 1.0, "exact")
            continue
        if len(exact) > 1:
            unmatched.append(room)
            continue

        scored = sorted(
            ((_ratio(room, items[0].get("piece", "")), name, items) for name, items in by_piece.items()),
            key=lambda item: item[0], reverse=True,
        )
        if scored:
            best_score, _best_name, candidates = scored[0]
            tied = len(scored) > 1 and abs(scored[1][0] - best_score) < 1e-9
            if not tied and best_score >= FUZZY_ROOM_THRESHOLD and len(candidates) == 1:
                matches[room] = (candidates[0], round(best_score, 2), "fuzzy")
                continue

        contained_names = [
            name for name in by_piece
            if len(name) >= 4 and (_contains_words(room_norm, name) or _contains_words(name, room_norm))
        ]
        if contained_names:
            best_name = max(contained_names, key=len)
            candidates = by_piece[best_name]
            if len(candidates) == 1:
                matches[room] = (candidates[0], 0.75, "contenance")
                continue
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


def _expand_symbol_rows(symbols, rules: dict, excluded_relations: set[str] | None = None) -> list[dict]:
    components = {
        _norm(rule.get("article", "")): rule.get("items", [])
        for rule in rules.get("components", [])
        if rule.get("article")
    }
    rows = []
    excluded_relations = excluded_relations or set()
    for symbol in symbols:
        relation_key = object_relation_key(symbol.room, symbol.article)
        if relation_key in excluded_relations:
            continue
        items = components.get(_norm(symbol.article))
        if not items:
            rows.append({
                "room": symbol.room, "article": symbol.article, "categorie": symbol.categorie,
                "page": symbol.page, "page_type": symbol.page_type, "label": symbol.label,
                "source_room": symbol.room, "source_material": symbol.article,
                "mapping_key": relation_key, "origin": "pdf", "quantity": 1,
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
                    "source_room": symbol.room,
                    "source_material": symbol.article,
                    "mapping_key": relation_key,
                    "origin": "pdf",
                    "quantity": 1,
                })
    return rows


def _ensure_room_identity(frame: pd.DataFrame) -> pd.DataFrame:
    if "room_id" in frame.columns and "room_label" in frame.columns:
        return frame
    result = frame.copy()
    result["room_id"] = result.apply(
        lambda row: excel_room_id(
            row.get("niveau"), row.get("occupation"), row.get("piece"), row.get("numero")
        ), axis=1,
    )
    result["room_label"] = result.apply(
        lambda row: excel_room_label(
            row.get("niveau"), row.get("occupation"), row.get("piece"), row.get("numero")
        ), axis=1,
    )
    return result


def _excel_category_maps(scope: pd.DataFrame) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    by_room_material: dict[tuple[str, str], str] = {}
    by_material: dict[str, str] = {}
    usable = scope[(scope["materiel"] != "") & (scope["categorie"] != "")]
    for (room_id, material), group in usable.groupby(["room_id", "materiel"], sort=False):
        category = str(group["categorie"].mode().iat[0] if not group["categorie"].mode().empty else group["categorie"].iat[0])
        by_room_material[(str(room_id), str(material))] = category
    for material, group in usable.groupby("materiel", sort=False):
        category = str(group["categorie"].mode().iat[0] if not group["categorie"].mode().empty else group["categorie"].iat[0])
        by_material[str(material)] = category
    return by_room_material, by_material


def _new_room_id(room: str) -> str:
    token = _norm(room) or "sans-nom"
    return f"pdf-room-{hashlib.sha1(token.encode('utf-8')).hexdigest()[:16]}"


def _positive_quantity(value) -> int:
    try:
        return max(0, int(float(str(value or 0).replace(",", "."))))
    except (TypeError, ValueError):
        return 0


def _is_invalid_excel_material(value) -> bool:
    text = str(value or "").strip().upper()
    return text.startswith("#REF") or text in {"#N/A", "#VALUE!", "#NAME?", "#DIV/0!"}


def _join_unique(values, separator: str = " | ") -> str:
    unique: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text.lower() == "nan" or text in unique:
            continue
        unique.append(text)
    return separator.join(unique)


def _join_pages(values) -> str:
    pages: set[int] = set()
    for value in values:
        try:
            page = int(float(value))
        except (TypeError, ValueError):
            continue
        if page > 0:
            pages.add(page)
    return ",".join(map(str, sorted(pages)))


def _match_materials(pdf_articles: list[str], excel_materials: list[str], overrides: dict | None = None,
                     validated_articles: list[str] | None = None) -> tuple[dict, bool]:
    """Rapproche les articles du plan des matériels maquette.
    4 étages : cache manuel/persisté -> exact -> fuzzy -> LLM."""
    mapping: dict[str, tuple[str, str, float]] = {}
    cache = _load_map_cache()
    rules = _load_material_rules()
    norm_index = {_norm(m): m for m in excel_materials}
    overrides = overrides or {}
    validated = {_norm(article) for article in (validated_articles or []) if str(article).strip()}

    # Une non-correspondance n'est valable que pour le catalogue matériel dans
    # lequel elle a été recherchée. Un ``None`` appris dans un autre pôle ne
    # doit pas empêcher une correspondance disponible dans le scope courant.
    catalogue_identity = "|".join(sorted({_norm(item) for item in excel_materials if _norm(item)}))
    catalogue_hash = hashlib.sha1(catalogue_identity.encode("utf-8")).hexdigest()[:16]

    def scoped_cache_key(article: str) -> str:
        article_hash = hashlib.sha1(_norm(article).encode("utf-8")).hexdigest()[:16]
        return f"v2:{catalogue_hash}:{article_hash}"

    remaining = []
    for art in pdf_articles:
        if art in overrides:
            manual = str(overrides.get(art) or "").strip()
            if manual:
                canonical = norm_index.get(_norm(manual))
                if canonical:
                    mapping[art] = (canonical, "correspondance utilisateur", 1.0)
                    continue
                # Une ancienne valeur libre qui n'existe pas dans le niveau
                # Excel ne doit pas transformer un objet PDF en faux matériel
                # connu. On laisse alors les règles exactes/fuzzy reprendre la
                # main. Une valeur vide reste, elle, un refus explicite.
            else:
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
        cache_key = scoped_cache_key(art)
        if cache_key in cache:
            cached = cache[cache_key]
            if cached is None:
                continue                    # refus connu dans ce catalogue précis
            canonical = norm_index.get(_norm(cached))
            if canonical:
                mapping[art] = (canonical, "cache scope", 1.0)
                continue
        elif art in cache and cache[art] is not None:
            # Compatibilité : les anciennes correspondances positives restent
            # utilisables si leur cible existe dans le catalogue actif. Les
            # anciens ``None`` globaux sont volontairement ignorés.
            canonical = norm_index.get(_norm(cache[art]))
            if canonical:
                mapping[art] = (canonical, "cache historique", 1.0)
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
                canonical = norm_index.get(_norm(mat))
                if canonical:
                    mapping[art] = (canonical, "llm", 0.9)
            # mémoriser aussi les non-correspondances (null) pour ne pas re-payer
            for art in still:
                proposed = suggested.get(art)
                cache[scoped_cache_key(art)] = norm_index.get(_norm(proposed)) if proposed else None
            _save_map_cache(cache)
    return mapping, llm_used


def compare(excel_df: pd.DataFrame, pdf: PdfExtraction,
            room_overrides: dict | None = None, material_overrides: dict | None = None,
            niveau_excel: str | None = None, nom_niveau: str | None = None,
            validated_articles: list[str] | None = None,
            object_relations: dict | None = None,
            excluded_relations: list[str] | set[str] | None = None,
            manual_lines: list[dict] | None = None,
            excel_scope_id: str | None = None,
            scope_hint: str | None = None) -> CompareResult:
    display_level = (nom_niveau or "").strip() or (niveau_excel or "").strip() \
        or (f"R{pdf.niveau_hint}" if pdf.niveau_hint else "")
    res = CompareResult(remarks=list(pdf.remarks), niveau=display_level)
    object_relations = {
        str(key): value for key, value in (object_relations or {}).items()
        if str(key).strip() and isinstance(value, dict)
    }
    excluded = {str(key).strip() for key in (excluded_relations or []) if str(key).strip()}
    res.excluded_relations = excluded
    manual_lines = [item for item in (manual_lines or []) if isinstance(item, dict)]
    pdf_rooms = list(dict.fromkeys(
        str(symbol.room or "").strip() for symbol in pdf.symbols if str(symbol.room or "").strip()
    ))
    pdf_room_norms = {_norm(room) for room in pdf_rooms}
    pdf_relation_keys = {
        object_relation_key(symbol.room, symbol.article) for symbol in pdf.symbols
    }

    # 1. Restreindre d'abord au niveau puis au lot/pôle physique. Les salles et
    # matériaux d'un autre cabinet du même étage ne doivent jamais participer
    # au matching ni aux quantités avant.
    level_scope = _ensure_room_identity(excel_df)
    if niveau_excel:
        selected = level_scope[
            level_scope["niveau"].str.casefold() == niveau_excel.strip().casefold()
        ]
        if selected.empty:
            raise ValueError(f"Le niveau Excel sélectionné est introuvable : {niveau_excel}")
        level_scope = selected
    elif pdf.niveau_hint:
        hint = re.escape(str(pdf.niveau_hint))
        lvl = level_scope[
            level_scope["niveau"].str.contains(rf"(?:Niveau\s*)?{hint}", case=False, na=False)
        ]
        if not lvl.empty:
            level_scope = lvl
    level_scope = level_scope.copy()

    res.scope_options = excel_scope_options(level_scope)
    level_room_options = excel_room_options(level_scope)
    selected_scope = None
    selection_method = ""
    if str(excel_scope_id or "").strip():
        selected_scope = resolve_excel_scope(excel_scope_id, res.scope_options)
        if selected_scope is None:
            raise ValueError("Le pôle/lot Excel sélectionné est introuvable dans le niveau choisi")
        selection_method = "sélection utilisateur"
    else:
        relation_targets = [
            relation.get("target_room_id")
            for key, relation in object_relations.items()
            if key in pdf_relation_keys
            and isinstance(relation, dict)
            and relation.get("target_room_id")
        ]
        relation_targets.extend(
            target for source, target in (room_overrides or {}).items()
            if _norm(source) in pdf_room_norms and str(target or "").strip()
        )
        selected_scope, selection_method = infer_excel_scope(
            res.scope_options,
            level_room_options,
            relation_targets,
            [scope_hint or ""],
        )

    if selected_scope is not None:
        scope = filter_excel_scope(level_scope, selected_scope)
        res.excel_scope_id = str(selected_scope["id"])
        res.selected_scope = dict(selected_scope)
        res.scope_selection_method = selection_method
        res.remarks.append(f"Périmètre Excel : {selected_scope['label']} ({selection_method}).")
    else:
        # Sans pôle certain, aucun matériel du niveau ne participe au matching.
        # Il vaut mieux afficher les objets PDF « à valider » que prélever des
        # quantités dans un autre cabinet du même étage.
        scope = level_scope.iloc[0:0].copy() if len(res.scope_options) > 1 else level_scope
        if len(res.scope_options) > 1:
            res.remarks.append(
                "Périmètre Excel non déterminé : sélectionnez le pôle/lot avant de valider les correspondances."
            )
    scope = scope.copy()
    broken_materials = scope[scope["materiel"].map(_is_invalid_excel_material)]
    if not broken_materials.empty:
        res.remarks.append(
            "Excel : "
            f"{len(broken_materials)} ligne(s) contiennent une référence de formule invalide "
            f"(quantité totale {int(broken_materials['quantite'].sum())}). "
            "Elles restent visibles pour audit mais ne sont jamais proposées comme correspondance."
        )
    options = excel_room_options(scope)
    option_by_id = {option["id"]: option for option in options}

    # 2. Le périmètre automatique vient uniquement des objets réellement
    # présents. Les étiquettes de salles lues sur d'autres pages ne doivent pas
    # créer des centaines de lignes « non détecté ».
    room_map, unmatched = _match_rooms(pdf_rooms, options, room_overrides)
    res.room_matches = [(room, match[0]["piece"], match[1]) for room, match in room_map.items()]
    res.room_match_details = {
        room: {**match[0], "score": match[1], "method": match[2]}
        for room, match in room_map.items()
    }
    res.unmatched_rooms = unmatched

    # 3. Construire les objets après, y compris les lignes ajoutées depuis la
    # table Word. Les exclusions sont appliquées avant toute expansion.
    material_rules = _load_material_rules()
    raw_after = _expand_symbol_rows(pdf.symbols, material_rules, excluded)
    for index, item in enumerate(manual_lines, start=1):
        quantity = _positive_quantity(item.get("quantity_after", item.get("quantity", 1)))
        source_room = str(item.get("room") or item.get("source_room") or "").strip()
        source_material = str(item.get("material") or item.get("source_material") or "").strip()
        if quantity <= 0 or not source_material:
            continue
        mapping_key = str(item.get("mapping_key") or f"manual:{item.get('id') or index}").strip()
        raw_after.append({
            "room": source_room,
            "article": source_material,
            "categorie": str(item.get("category") or item.get("categorie") or "").strip(),
            "page": 0,
            "page_type": "SAISIE MANUELLE",
            "label": str(item.get("label") or "Saisie manuelle").strip(),
            "source_room": source_room,
            "source_material": source_material,
            "mapping_key": mapping_key,
            "origin": "manual",
            "quantity": quantity,
            "inline_relation": {
                "target_room_id": item.get("target_room_id", item.get("comparison_room", "")),
                "target_material": item.get("target_material", item.get("comparison_material", "")),
                "is_addition": bool(item.get("is_addition", False)),
            },
        })

    after_columns = [
        "room", "article", "categorie", "page", "page_type", "label",
        "source_room", "source_material", "mapping_key", "origin", "quantity", "inline_relation",
    ]
    apres = pd.DataFrame(raw_after)
    if apres.empty:
        apres = pd.DataFrame(columns=after_columns)
    for column in after_columns:
        if column not in apres.columns:
            apres[column] = None
    # Un symbole PDF sans pièce reste hors périmètre automatique. En revanche,
    # une ligne saisie manuellement doit pouvoir être exportée même si l'usager
    # ne connaît pas encore la pièce : les cellules correspondantes resteront
    # simplement vides.
    has_source_room = apres["source_room"].fillna("").astype(str).str.strip() != ""
    is_manual = apres["origin"].fillna("").astype(str).str.casefold() == "manual"
    apres = apres[has_source_room | is_manual]

    # 4. Rapprocher les articles globalement pour les lignes qui ne possèdent
    # pas encore une relation objet explicite.
    pdf_articles = sorted(str(item) for item in apres["article"].dropna().unique() if str(item).strip())
    excel_materials = sorted(
        m for m in scope["materiel"].unique() if m and not _is_invalid_excel_material(m)
    )
    mat_map, res.llm_used = _match_materials(pdf_articles, excel_materials, material_overrides, validated_articles)
    res.material_mapping = mat_map
    material_by_norm: dict[str, str] = {}
    for material in excel_materials:
        material_by_norm.setdefault(_norm(material), str(material))
    category_by_room_material, category_by_material = _excel_category_maps(scope)
    room_meta: dict[str, dict] = {option["id"]: dict(option) for option in options}
    mapped_rows: list[dict] = []
    explicitly_known_materials: set[str] = set()
    invalid_explicit_materials: set[str] = set()

    for _, row in apres.iterrows():
        source_room = str(row.get("source_room") or "").strip()
        source_material = str(row.get("source_material") or row.get("article") or "").strip()
        article = str(row.get("article") or source_material).strip()
        mapping_key = str(row.get("mapping_key") or object_relation_key(source_room, source_material)).strip()
        inline_relation = row.get("inline_relation") if isinstance(row.get("inline_relation"), dict) else None
        relation = inline_relation if inline_relation is not None else object_relations.get(mapping_key)
        relation_is_explicit = relation is not None

        if relation_is_explicit:
            option = resolve_excel_room(relation.get("target_room_id"), options)
        else:
            option = room_map.get(source_room, (None, 0.0, ""))[0]

        raw_target_material = str((relation or {}).get("target_material") or "").strip()
        canonical_target_material = material_by_norm.get(_norm(raw_target_material)) \
            if raw_target_material else None
        is_addition = bool((relation or {}).get("is_addition", False))
        if is_addition:
            # « Ajout sans équivalent » signifie bien que le libellé PDF est
            # conservé, même si le moteur fuzzy aurait trouvé un nom proche.
            target_material = article
            material_method = "validé sans équivalent Excel (1.0)"
        elif canonical_target_material:
            target_material = canonical_target_material
            material_method = "correspondance objet (1.0)"
            explicitly_known_materials.add(target_material)
        else:
            if raw_target_material:
                invalid_explicit_materials.add(raw_target_material)
            matched_material = mat_map.get(article)
            target_material = matched_material[0] if matched_material else article
            material_method = (
                f"{matched_material[1]} ({matched_material[2]})" if matched_material else "article PDF sans équivalent"
            )

        if option is not None:
            room_id = option["id"]
            room_meta[room_id] = dict(option)
        else:
            room_id = _new_room_id(source_room)
            new_room_label = f"{source_room} [nouvelle pièce]" if source_room else ""
            room_meta.setdefault(room_id, {
                "id": room_id,
                "label": new_room_label,
                "niveau": display_level,
                "occupation": "",
                "piece": new_room_label,
                "numero": "",
            })

        category = category_by_room_material.get((room_id, target_material)) \
            or category_by_material.get(target_material) \
            or str(row.get("categorie") or "").strip()
        mapped_rows.append({
            "room_id": room_id,
            "categorie": category,
            "materiel": target_material,
            "quantite_apres": _positive_quantity(row.get("quantity", 1)),
            "pages": row.get("page"),
            "labels": row.get("label"),
            "source_room": source_room,
            "source_material": source_material,
            "origin": str(row.get("origin") or "pdf"),
            "mapping_key": mapping_key,
            "mapping_method": material_method,
            "is_addition": is_addition,
        })

    # Exposer la relation effective pour la traçabilité symbole par symbole.
    for symbol in pdf.symbols:
        key = object_relation_key(symbol.room, symbol.article)
        if key in excluded:
            continue
        relation = object_relations.get(key)
        option = resolve_excel_room((relation or {}).get("target_room_id"), options) \
            if relation is not None else room_map.get(symbol.room, (None, 0.0, ""))[0]
        matched_material = mat_map.get(symbol.article)
        raw_target = str((relation or {}).get("target_material") or "").strip()
        is_addition = bool((relation or {}).get("is_addition", False))
        canonical_target = material_by_norm.get(_norm(raw_target)) if raw_target else None
        if is_addition:
            material = symbol.article
        elif canonical_target:
            material = canonical_target
        else:
            material = matched_material[0] if matched_material else symbol.article
        res.object_mapping[key] = {
            "room_id": option["id"] if option is not None else _new_room_id(symbol.room),
            "room": option,
            "material": material,
            "is_addition": is_addition,
        }

    if invalid_explicit_materials:
        invalid_list = ", ".join(sorted(invalid_explicit_materials, key=_norm))
        res.remarks.append(
            "Correspondance ignorée car absente du niveau Excel sélectionné : " + invalid_list
        )

    apres_mapped = pd.DataFrame(mapped_rows)
    merge_keys = ["room_id", "categorie", "materiel"]
    if apres_mapped.empty:
        apres_grp = pd.DataFrame(columns=merge_keys + [
            "quantite_apres", "pages", "labels", "source_room", "source_material",
            "origin", "mapping_key", "mapping_method", "is_addition",
        ])
    else:
        apres_grp = (
            apres_mapped.groupby(merge_keys, dropna=False, sort=False)
            .agg(
                quantite_apres=("quantite_apres", "sum"),
                pages=("pages", _join_pages),
                labels=("labels", lambda values: _join_unique(values, ", ")),
                source_room=("source_room", _join_unique),
                source_material=("source_material", _join_unique),
                origin=("origin", lambda values: _join_unique(values, " + ")),
                mapping_key=("mapping_key", _join_unique),
                mapping_method=("mapping_method", _join_unique),
                is_addition=("is_addition", "max"),
            )
            .reset_index()
        )

    # 5. Le « avant » ne contient que les instances physiques effectivement
    # ciblées par une relation de salle, d'objet ou une ligne manuelle.
    matched_room_ids = {
        row["room_id"] for row in mapped_rows if row["room_id"] in option_by_id
    }
    invalid_before_material = scope["materiel"].map(_is_invalid_excel_material).astype(bool)
    avant = scope[
        scope["room_id"].isin(matched_room_ids)
        & (scope["materiel"] != "")
        & ~invalid_before_material
    ].copy()
    if avant.empty:
        avant_grp = pd.DataFrame(columns=merge_keys + ["quantite_avant"])
    else:
        avant_grp = (
            avant.groupby(merge_keys, dropna=False, sort=False)
            .agg(quantite_avant=("quantite", "sum"))
            .reset_index()
        )

    # 6. Fusion par identité physique, catégorie et matériel.
    merged = avant_grp.merge(apres_grp, on=merge_keys, how="outer")
    merged["quantite_avant"] = merged["quantite_avant"].fillna(0).astype(int)
    merged["quantite_apres"] = merged["quantite_apres"].fillna(0).astype(int)
    merged["ecart"] = merged["quantite_apres"] - merged["quantite_avant"]

    known = {v[0] for v in mat_map.values()} | explicitly_known_materials

    def statut(row):
        if row["quantite_avant"] and not row["quantite_apres"]:
            return STATUT_NON_DETECTE
        if not row["quantite_avant"] and row["quantite_apres"]:
            return STATUT_AJOUT if bool(row.get("is_addition")) or row["materiel"] in known else STATUT_A_VALIDER
        return STATUT_INCHANGE if row["ecart"] == 0 else STATUT_MODIFIE

    merged["statut"] = merged.apply(statut, axis=1)

    for column in (
        "pages", "labels", "source_room", "source_material", "origin",
        "mapping_key", "mapping_method",
    ):
        if column not in merged.columns:
            merged[column] = ""
        merged[column] = merged[column].fillna("")
    if "is_addition" not in merged.columns:
        merged["is_addition"] = False
    merged["is_addition"] = merged["is_addition"].fillna(False).astype(bool)
    merged.loc[merged["origin"] == "", "origin"] = "excel"
    merged["rapprochement"] = merged["mapping_method"].where(
        merged["mapping_method"] != "", "maquette seule"
    )

    def meta_value(room_id: str, field: str) -> str:
        return str((room_meta.get(str(room_id)) or {}).get(field) or "")

    merged["niveau"] = merged["room_id"].map(lambda value: meta_value(value, "niveau"))
    merged["scope_id"] = merged["room_id"].map(lambda value: meta_value(value, "scope_id"))
    merged["occupation"] = merged["room_id"].map(lambda value: meta_value(value, "occupation"))
    merged["piece"] = merged["room_id"].map(lambda value: meta_value(value, "piece"))
    merged["numero"] = merged["room_id"].map(lambda value: meta_value(value, "numero"))
    merged["room_label"] = merged["room_id"].map(lambda value: meta_value(value, "label"))

    merged = merged.sort_values(
        ["niveau", "occupation", "piece", "categorie", "materiel"], kind="stable"
    ).reset_index(drop=True)
    res.table = merged
    return res
