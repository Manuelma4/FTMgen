# -*- coding: utf-8 -*-
"""Pipeline FTMgen : Excel maquette + PDF modificatif -> Excel comparatif.

Utilisation CLI :
    python -m app.pipeline "<listing.xlsx>" "<plan.pdf>" [-o sortie.xlsx]
"""
import argparse
import hashlib
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from . import config
from .core import compare as compare_mod
from .core import report
from .extract import excel_reader, pdf_reader
from .extract.pdf_reader import Room, Symbol


def run(excel_path: str, pdf_path: str, out_path: str | None = None,
        niveau_excel: str | None = None, nom_niveau: str | None = None,
        corrections: dict | None = None) -> dict:
    excel_path, pdf_path = str(excel_path), str(pdf_path)
    if out_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = str(config.OUTPUT_DIR / f"FTM_comparatif_{stamp}.xlsx")

    df = excel_reader.read_listing(excel_path)
    corrections = corrections or {}
    room_overrides, material_overrides = excel_reader.read_correspondences(excel_path)
    room_overrides = {**room_overrides, **_clean_mapping(corrections.get("room_mappings") or {}, keep_empty=True)}
    material_overrides = {**material_overrides, **_clean_mapping(corrections.get("material_mappings") or {}, keep_empty=True)}
    pdf = pdf_reader.extract_pdf(pdf_path)
    raw_catalog = json.loads((config.DATA_DIR / "symbol_catalog.json").read_text(encoding="utf-8"))
    article_meta = _article_meta(raw_catalog)
    article_refs = _article_refs(raw_catalog)
    _assign_detection_metadata(pdf, article_refs)
    initial_zones = _build_room_zones(pdf)
    effective_zones = _merge_room_zones(initial_zones, corrections.get("rooms", []))
    _apply_user_corrections(pdf, effective_zones, article_meta, corrections)
    result = compare_mod.compare(
        df, pdf, room_overrides, material_overrides,
        niveau_excel=niveau_excel, nom_niveau=nom_niveau,
        validated_articles=corrections.get("validated_articles") or [],
    )
    report.write_report(out_path, result, pdf, Path(excel_path).name, Path(pdf_path).name)

    table = result.table
    counts = table["statut"].value_counts().to_dict() if table is not None and not table.empty else {}
    comparison_rows = []
    if table is not None and not table.empty:
        # Passage par JSON pour convertir proprement les types pandas/numpy.
        comparison_rows = json.loads(table.to_json(orient="records", force_ascii=False))
    article_refs = {}
    for sections in raw_catalog.values():
        if not isinstance(sections, dict):
            continue
        for entry in list(sections.get("glyphs", {}).values()) + sections.get("text", []):
            article_refs[entry["article"]] = str(entry.get("reference", ""))
    page_markers = {}
    trace_rows = []
    room_to_piece = {plan: excel for plan, excel, _score in result.room_matches}
    table_status = {}
    if table is not None and not table.empty:
        table_status = {
            (str(row["piece"]), str(row["materiel"])): str(row["statut"])
            for _, row in table.iterrows()
        }
    for s in pdf.symbols:
        page_markers[s.page] = page_markers.get(s.page, 0) + 1
        piece = room_to_piece.get(s.room, f"{s.room} [nouvelle pièce]")
        material = result.material_mapping.get(s.article, (s.article,))[0]
        trace_rows.append({
            "marker": article_refs.get(s.article, "?"),
            "reference": article_refs.get(s.article, "?"),
            "detection_id": s.detection_id or f"p{s.page}-d{page_markers[s.page]}",
            "page": int(s.page), "page_type": s.page_type, "source": s.source,
            "label": s.label, "article": s.article, "categorie": s.categorie,
            "original_article": s.original_article or s.article,
            "original_reference": s.original_reference or article_refs.get(s.article, "?"),
            "confidence": round(float(s.confidence or _symbol_confidence(s)), 2),
            "room": s.room, "room_dist": round(float(s.room_dist), 1),
            "x": round(float(s.x), 1), "y": round(float(s.y), 1),
            "materiel_compare": material,
            "needs_review": _needs_review_status(table_status.get((piece, material), "")),
            "statut": table_status.get((piece, material), "COMPTÉ SUR LE PLAN"),
        })
    uncatalogued_rows = [
        {"page_type": page_type, "label": label, "occurrences": int(count)}
        for page_type, labels in (pdf.uncatalogued or {}).items()
        for label, count in labels.items()
    ]
    detected_by_type = {}
    for symbol in pdf.symbols:
        detected_by_type[(symbol.page_type, symbol.article)] = \
            detected_by_type.get((symbol.page_type, symbol.article), 0) + 1
    catalogue_rows = []
    for page_type, sections in raw_catalog.items():
        if page_type.startswith("_"):
            continue
        entries = list(sections.get("glyphs", {}).values()) + sections.get("text", [])
        seen = set()
        for entry in entries:
            article = entry["article"]
            if article in seen:
                continue
            seen.add(article)
            catalogue_rows.append({
                "page_type": page_type, "article": article,
                "categorie": entry["categorie"],
                "reference": str(entry.get("reference", "?")),
                "count": detected_by_type.get((page_type, article), 0),
            })
    excel_scope = _excel_scope(df, niveau_excel)
    return {
        "output": out_path,
        "niveau": result.niveau or None,
        "niveau_excel_selectionne": niveau_excel,
        "pages": {str(k): v for k, v in pdf.page_types.items()},
        "pieces_plan": [r.name for r in pdf.rooms],
        "pieces_zones": effective_zones,
        "corrections": _normalize_corrections(corrections),
        "referentiel_excel": {
            "pieces": sorted(str(item) for item in excel_scope["piece"].dropna().unique().tolist() if str(item).strip()),
            "materiels": sorted(str(item) for item in excel_scope["materiel"].dropna().unique().tolist() if str(item).strip()),
        },
        "pieces_rapprochees": [
            {"plan": a, "maquette": b, "score": s} for a, b, s in result.room_matches
        ],
        "pieces_non_rapprochees": result.unmatched_rooms,
        "articles_rapproches": [
            {"plan": article, "maquette": target, "methode": method, "score": score}
            for article, (target, method, score) in sorted(result.material_mapping.items())
        ],
        "objets_composes": _component_rules_for_pdf(pdf, raw_catalog),
        "symboles_detectes": len(pdf.symbols),
        "symboles_vision": sum(1 for s in pdf.symbols if s.source == "vision"),
        "vision_utilisee": pdf.cv_used,
        "llm_utilise": result.llm_used,
        "statuts": counts,
        "lignes": int(len(table)) if table is not None else 0,
        "remarques": result.remarks,
        "comparatif": comparison_rows,
        "traceabilite": trace_rows,
        "non_catalogues": [],
        "non_comptes": [],
        "catalogue_symboles": catalogue_rows,
        "audit_excel": {
            "lignes_materiel": int(len(df)),
            "pieces_uniques": int(df["piece"].nunique()),
            "quantite_totale": int(df["quantite"].sum()),
            "niveaux": sorted(df["niveau"].dropna().unique().tolist()),
            "codes_articles_renseignes": int((df["code_article"] != "").sum()),
            "correspondances_pieces": len(room_overrides),
            "correspondances_articles": len(material_overrides),
        },
    }


