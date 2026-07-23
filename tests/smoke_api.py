"""Smoke test manuel du flux API complet avec deux fichiers réels.

Usage :
    python tests/smoke_api.py --excel chemin.xlsx --pdf chemin.pdf --level R+2

Le traitement créé pour la vérification est supprimé dans un bloc ``finally``.
"""
from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app


def normalized(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return " ".join("".join(char if char.isalnum() else " " for char in text.lower()).split())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--excel", required=True)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--level", default="R+2")
    args = parser.parse_args()

    excel_path = Path(args.excel)
    pdf_path = Path(args.pdf)
    client = TestClient(app)
    job = ""
    try:
        with excel_path.open("rb") as excel_file, pdf_path.open("rb") as pdf_file:
            response = client.post(
                "/api/compare",
                files={
                    "excel": (excel_path.name, excel_file, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                    "pdf": (pdf_path.name, pdf_file, "application/pdf"),
                },
                data={"niveau_excel": args.level, "nom_niveau": args.level},
            )
        response.raise_for_status()
        analysis = response.json()
        job = analysis["job"]

        options = analysis["referentiel_excel"]["piece_options"]
        scope_options = analysis["referentiel_excel"]["scope_options"]
        target = next(
            item for item in options
            if normalized(item.get("occupation")) == "vasculaire angio"
            and normalized(item.get("piece")) == "consultation 1"
            and normalized(item.get("numero")) == "28"
        )
        before_inventory = {
            normalized(item.get("name")): int(item.get("quantity") or 0)
            for item in target.get("materials") or []
        }
        assert before_inventory["prise de courant"] == 8
        assert before_inventory["prise rj45"] == 1
        scope_id = target["scope_id"]
        selected_scope = analysis["referentiel_excel"]["selected_scope"]
        assert len(scope_options) == 17, len(scope_options)
        assert analysis["excel_scope_selectionne"] == scope_id
        assert selected_scope["id"] == scope_id
        assert normalized(selected_scope["occupation"]) == "vasculaire angio"
        assert normalized(selected_scope["numero"]) == "28"
        assert len(analysis["referentiel_excel"]["pieces"]) == 10
        assert len(analysis["referentiel_excel"]["materiels"]) == 16
        assert analysis["audit_excel"]["pieces_scope_selectionne"] == 10
        trace = next(
            item for item in analysis["traceabilite"]
            if normalized(item.get("room")) == "vasculaire 01"
            and normalized(item.get("article")) == "pc 10 16a 2p t"
        )

        document = {
            "materials_version": 3,
            "excel_scope_id": scope_id,
            "project_name": "Smoke test",
            "materials": [
                {
                    "id": "pdf-smoke",
                    "mapping_key": trace["mapping_key"],
                    "origin": "pdf",
                    "room": trace["room"],
                    "lot": "",
                    "sous_lot": "",
                    "material": trace["article"],
                    "category": trace["categorie"],
                    "comparison_room": target["id"],
                    "comparison_material": "Prise de courant",
                    "is_addition": False,
                    "quantity_before": "",
                    "quantity_after": "1",
                    "unit_price": "",
                },
                {
                    "id": "manual-smoke",
                    "mapping_key": "manual:smoke",
                    "origin": "manual",
                    "room": "Local test",
                    "lot": "",
                    "sous_lot": "",
                    "material": "Objet ajouté manuellement",
                    "category": "Test",
                    "comparison_room": "",
                    "comparison_material": "",
                    "is_addition": True,
                    "quantity_before": "0",
                    "quantity_after": "2",
                    "unit_price": "",
                },
            ],
        }
        # La route Word historique doit elle aussi recalculer le comparatif :
        # sinon la relation est sauvegardÃ©e mais la quantitÃ© Avant reste Ã  0.
        response = client.post(f"/api/history/{job}/ftm", json=document)
        response.raise_for_status()
        recalculated = response.json()

        selected_rows = [
            row for row in recalculated["comparatif"]
            if row.get("room_id") == target["id"] and row.get("materiel") == "Prise de courant"
        ]
        assert selected_rows and selected_rows[0]["quantite_avant"] == 8, selected_rows
        foreign_rows = [
            row for row in recalculated["comparatif"]
            if row.get("occupation")
            and normalized(row.get("occupation")) != "vasculaire angio"
        ]
        assert not foreign_rows, foreign_rows
        assert not any(
            str(row.get("materiel") or "").strip().upper().startswith("#REF")
            for row in recalculated["comparatif"]
        )
        manual_rows = [row for row in recalculated["comparatif"] if row.get("origin") == "manual"]
        assert manual_rows and manual_rows[0]["quantite_apres"] == 2, manual_rows
        assert len(recalculated["referentiel_excel"]["piece_options"]) == 44
        assert not any(str(item).upper().startswith("#REF") for item in recalculated["referentiel_excel"]["materiels"])

        response = client.get(f"/api/history/{job}")
        response.raise_for_status()
        saved = response.json()
        assert saved["corrections"]["excel_scope_id"] == scope_id
        assert saved["ftm_document"]["excel_scope_id"] == scope_id
        relation = saved["corrections"]["object_relations"][trace["mapping_key"]]
        assert relation["target_room_id"] == target["id"]
        assert len(saved["corrections"]["manual_lines"]) == 1
        excluded_count = len(saved["corrections"]["excluded_relations"])
        assert excluded_count > 0

        assert client.get(saved["download"]).status_code == 200
        assert client.get(saved["word_download"]).status_code == 200

        response = client.post(f"/api/history/{job}/ftm", json=saved["ftm_document"])
        response.raise_for_status()
        saved_again = client.get(f"/api/history/{job}").json()
        assert len(saved_again["corrections"]["excluded_relations"]) == excluded_count

        print({
            "job": job,
            "piece_options": len(options),
            "scope_options": len(scope_options),
            "selected_scope": selected_scope["label"],
            "scope_rooms": len(analysis["referentiel_excel"]["pieces"]),
            "scope_materials": len(analysis["referentiel_excel"]["materiels"]),
            "consultation_1_before": selected_rows[0]["quantite_avant"],
            "manual_after": manual_rows[0]["quantite_apres"],
            "excluded_relations_preserved": excluded_count,
            "downloads": "ok",
        })
    finally:
        if job:
            client.delete(f"/api/history/{job}")


if __name__ == "__main__":
    main()
