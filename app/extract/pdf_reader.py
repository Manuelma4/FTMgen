# -*- coding: utf-8 -*-
"""Extraction du plan PDF (vectoriel) :
- classification des pages (AMENAGEMENT / CLOISONNEMENT / ELECTRICITE / LUMINAIRE / PLOMBERIE)
- détection des pièces via les étiquettes « XX.XX m² » + nom, avec coordonnées
- symboles TEXTE : libellés reconnus par catalogue regex
- symboles GRAPHIQUES : template matching OpenCV, templates auto-extraits de la
  légende de la page (voir pdf_cv.py) — ex. smiley = PC 10/16A 2P+T
- symboles composés déployés (ex. POSTE = 4 PC + 1 RJ, règle « expands »)
- rattachement pièce : distance géodésique (murs bloquants) quand la vision est
  disponible, sinon plus proche étiquette.

Aucun OCR : le PDF est vectoriel, tout est extrait avec coordonnées, donc
chaque quantité du rapport est traçable (page + position).
"""
import json
import re
import unicodedata
from dataclasses import dataclass, field

import fitz

from .. import config
from .pdf_cv import CV_AVAILABLE, PageCV

M2_RE = re.compile(r"^[\d\s.,]+m²$")
# fragments de finitions/annotations à ne jamais prendre pour un nom de pièce
_NOT_A_NAME = (
    "sol souple", "carrelage", "terrazzo", "faience", "faux plafond", "demontable",
    "peinture", "lasure", "stylobate", "partage", "privatifs", "communs", "prive",
    "hsfxp", "plafond", "mur", "sol", "lot ",
)
PAGE_TYPES = ("ELECTRICITE", "LUMINAIRE", "PLOMBERIE", "CLOISONNEMENT", "AMENAGEMENT")


@dataclass
class Span:
    text: str
    size: float
    x: float
    y: float
    page: int  # 1-based


@dataclass
class Room:
    name: str
    surface: str
    x: float
    y: float
    page: int


@dataclass
class Symbol:
    label: str          # texte brut ou nom du glyphe détecté
    article: str        # nom canonique (catalogue / légende)
    categorie: str
    page: int
    page_type: str
    x: float
    y: float
    room: str = ""
    room_dist: float = 0.0
    source: str = "texte"   # texte | vision | expansion
    detection_id: str = ""
    confidence: float = 0.0
    original_article: str = ""
    original_reference: str = ""


@dataclass
class PdfExtraction:
    page_types: dict = field(default_factory=dict)
    rooms: list = field(default_factory=list)
    room_zones: list = field(default_factory=list)
    symbols: list = field(default_factory=list)
    remarks: list = field(default_factory=list)
    niveau_hint: str = ""
    uncatalogued: dict = field(default_factory=dict)
    uncatalogued_spans: list = field(default_factory=list)
    cv_used: bool = False


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip().lower()


def _load_catalog() -> dict:
    with open(config.DATA_DIR / "symbol_catalog.json", encoding="utf-8") as f:
        raw = json.load(f)
    catalog = {}
    for page_type, sections in raw.items():
        if page_type.startswith("_"):
            continue
        text_entries = [
            {
                "re": re.compile(e["pattern"], re.IGNORECASE),
                "article": e["article"],
                "categorie": e["categorie"],
                "max_font_size": e.get("max_font_size"),
                "min_font_size": e.get("min_font_size"),
                "min_x": e.get("min_x"),
                "max_x": e.get("max_x"),
                "min_y": e.get("min_y"),
                "max_y": e.get("max_y"),
                "expands": e.get("expands"),
            }
            for e in sections.get("text", [])
        ]
        glyphs = {
            key: (
                g["article"], g["categorie"], g.get("threshold"),
                g.get("template_side", "left"), g.get("template_pick", "rightmost"),
                g.get("detector"),
            )
            for key, g in sections.get("glyphs", {}).items()
        }
        catalog[page_type] = {"text": text_entries, "glyphs": glyphs}
    return catalog


def _page_spans(page, pno: int) -> list[Span]:
    spans = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for s in line["spans"]:
                t = s["text"].strip()
                if not t:
                    continue
                x0, y0, x1, y1 = s["bbox"]
                spans.append(Span(t, round(s["size"], 1), (x0 + x1) / 2, (y0 + y1) / 2, pno + 1))
    return spans


def _classify_page(spans: list[Span]) -> str:
    titles = " ".join(_norm(s.text) for s in spans if s.size >= 17)
    for pt in PAGE_TYPES:
        if _norm(pt.replace("_", " ")) in titles or pt.lower() in titles:
            return pt
    if "plan d'amenagement" in titles or "amenagement" in titles:
        return "AMENAGEMENT"
    return "AUTRE"


def _looks_like_finish(text: str) -> bool:
    t = _norm(text)
    return any(k in t for k in _NOT_A_NAME)