def _article_meta(raw_catalog: dict) -> dict:
    meta = {}
    for page_type, sections in raw_catalog.items():
        if page_type.startswith("_") or not isinstance(sections, dict):
            continue
        for entry in list(sections.get("glyphs", {}).values()) + sections.get("text", []):
            article = entry["article"]
            meta[(page_type, str(entry.get("reference", "")))] = {
                "article": article,
                "categorie": entry["categorie"],
                "reference": str(entry.get("reference", "")),
                "page_type": page_type,
            }
            meta[(page_type, article)] = meta[(page_type, str(entry.get("reference", "")))]
    return meta


def _article_refs(raw_catalog: dict) -> dict[str, str]:
    refs = {}
    for sections in raw_catalog.values():
        if not isinstance(sections, dict):
            continue
        for entry in list(sections.get("glyphs", {}).values()) + sections.get("text", []):
            refs[entry["article"]] = str(entry.get("reference", ""))
    return refs


def _clean_mapping(mapping: dict, keep_empty: bool = False) -> dict[str, str]:
    clean = {}
    for source, target in (mapping or {}).items():
        left, right = str(source or "").strip(), str(target or "").strip()
        if left and (right or keep_empty):
            clean[left] = right
    return clean


def _normalize_corrections(corrections: dict) -> dict:
    return {
        "rooms": corrections.get("rooms") or [],
        "manual_objects": corrections.get("manual_objects") or [],
        "edited_objects": corrections.get("edited_objects") or {},
        "room_mappings": _clean_mapping(corrections.get("room_mappings") or {}, keep_empty=True),
        "material_mappings": _clean_mapping(corrections.get("material_mappings") or {}, keep_empty=True),
        "validated_articles": [
            str(item).strip() for item in (corrections.get("validated_articles") or [])
            if str(item or "").strip()
        ],
    }


