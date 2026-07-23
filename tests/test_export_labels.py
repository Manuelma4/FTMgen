from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd
from docx import Document
from openpyxl import load_workbook

from app.core.compare import CompareResult, STATUT_MODIFIE
from app.core.report import write_report
from app.core.word_report import write_ftm_document
from app.extract.pdf_reader import PdfExtraction


class ExportLabelTests(unittest.TestCase):
    def test_excel_uses_market_and_after_ftm_headers(self) -> None:
        table = pd.DataFrame([{
            "niveau": "R+2",
            "occupation": "VASCULAIRE ANGIO",
            "piece": "Consultation 1",
            "numero": "28",
            "room_id": "room-1",
            "source_room": "Vasculaire 01",
            "source_material": "PC 10/16A 2P+T",
            "origin": "pdf",
            "categorie": "Électricité",
            "materiel": "Prise de courant",
            "quantite_avant": 8,
            "quantite_apres": 6,
            "ecart": -2,
            "statut": STATUT_MODIFIE,
            "pages": "5",
            "labels": "glyphe",
            "rapprochement": "correspondance objet",
        }])
        result = CompareResult(table=table, niveau="R+2")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "comparatif.xlsx"
            write_report(str(path), result, PdfExtraction(), "marché.xlsx", "ftm.pdf")
            workbook = load_workbook(path, read_only=True, data_only=True)
            try:
                headers = [cell.value for cell in next(workbook["Comparatif"].iter_rows())]
            finally:
                workbook.close()

        self.assertIn("Quantité marché", headers)
        self.assertIn("Quantité après FTM", headers)
        self.assertNotIn("Qté avant (maquette)", headers)
        self.assertNotIn("Qté après (plan)", headers)

    def test_word_uses_market_and_after_ftm_headers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ftm.docx"
            write_ftm_document(path, {
                "materials": [{
                    "id": "row-1",
                    "room": "Vasculaire 01",
                    "material": "PC 10/16A 2P+T",
                    "quantity_before": "8",
                    "quantity_after": "6",
                }],
            })
            document = Document(path)

        rows = [
            [" ".join(cell.text.split()) for cell in row.cells]
            for table in document.tables
            for row in table.rows
        ]
        material_header = next(row for row in rows if "Nom de la pièce" in row and "Matériel" in row)
        self.assertIn("Quantité marché", material_header)
        self.assertIn("Quantité après FTM", material_header)


if __name__ == "__main__":
    unittest.main()