def _extract_rooms(spans: list[Span]) -> list[Room]:
    """Deux styles d'étiquette pièce observés sur les plans :
    - cartouche maquette : nom AU-DESSUS du 'XX m²' (ligne la plus haute du
      cartouche ; l'occupant, ex. ANESTHESISTE, est entre le nom et le m²)
    - étiquette projet  : nom À DROITE du 'XX m²', même taille, même ligne."""
    rooms = []
    for sp in spans:
        if not M2_RE.match(sp.text):
            continue
        name_span, best_key = None, None
        for other in spans:
            if other is sp or abs(other.size - sp.size) > 0.3:
                continue
            if M2_RE.match(other.text) or _looks_like_finish(other.text):
                continue
            dx, dy = other.x - sp.x, other.y - sp.y
            if 0 < dx <= 40 and abs(dy) <= 3:
                key = (0, dx)              # style étiquette projet (prioritaire)
            elif abs(dx) <= 6 and -35 <= dy <= -5:
                key = (1, 35 + dy)         # style cartouche : ligne la plus haute
            else:
                continue
            if best_key is None or key < best_key:
                best_key, name_span = key, other
        if name_span:
            rooms.append(Room(name_span.text.strip(), sp.text, sp.x, sp.y, sp.page))
    seen, unique = set(), []
    for r in rooms:
        key = _norm(r.name)
        if key and key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _nearest_room(x: float, y: float, rooms: list[Room]) -> tuple[str, float]:
    best, best_d = "", float("inf")
    for r in rooms:
        d = ((r.x - x) ** 2 + (r.y - y) ** 2) ** 0.5
        if d < best_d:
            best, best_d = r.name, d
    return best, round(best_d, 1)


def _expand(symbols: list[Symbol], entry: dict, sym: Symbol) -> None:
    """Déploie un symbole composé (ex. POSTE = 4 PC + 1 RJ)."""
    for article, count in entry["expands"].items():
        for _ in range(int(count)):
            symbols.append(Symbol(
                f"{sym.label} (déployé)", article, sym.categorie, sym.page,
                sym.page_type, sym.x, sym.y, sym.room, sym.room_dist, "expansion",
                confidence=sym.confidence,
            ))


def _entry_allows_span(entry: dict, span: Span) -> bool:
    if entry["max_font_size"] is not None and span.size > entry["max_font_size"]:
        return False
    if entry["min_font_size"] is not None and span.size < entry["min_font_size"]:
        return False
    if entry["min_x"] is not None and span.x < entry["min_x"]:
        return False
    if entry["max_x"] is not None and span.x > entry["max_x"]:
        return False
    if entry["min_y"] is not None and span.y < entry["min_y"]:
        return False
    if entry["max_y"] is not None and span.y > entry["max_y"]:
        return False
    return True


def _symbol_quality(symbol: Symbol) -> float:
    """Quality used only to collapse duplicate text/vision hits."""
    if symbol.source == "texte":
        return 2.0
    match = re.search(r"\((0(?:\.\d+)?|1(?:\.0+)?)\)", symbol.label)
    if match:
        return float(match.group(1))
    return 1.0 if symbol.source == "vision" else 0.8


def _dedupe_symbols(symbols: list[Symbol]) -> list[Symbol]:
    """Remove duplicate hits of the same catalog article at the same location.

    Luminaires may be exposed both as vector text and as colored glyphs. Keeping
    both would double-count, so we keep the strongest source within a small
    radius while preserving the original order for unrelated detections.
    """
    kept: list[tuple[int, Symbol]] = []
    for index, symbol in sorted(enumerate(symbols), key=lambda item: _symbol_quality(item[1]), reverse=True):
        radius = 8.0 if symbol.page_type == "LUMINAIRE" else 5.0
        duplicate = False
        for _, existing in kept:
            if existing.page != symbol.page or existing.page_type != symbol.page_type:
                continue
            if existing.article != symbol.article:
                continue
            if (existing.x - symbol.x) ** 2 + (existing.y - symbol.y) ** 2 <= radius ** 2:
                duplicate = True
                break
        if not duplicate:
            kept.append((index, symbol))
    return [symbol for _, symbol in sorted(kept, key=lambda item: item[0])]