def _excel_scope(df, niveau_excel: str | None):
    if niveau_excel:
        selected = df[df["niveau"].str.casefold() == niveau_excel.strip().casefold()]
        if not selected.empty:
            return selected
    return df


def _component_rules_for_pdf(pdf: pdf_reader.PdfExtraction, raw_catalog: dict) -> list[dict]:
    detected = {_norm_token(symbol.article) for symbol in pdf.symbols}
    rules = compare_mod._load_material_rules()
    components = []
    for rule in rules.get("components", []):
        article = str(rule.get("article") or "").strip()
        if not article or _norm_token(article) not in detected:
            continue
        components.append({
            "article": article,
            "items": [
                {
                    "article": str(item.get("article") or "").strip(),
                    "categorie": str(item.get("categorie") or "").strip(),
                    "quantity": int(item.get("quantity") or 1),
                }
                for item in rule.get("items", [])
                if str(item.get("article") or "").strip()
            ],
        })
    return components


def _norm_token(value: str) -> str:
    value = unicodedata.normalize("NFD", str(value or ""))
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def _needs_review_status(status: str) -> bool:
    normalized = _norm_token(status)
    return normalized.startswith("a valider")


def _symbol_confidence(symbol: Symbol) -> float:
    if symbol.confidence:
        return float(symbol.confidence)
    if symbol.source in {"texte", "manuel"} or _norm_token(symbol.source) == "corrige":
        return 1.0
    match = re.search(r"\((0(?:\.\d+)?|1(?:\.0+)?)\)", str(symbol.label))
    if match:
        return float(match.group(1))
    return 0.75 if symbol.source == "vision" else 0.6


def _stable_detection_id(symbol: Symbol, article_refs: dict[str, str]) -> str:
    reference = article_refs.get(symbol.article, "")
    qx = round(float(symbol.x) / 2.0) * 2
    qy = round(float(symbol.y) / 2.0) * 2
    basis = "|".join([
        str(int(symbol.page)),
        str(symbol.page_type),
        str(reference),
        _norm_token(symbol.article),
        str(int(qx)),
        str(int(qy)),
    ])
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]
    return f"p{int(symbol.page)}-{digest}"


def _assign_detection_metadata(pdf: pdf_reader.PdfExtraction, article_refs: dict[str, str]) -> None:
    used: dict[str, int] = {}
    for symbol in pdf.symbols:
        base = symbol.detection_id or _stable_detection_id(symbol, article_refs)
        used[base] = used.get(base, 0) + 1
        symbol.detection_id = base if used[base] == 1 else f"{base}-{used[base]}"
        symbol.confidence = _symbol_confidence(symbol)
        symbol.original_article = symbol.original_article or symbol.article
        symbol.original_reference = symbol.original_reference or article_refs.get(symbol.article, "")


