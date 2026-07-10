# -*- coding: utf-8 -*-
"""Lecture du listing maquette « Pièces + Matériel » (export type MEDIVIE).

Structure attendue : une ligne d'en-tête contenant « Occupation », puis une
ligne par matériel ; les colonnes pièce/numéro/niveau ne sont renseignées que
sur la première ligne de chaque pièce (cellules fusionnées) -> forward-fill.
"""
import openpyxl
import pandas as pd
import re
import unicodedata

COLUMNS = ["occupation", "piece", "numero", "niveau", "categorie", "code_article", "materiel", "quantite"]
HEADER_FIRST_CELL = "occupation"

HEADER_ALIASES = {
    "occupation": "occupation",
    "nom de la piece": "piece", "piece": "piece",
    "numero": "numero", "no": "numero",
    "niveau": "niveau",
    "categorie": "categorie",
    "code article": "code_article", "code": "code_article",
    "materiel": "materiel", "article": "materiel",
    "quantite": "quantite", "qte": "quantite",
}


def _norm(value) -> str:
    value = unicodedata.normalize("NFD", str(value or ""))
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def read_listing(path: str) -> pd.DataFrame:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        ws = _find_sheet(wb)
        rows = [r for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()

    header_idx = None
    for i, row in enumerate(rows):
        if row and str(row[0] or "").strip().lower() == HEADER_FIRST_CELL:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(
            "Feuille « Pièces + Matériel » introuvable : aucune ligne d'en-tête "
            "commençant par 'Occupation'."
        )

    header = rows[header_idx]
    positions = {}
    for index, label in enumerate(header):
        canonical = HEADER_ALIASES.get(_norm(label))
        if canonical and canonical not in positions:
            positions[canonical] = index
    required = {"occupation", "piece", "numero", "niveau", "categorie", "materiel", "quantite"}
    missing = required - set(positions)
    if missing:
        raise ValueError(f"Colonnes obligatoires absentes de l'Excel : {sorted(missing)}")

    data = []
    for row in rows[header_idx + 1:]:
        data.append({column: row[positions[column]] if column in positions and positions[column] < len(row) else None
                     for column in COLUMNS})
    df = pd.DataFrame(data, columns=COLUMNS).dropna(how="all")

    # cellules fusionnées : la pièce n'apparaît que sur sa première ligne
    for col in ("occupation", "piece", "numero", "niveau"):
        df[col] = df[col].ffill()

    df = df.dropna(subset=["materiel"])
    for col in ("occupation", "piece", "niveau", "categorie", "code_article", "materiel"):
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["numero"] = df["numero"].apply(lambda v: str(v).strip() if v is not None else "")
    df["quantite"] = pd.to_numeric(df["quantite"], errors="coerce").fillna(0).astype(int)
    df = df[df["materiel"] != ""]
    # lignes placeholder de l'export maquette, ex. "(aucun équipement rattaché)" :
    # on garde la pièce (elle existe dans la maquette) mais sans matériel
    placeholder = df["materiel"].str.match(r"^\(.*\)$")
    df.loc[placeholder, "materiel"] = ""
    df.loc[placeholder, "quantite"] = 0
    return df.reset_index(drop=True)


def read_correspondences(path: str) -> tuple[dict, dict]:
    """Lit les feuilles optionnelles de correspondance pieces et articles."""
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    room_map, material_map = {}, {}
    try:
        for sheet_name in workbook.sheetnames:
            normalized = _norm(sheet_name)
            if "correspondance" not in normalized:
                continue
            sheet = workbook[sheet_name]
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                continue
            headers = [_norm(value) for value in rows[0]]
            if "piece plan pdf" in headers and "piece existante excel" in headers:
                left, right = headers.index("piece plan pdf"), headers.index("piece existante excel")
                for row in rows[1:]:
                    if len(row) > max(left, right) and row[left] and row[right]:
                        room_map[str(row[left]).strip()] = str(row[right]).strip()
            if "article plan pdf" in headers and "materiel existant excel" in headers:
                left, right = headers.index("article plan pdf"), headers.index("materiel existant excel")
                for row in rows[1:]:
                    if len(row) > max(left, right) and row[left] and row[right]:
                        material_map[str(row[left]).strip()] = str(row[right]).strip()
    finally:
        workbook.close()
    return room_map, material_map


def _find_sheet(wb):
    for name in wb.sheetnames:
        if "pièce" in name.lower() or "piece" in name.lower():
            return wb[name]
    return wb[wb.sheetnames[0]]