def extract_pdf(path: str) -> PdfExtraction:
    catalog = _load_catalog()
    result = PdfExtraction()
    doc = fitz.open(path)
    try:
        all_spans = {pno: _page_spans(doc[pno], pno) for pno in range(doc.page_count)}

        for pno, spans in all_spans.items():
            result.page_types[pno + 1] = _classify_page(spans)

        # indice de niveau (ex: "Circulation 1 - R2" -> "2")
        for spans in all_spans.values():
            for sp in spans:
                m = re.search(r"\bR(\d)\b", sp.text)
                if m:
                    result.niveau_hint = m.group(1)
                    break
            if result.niveau_hint:
                break

        rooms_by_page = {pno: _extract_rooms(spans) for pno, spans in all_spans.items()}
        ref_page = max(rooms_by_page, key=lambda p: len(rooms_by_page[p]))
        result.rooms = rooms_by_page[ref_page]
        rooms_for = {
            pno: (rooms if len(rooms) >= 3 else result.rooms)
            for pno, rooms in rooms_by_page.items()
        }

        for pno, spans in all_spans.items():
            page_type = result.page_types[pno + 1]
            entries = catalog.get(page_type)
            if not entries:
                continue
            rooms = rooms_for[pno]
            glyphs = entries.get("glyphs") or {}

            # contexte vision : rendu + carte géodésique (si OpenCV disponible)
            pagecv = None
            if glyphs and CV_AVAILABLE and rooms:
                try:
                    pagecv = PageCV(doc[pno], rooms)
                    for room_name, points in pagecv.room_polygons_pt().items():
                        if len(points) >= 3:
                            result.room_zones.append({
                                "id": f"r{pno + 1}-{len(result.room_zones) + 1}",
                                "page": pno + 1,
                                "name_original": room_name,
                                "name": room_name,
                                "points": points,
                                "locked": False,
                                "source": "murs",
                            })
                except Exception:
                    pagecv = None

            def assign(x, y):
                if pagecv is not None:
                    room, dist = pagecv.assign_room_pt(x, y)
                    if room:
                        return room, dist
                return _nearest_room(x, y, rooms)

            # --- symboles texte ---
            matched_texts = set()
            for sp in spans:
                for e in entries["text"]:
                    if not _entry_allows_span(e, sp):
                        continue
                    m = e["re"].match(sp.text)
                    if not m:
                        continue
                    article = e["article"]
                    if "{0}" in article:
                        article = article.format(m.group(1))
                    room, dist = assign(sp.x, sp.y)
                    sym = Symbol(sp.text, article, e["categorie"], pno + 1,
                                 page_type, sp.x, sp.y, room, dist, confidence=1.0)
                    if e["expands"]:
                        _expand(result.symbols, e, sym)
                    else:
                        result.symbols.append(sym)
                    matched_texts.add(sp.text)
                    break

            # --- symboles graphiques (vision) ---
            if pagecv is not None:
                # les spots (disques verts) ont leur détecteur dédié : le
                # template matching ne sait pas séparer fixe / orientable.
                template_glyphs = {k: g for k, g in glyphs.items() if not g[5]}
                spot_plain = next((g[:2] for g in glyphs.values() if g[5] == "green_disc"), None)
                spot_arms = next((g[:2] for g in glyphs.values() if g[5] == "green_disc_arms"), None)
                triangle_ref = next((g[:2] for g in glyphs.values() if g[5] == "orange_triangle"), None)
                templates, legend_box = pagecv.extract_templates(template_glyphs, _norm)
                detections = []
                if templates:
                    if page_type == "PLOMBERIE" and "Évacuation EU Ø40" in templates:
                        eu_template = {"Évacuation EU Ø40": templates["Évacuation EU Ø40"]}
                        detections = pagecv.detect(eu_template, legend_box)
                        companions = {k: v for k, v in templates.items() if k != "Évacuation EU Ø40"}
                        detections += pagecv.detect_plumbing_companions(companions, detections)
                    else:
                        detections = pagecv.detect(templates, legend_box)
                if spot_plain or spot_arms:
                    detections += pagecv.detect_spots(spot_plain, spot_arms, legend_box)
                if triangle_ref:
                    detections += pagecv.detect_triangles(triangle_ref, legend_box)
                if detections:
                    result.cv_used = True
                for article, categorie, x_pt, y_pt, score in detections:
                    room, dist = assign(x_pt, y_pt)
                    result.symbols.append(Symbol(
                        f"glyphe ({score})", article, categorie, pno + 1,
                        page_type, x_pt, y_pt, room, dist, "vision",
                        confidence=float(score),
                    ))

            # --- libellés non catalogués (pour enrichir le catalogue) ---
            unc = result.uncatalogued.setdefault(page_type, {})
            for sp in spans:
                if sp.text in matched_texts or sp.size >= 13 or len(sp.text) < 2:
                    continue
                if re.fullmatch(r"[\d.,:x×\s%°/m²-]+", sp.text, re.IGNORECASE):
                    continue
                unc[sp.text] = unc.get(sp.text, 0) + 1
                room, dist = assign(sp.x, sp.y)
                result.uncatalogued_spans.append({
                    "page": pno + 1, "page_type": page_type, "label": sp.text,
                    "x": round(sp.x, 1), "y": round(sp.y, 1), "room": room,
                    "room_dist": round(float(dist), 1),
                })

            # --- annotations importantes ---
            for sp in spans:
                if 14.5 <= sp.size <= 21 and len(sp.text) > 20:
                    remark = f"p.{pno + 1} ({page_type}) : {sp.text}"
                    if remark not in result.remarks:
                        result.remarks.append(remark)
    finally:
        doc.close()

    result.symbols = _dedupe_symbols(result.symbols)
    return result