def _build_room_zones(pdf: pdf_reader.PdfExtraction) -> list[dict]:
    """Prépare des polygones modifiables. Ce sont des bornes de départ, pas une
    vérité géométrique : l'utilisateur peut les ajuster depuis la page."""
    if getattr(pdf, "room_zones", None):
        zones = []
        for zone in pdf.room_zones:
            points = zone.get("points") or []
            if len(points) < 3:
                continue
            x, y, w, h = _bounds(points)
            zones.append({
                "id": zone.get("id") or f"r{zone.get('page', 1)}-{len(zones) + 1}",
                "page": int(zone.get("page") or 1),
                "name_original": zone.get("name_original") or zone.get("name") or "",
                "name": zone.get("name") or zone.get("name_original") or "Pièce sans nom",
                "x": round(x, 1),
                "y": round(y, 1),
                "w": round(max(8.0, w), 1),
                "h": round(max(8.0, h), 1),
                "points": points,
                "locked": bool(zone.get("locked", False)),
                "source": zone.get("source") or "murs",
            })
        if zones:
            return sorted(zones, key=lambda z: (int(z["page"]), float(z["y"]), float(z["x"])))
    pages = [int(p) for p, t in pdf.page_types.items() if t in {"ELECTRICITE", "LUMINAIRE", "PLOMBERIE"}]
    if not pages:
        pages = sorted({int(s.page) for s in pdf.symbols})
    labels = list(pdf.rooms)
    zones = []
    for page in pages:
        for idx, room in enumerate(labels, start=1):
            pts = [(room.x, room.y)]
            pts += [(s.x, s.y) for s in pdf.symbols if s.room == room.name]
            xs = [float(x) for x, _y in pts]
            ys = [float(y) for _x, y in pts]
            pad_x, pad_y = 38.0, 30.0
            x0 = max(0.0, min(xs) - pad_x)
            y0 = max(0.0, min(ys) - pad_y)
            x1 = max(xs) + pad_x
            y1 = max(ys) + pad_y
            if x1 - x0 < 80:
                cx = (x0 + x1) / 2
                x0, x1 = max(0.0, cx - 40), cx + 40
            if y1 - y0 < 55:
                cy = (y0 + y1) / 2
                y0, y1 = max(0.0, cy - 28), cy + 28
            zones.append({
                "id": f"r{page}-{idx}",
                "page": int(page),
                "name_original": room.name,
                "name": room.name,
                "x": round(x0, 1),
                "y": round(y0, 1),
                "w": round(x1 - x0, 1),
                "h": round(y1 - y0, 1),
                "points": [
                    {"x": round(x0, 1), "y": round(y0, 1)},
                    {"x": round(x1, 1), "y": round(y0, 1)},
                    {"x": round(x1, 1), "y": round(y1, 1)},
                    {"x": round(x0, 1), "y": round(y1, 1)},
                ],
                "locked": False,
                "source": "proposition",
            })
    return zones


def _rect_points(x: float, y: float, w: float, h: float) -> list[dict]:
    return [
        {"x": round(x, 1), "y": round(y, 1)},
        {"x": round(x + w, 1), "y": round(y, 1)},
        {"x": round(x + w, 1), "y": round(y + h, 1)},
        {"x": round(x, 1), "y": round(y + h, 1)},
    ]


def _bounds(points: list[dict]) -> tuple[float, float, float, float]:
    xs = [float(p.get("x", 0)) for p in points]
    ys = [float(p.get("y", 0)) for p in points]
    x0, y0 = min(xs), min(ys)
    return x0, y0, max(xs) - x0, max(ys) - y0


def _merge_room_zones(initial: list[dict], submitted: list[dict]) -> list[dict]:
    by_id = {str(z.get("id")): dict(z) for z in initial}
    for item in submitted or []:
        zid = str(item.get("id") or "")
        base = by_id.get(zid, {})
        page = int(item.get("page") or base.get("page") or 1)
        name_original = str(item.get("name_original") or base.get("name_original") or item.get("name") or "")
        x = round(float(item.get("x", base.get("x", 0))), 1)
        y = round(float(item.get("y", base.get("y", 0))), 1)
        w = round(max(8.0, float(item.get("w", base.get("w", 80)))), 1)
        h = round(max(8.0, float(item.get("h", base.get("h", 55)))), 1)
        points = item.get("points") or base.get("points") or _rect_points(x, y, w, h)
        clean_points = [
            {"x": round(float(p.get("x", 0)), 1), "y": round(float(p.get("y", 0)), 1)}
            for p in points
            if isinstance(p, dict)
        ]
        if len(clean_points) < 3:
            clean_points = _rect_points(x, y, w, h)
        x, y, w, h = _bounds(clean_points)
        merged = {
            "id": zid or f"r{page}-{len(by_id) + 1}",
            "page": page,
            "name_original": name_original,
            "name": str(item.get("name") or name_original or "Pièce sans nom").strip(),
            "x": round(x, 1),
            "y": round(y, 1),
            "w": round(max(8.0, w), 1),
            "h": round(max(8.0, h), 1),
            "points": clean_points,
            "locked": bool(item.get("locked", False)),
            "source": "utilisateur" if item else base.get("source", "proposition"),
        }
        by_id[merged["id"]] = merged
    return sorted(by_id.values(), key=lambda z: (int(z["page"]), float(z["y"]), float(z["x"])))


def _zone_for_point(page: int, x: float, y: float, zones: list[dict]) -> tuple[str, float]:
    same_page = [z for z in zones if int(z.get("page", 0)) == int(page) and bool(z.get("locked"))]
    for z in same_page:
        if _point_in_polygon(x, y, z.get("points") or _rect_points(float(z["x"]), float(z["y"]), float(z["w"]), float(z["h"]))):
            return str(z.get("name") or ""), 0.0
    return "", 0.0


