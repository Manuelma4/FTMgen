# -*- coding: utf-8 -*-
"""Génération du classeur Excel comparatif (marché / après FTM)."""
from datetime import datetime

import xlsxwriter

from .compare import (
    CompareResult, STATUT_AJOUT, STATUT_MODIFIE, STATUT_INCHANGE,
    STATUT_NON_DETECTE, STATUT_A_VALIDER,
)
from .relations import object_relation_key
from ..extract.pdf_reader import PdfExtraction

HEADERS = [
    "Niveau", "Occupation", "Pièce Excel", "N°", "ID pièce Excel",
    "Pièce source PDF", "Objet source PDF", "Origine", "Catégorie", "Matériel comparé",
    "Quantité marché", "Quantité après FTM", "Écart", "Statut",
    "Pages PDF", "Libellés plan", "Rapprochement",
]

STATUT_COLORS = {
    STATUT_AJOUT: "#C6EFCE",          # vert
    STATUT_MODIFIE: "#FFEB9C",        # jaune
    STATUT_INCHANGE: "#FFFFFF",
    STATUT_NON_DETECTE: "#FFC7CE",    # rouge clair
    STATUT_A_VALIDER: "#D9E1F2",      # bleu clair
}


def write_report(path: str, result: CompareResult, pdf: PdfExtraction,
                 excel_name: str, pdf_name: str) -> None:
    wb = xlsxwriter.Workbook(path)
    f_title = wb.add_format({"bold": True, "font_size": 14})
    f_head = wb.add_format({"bold": True, "bg_color": "#305496", "font_color": "white",
                            "border": 1, "text_wrap": True, "valign": "vcenter"})
    f_cell = wb.add_format({"border": 1})
    f_num = wb.add_format({"border": 1, "align": "center"})
    f_ecart_pos = wb.add_format({"border": 1, "align": "center", "bold": True, "font_color": "#006100"})
    f_ecart_neg = wb.add_format({"border": 1, "align": "center", "bold": True, "font_color": "#9C0006"})
    f_label = wb.add_format({"bold": True})
    statut_formats = {
        s: wb.add_format({"border": 1, "bg_color": c}) for s, c in STATUT_COLORS.items()
    }

    df = result.table

    # ---- Synthèse ----
    ws = wb.add_worksheet("Synthèse")
    ws.set_column("A:A", 52)
    ws.set_column("B:B", 30)
    ws.write(0, 0, "FTMgen — Comparatif marché / après FTM", f_title)
    rows = [
        ("Généré le", datetime.now().strftime("%d/%m/%Y %H:%M")),
        ("Marché (Excel)", excel_name),
        ("Après FTM (plan PDF)", pdf_name),
        ("Niveau sélectionné", result.niveau if result.niveau else "non détecté"),
        ("Pôle / lot Excel", (result.selected_scope or {}).get("label") or "non sélectionné"),
        ("ID périmètre Excel", result.excel_scope_id or "—"),
        ("Pièces reconnues sur le plan", len(result.room_matches) + len(result.unmatched_rooms)),
        ("Pièces rapprochées avec la maquette", len(result.room_matches)),
        ("Pièces non rapprochées", ", ".join(result.unmatched_rooms) or "—"),
        ("Rapprochement des noms par LLM", "oui" if result.llm_used else "non (fuzzy local)"),
        ("", ""),
    ]
    counts = df["statut"].value_counts().to_dict() if df is not None and not df.empty else {}
    for s in (STATUT_AJOUT, STATUT_MODIFIE, STATUT_INCHANGE, STATUT_NON_DETECTE, STATUT_A_VALIDER):
        rows.append((f"Lignes « {s} »", counts.get(s, 0)))
    r = 2
    for k, v in rows:
        ws.write(r, 0, k, f_label)
        ws.write(r, 1, v)
        r += 1
    if result.remarks:
        r += 1
        ws.write(r, 0, "Remarques relevées sur le plan :", f_label)
        for remark in result.remarks:
            r += 1
            ws.write(r, 0, remark)

    # ---- Comparatif (+ feuilles filtrées) ----
    def write_table(name, data):
        w = wb.add_worksheet(name[:31])
        widths = [12, 24, 25, 10, 24, 25, 36, 12, 16, 38, 12, 12, 8, 34, 10, 34, 24]
        for c, wd in enumerate(widths):
            w.set_column(c, c, wd)
        for c, h in enumerate(HEADERS):
            w.write(0, c, h, f_head)
        for i, (_, row) in enumerate(data.iterrows(), start=1):
            f_statut = statut_formats.get(row["statut"], f_cell)
            w.write(i, 0, row.get("niveau", ""), f_cell)
            w.write(i, 1, row.get("occupation", ""), f_cell)
            w.write(i, 2, row.get("piece", ""), f_cell)
            w.write(i, 3, str(row.get("numero", "") or ""), f_num)
            w.write(i, 4, str(row.get("room_id", "") or ""), f_cell)
            w.write(i, 5, str(row.get("source_room", "") or ""), f_cell)
            w.write(i, 6, str(row.get("source_material", "") or ""), f_cell)
            w.write(i, 7, str(row.get("origin", "") or ""), f_cell)
            w.write(i, 8, row["categorie"], f_cell)
            w.write(i, 9, row["materiel"], f_cell)
            w.write(i, 10, int(row["quantite_avant"]), f_num)
            w.write(i, 11, int(row["quantite_apres"]), f_num)
            ecart = int(row["ecart"])
            w.write(i, 12, ecart, f_ecart_pos if ecart >= 0 else f_ecart_neg)
            w.write(i, 13, row["statut"], f_statut)
            w.write(i, 14, str(row.get("pages", "") or ""), f_num)
            w.write(i, 15, str(row.get("labels", "") or ""), f_cell)
            w.write(i, 16, str(row.get("rapprochement", "") or ""), f_cell)
        w.autofilter(0, 0, max(len(data), 1), len(HEADERS) - 1)
        w.freeze_panes(1, 0)

    if df is not None and not df.empty:
        write_table("Comparatif", df)
        changes = df[df["statut"] != STATUT_INCHANGE]
        write_table("Écarts uniquement", changes)
        write_table("À valider", df[df["statut"].isin([STATUT_A_VALIDER, STATUT_NON_DETECTE])])

    # ---- Traçabilité symboles ----
    ws = wb.add_worksheet("Traçabilité plan")
    trace_headers = [
        "Page", "Type de plan", "Source", "Libellé", "Article", "Catégorie",
        "Pièce rattachée", "Distance (pt)", "X", "Y", "Clé relation",
        "ID pièce Excel", "Matériel comparé", "État relation",
    ]
    for c, h in enumerate(trace_headers):
        ws.write(0, c, h, f_head)
    ws.set_column(0, 2, 14)
    ws.set_column(3, 6, 30)
    for i, s in enumerate(pdf.symbols, start=1):
        mapping_key = object_relation_key(s.room, s.article)
        effective = result.object_mapping.get(mapping_key) or {}
        ignored = mapping_key in result.excluded_relations
        ws.write_row(i, 0, [s.page, s.page_type, s.source, s.label, s.article, s.categorie,
                            s.room, s.room_dist, round(s.x), round(s.y), mapping_key,
                            effective.get("room_id", ""), effective.get("material", ""),
                            "Exclu" if ignored else "Compté"], f_cell)
    ws.autofilter(0, 0, max(len(pdf.symbols), 1), len(trace_headers) - 1)
    ws.freeze_panes(1, 0)

    # ---- Libellés non catalogués (pour enrichir le catalogue) ----
    ws = wb.add_worksheet("Libellés non catalogués")
    ws.set_column(0, 0, 16)
    ws.set_column(1, 1, 46)
    for c, h in enumerate(["Type de plan", "Libellé", "Occurrences"]):
        ws.write(0, c, h, f_head)
    r = 1
    for page_type, labels in result_uncatalogued(pdf).items():
        for label, n in sorted(labels.items(), key=lambda kv: -kv[1]):
            ws.write_row(r, 0, [page_type, label, n], f_cell)
            r += 1

    wb.close()


def result_uncatalogued(pdf: PdfExtraction) -> dict:
    return pdf.uncatalogued or {}