def _point_in_polygon(x: float, y: float, points: list[dict]) -> bool:
    inside = False
    clean = [(float(p.get("x", 0)), float(p.get("y", 0))) for p in points]
    j = len(clean) - 1
    for i, (xi, yi) in enumerate(clean):
        xj, yj = clean[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _apply_user_corrections(pdf: pdf_reader.PdfExtraction, zones: list[dict], article_meta: dict, corrections: dict) -> None:
    edited = corrections.get("edited_objects") or {}
    rename_by_original = {}
    for zone in zones:
        original = str(zone.get("name_original") or "").strip()
        name = str(zone.get("name") or "").strip()
        if original and name:
            rename_by_original[original] = name
    kept = []
    page_counts = {}
    for symbol in pdf.symbols:
        page_counts[symbol.page] = page_counts.get(symbol.page, 0) + 1
        legacy_id = f"p{symbol.page}-d{page_counts[symbol.page]}"
        detection_id = symbol.detection_id or legacy_id
        patch = edited.get(detection_id) or edited.get(legacy_id) or {}
        if patch.get("ignored"):
            continue
        if patch.get("reference") or patch.get("article"):
            meta = article_meta.get((symbol.page_type, str(patch.get("reference") or ""))) \
                or article_meta.get((symbol.page_type, str(patch.get("article") or "")))
            if meta:
                symbol.article = meta["article"]
                symbol.categorie = meta["categorie"]
                symbol.label = f"{symbol.label} (corrigé)"
                symbol.source = "corrigé"
        if patch.get("room"):
            symbol.room = str(patch["room"])
            symbol.room_dist = 0.0
        else:
            if symbol.room in rename_by_original:
                symbol.room = rename_by_original[symbol.room]
            room, dist = _zone_for_point(symbol.page, float(symbol.x), float(symbol.y), zones)
            if room:
                symbol.room, symbol.room_dist = room, dist
        kept.append(symbol)
    pdf.symbols = kept

    for item in corrections.get("manual_objects") or []:
        if item.get("ignored"):
            continue
        page = int(item.get("page") or 1)
        page_type = str(item.get("page_type") or pdf.page_types.get(page) or "")
        meta = article_meta.get((page_type, str(item.get("reference") or ""))) \
            or article_meta.get((page_type, str(item.get("article") or "")))
        if not meta:
            continue
        x, y = float(item.get("x", 0)), float(item.get("y", 0))
        room = str(item.get("room") or "")
        dist = 0.0
        if not room:
            room, dist = _zone_for_point(page, x, y, zones)
        if not room:
            room = _zone_name_at_point_for_display(page, x, y, zones)
        pdf.symbols.append(Symbol(
            str(item.get("label") or "Ajout manuel"),
            meta["article"], meta["categorie"], page, page_type,
            x, y, room, dist, "manuel",
            detection_id=str(item.get("id") or ""),
            confidence=1.0,
            original_article=meta["article"],
            original_reference=meta["reference"],
        ))

    corrected_rooms = []
    seen = set()
    for room in pdf.rooms:
        name = rename_by_original.get(room.name, room.name)
        if name and name not in seen:
            corrected_rooms.append(Room(name, room.surface, room.x, room.y, room.page))
            seen.add(name)
    for z in zones:
        name = str(z.get("name") or "").strip()
        if name and name not in seen:
            corrected_rooms.append(Room(
                name, "", float(z["x"]) + float(z["w"]) / 2,
                float(z["y"]) + float(z["h"]) / 2, int(z["page"]),
            ))
            seen.add(name)
    pdf.rooms = corrected_rooms


def _zone_name_at_point_for_display(page: int, x: float, y: float, zones: list[dict]) -> str:
    """Aide seulement pour les ajouts manuels sans salle explicite. Contrairement
    au recalcul automatique, cette fonction peut utiliser les propositions."""
    for z in zones:
        if int(z.get("page", 0)) != int(page):
            continue
        points = z.get("points") or _rect_points(float(z["x"]), float(z["y"]), float(z["w"]), float(z["h"]))
        if _point_in_polygon(x, y, points):
            return str(z.get("name") or "")
    return ""


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="FTMgen — comparatif maquette / plan modificatif")
    ap.add_argument("excel", help="Listing pièces + matériel (.xlsx)")
    ap.add_argument("pdf", help="Plan modificatif (.pdf)")
    ap.add_argument("-o", "--output", default=None, help="Fichier Excel de sortie")
    args = ap.parse_args()
    summary = run(args.excel, args.pdf, args.output)
    import json
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
