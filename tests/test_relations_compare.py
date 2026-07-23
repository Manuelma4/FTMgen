from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
from fastapi import HTTPException

from app.core.compare import STATUT_AJOUT, _match_materials, compare
from app.core.relations import (
    excel_room_id,
    excel_room_options,
    excel_scope_id,
    excel_scope_options,
    object_relation_key,
)
from app.core.word_report import all_pdf_relation_keys, materials_detected_in_pdf, normalize_ftm_document
from app.extract.pdf_reader import PdfExtraction, Symbol
from app.main import (
    _enrich_analysis_for_ui,
    _validate_corrections_document_scope,
    _validated_ftm_scope,
    api_generate_ftm_word,
    api_save_corrections,
)


def listing(**row) -> pd.DataFrame:
    defaults = {
        "niveau": "R+2",
        "occupation": "",
        "piece": "",
        "numero": "",
        "categorie": "Électricité",
        "code_article": "",
        "materiel": "Prise de courant",
        "quantite": 0,
    }
    return pd.DataFrame([{**defaults, **row}])


def extraction(room: str = "Secrétariat", article: str = "Prise de courant") -> PdfExtraction:
    return PdfExtraction(symbols=[
        Symbol("symbole", article, "Électricité", 5, "ELECTRICITE", 10, 10, room),
    ])


class PhysicalRoomComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frame = listing(
            occupation="ADMINISTRATION",
            piece="Secrétariat",
            numero="12",
            quantite=63,
        )
        self.frame = pd.concat([
            self.frame,
            listing(
                occupation="VASCULAIRE ANGIO",
                piece="Secrétariat",
                numero="28",
                quantite=22,
            ),
        ], ignore_index=True)
        self.target_id = excel_room_id("R+2", "VASCULAIRE ANGIO", "Secrétariat", "28")
        self.key = object_relation_key("Secrétariat", "Prise de courant")

    def test_ambiguous_room_is_not_aggregated(self) -> None:
        result = compare(self.frame, extraction(), niveau_excel="R+2")

        self.assertEqual(result.unmatched_rooms, ["Secrétariat"])
        self.assertEqual(int(result.table["quantite_avant"].sum()), 0)

    def test_object_relation_uses_only_selected_physical_room(self) -> None:
        result = compare(
            self.frame,
            extraction(),
            niveau_excel="R+2",
            object_relations={
                self.key: {
                    "target_room_id": self.target_id,
                    "target_material": "Prise de courant",
                    "is_addition": False,
                },
            },
        )

        row = result.table[result.table["materiel"] == "Prise de courant"].iloc[0]
        self.assertEqual(row["room_id"], self.target_id)
        self.assertEqual(int(row["quantite_avant"]), 22)
        self.assertEqual(int(row["quantite_apres"]), 1)

    def test_addition_keeps_pdf_name_instead_of_fuzzy_excel_name(self) -> None:
        result = compare(
            self.frame,
            extraction(article="Prise courant nouvelle"),
            niveau_excel="R+2",
            object_relations={
                object_relation_key("Secrétariat", "Prise courant nouvelle"): {
                    "target_room_id": self.target_id,
                    "target_material": "",
                    "is_addition": True,
                },
            },
        )

        added = result.table[result.table["quantite_apres"] > 0].iloc[0]
        self.assertEqual(added["materiel"], "Prise courant nouvelle")
        self.assertEqual(added["statut"], STATUT_AJOUT)

    def test_manual_line_without_room_is_still_exported(self) -> None:
        result = compare(
            self.frame,
            PdfExtraction(),
            niveau_excel="R+2",
            manual_lines=[{
                "id": "manual-1",
                "origin": "manual",
                "room": "",
                "material": "Objet libre",
                "quantity_after": "3",
                "is_addition": True,
            }],
        )

        self.assertEqual(len(result.table), 1)
        self.assertEqual(result.table.iloc[0]["piece"], "")
        self.assertEqual(int(result.table.iloc[0]["quantite_apres"]), 3)
        self.assertEqual(result.table.iloc[0]["statut"], STATUT_AJOUT)

    def test_object_remap_does_not_keep_source_room_baseline(self) -> None:
        frame = pd.concat([
            listing(
                occupation="VASCULAIRE ANGIO", piece="Source", numero="28",
                materiel="Prise de courant", quantite=7,
            ),
            listing(
                occupation="VASCULAIRE ANGIO", piece="Destination", numero="28",
                materiel="Prise de courant", quantite=3,
            ),
        ], ignore_index=True)
        destination_id = excel_room_id("R+2", "VASCULAIRE ANGIO", "Destination", "28")
        key = object_relation_key("Source", "Prise de courant")

        result = compare(
            frame,
            extraction(room="Source"),
            niveau_excel="R+2",
            object_relations={key: {
                "target_room_id": destination_id,
                "target_material": "Prise de courant",
            }},
        )

        self.assertEqual(int(result.table["quantite_avant"].sum()), 3)
        self.assertFalse((result.table["piece"] == "Source").any())

    def test_invalid_excel_formula_is_never_in_before_comparison(self) -> None:
        frame = pd.concat([
            listing(
                occupation="VASCULAIRE ANGIO", piece="Attente", numero="28",
                materiel="#REF!", quantite=10,
            ),
            listing(
                occupation="VASCULAIRE ANGIO", piece="Attente", numero="28",
                materiel="Prise de courant", quantite=2,
            ),
        ], ignore_index=True)

        result = compare(frame, extraction(room="Attente"), niveau_excel="R+2")

        self.assertEqual(int(result.table["quantite_avant"].sum()), 2)
        self.assertFalse(result.table["materiel"].astype(str).str.startswith("#REF").any())


class ExcelScopeComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frame = pd.concat([
            listing(
                occupation="ADMINISTRATION", piece="Attente 1", numero="12",
                materiel="Prise de courant", quantite=99,
            ),
            listing(
                occupation="VASCULAIRE ANGIO", piece="Attente 1", numero="28",
                materiel="Prise de courant", quantite=2,
            ),
            listing(
                occupation="VASCULAIRE ANGIO", piece="Consultation 1", numero="28",
                materiel="Prise RJ45", quantite=4,
            ),
        ], ignore_index=True)
        self.vascular_scope_id = excel_scope_id("R+2", "VASCULAIRE ANGIO", "28")

    def test_scope_options_have_stable_id_and_own_material_catalogue(self) -> None:
        options = excel_scope_options(self.frame)
        vascular = next(item for item in options if item["id"] == self.vascular_scope_id)

        self.assertEqual(len(options), 2)
        self.assertEqual(vascular["numero"], "28")
        self.assertEqual(vascular["piece_count"], 2)
        self.assertEqual(vascular["materiels"], ["Prise de courant", "Prise RJ45"])

    def test_room_options_expose_its_own_before_inventory(self) -> None:
        options = excel_room_options(self.frame)
        consultation = next(item for item in options if item["piece"] == "Consultation 1")

        self.assertEqual(consultation["materiels"], ["Prise RJ45"])
        self.assertEqual(len(consultation["materials"]), 1)
        self.assertEqual(consultation["materials"][0]["name"], "Prise RJ45")
        self.assertEqual(consultation["materials"][0]["quantity"], 4)

    def test_pdf_filename_hint_limits_matching_and_quantities_to_vascular_scope(self) -> None:
        result = compare(
            self.frame,
            extraction(room="Attente 1"),
            niveau_excel="R+2",
            scope_hint="24-031 CABINET VASCULAIRE @ind N 16.06.2026.pdf",
        )

        self.assertEqual(result.excel_scope_id, self.vascular_scope_id)
        self.assertEqual(result.selected_scope["occupation"], "VASCULAIRE ANGIO")
        self.assertEqual(result.scope_selection_method, "nom du PDF")
        prise = result.table[result.table["materiel"] == "Prise de courant"].iloc[0]
        self.assertEqual(int(prise["quantite_avant"]), 2)
        self.assertEqual(int(prise["quantite_apres"]), 1)
        self.assertFalse((result.table["quantite_avant"] == 99).any())

    def test_ambiguous_scope_never_uses_the_whole_level_as_fallback(self) -> None:
        result = compare(self.frame, extraction(room="Attente 1"), niveau_excel="R+2")

        self.assertEqual(result.excel_scope_id, "")
        self.assertEqual(result.material_mapping, {})
        self.assertEqual(int(result.table["quantite_avant"].sum()), 0)
        self.assertFalse((result.table["occupation"] == "ADMINISTRATION").any())
        self.assertFalse((result.table["occupation"] == "VASCULAIRE ANGIO").any())

    def test_only_scope_in_level_is_selected_automatically(self) -> None:
        vascular_only = self.frame[self.frame["occupation"] == "VASCULAIRE ANGIO"]
        result = compare(vascular_only, extraction(room="Attente 1"), niveau_excel="R+2")

        self.assertEqual(result.excel_scope_id, self.vascular_scope_id)
        self.assertEqual(result.scope_selection_method, "seul pôle du niveau")
        self.assertEqual(int(result.table["quantite_avant"].sum()), 2)

    def test_stale_room_and_object_relations_cannot_infer_scope(self) -> None:
        stale_target = excel_room_id("R+2", "VASCULAIRE ANGIO", "Attente 1", "28")
        result = compare(
            self.frame,
            extraction(room="Attente 1"),
            niveau_excel="R+2",
            room_overrides={"Salle absente du PDF": stale_target},
            object_relations={
                object_relation_key("Salle absente du PDF", "Prise de courant"): {
                    "target_room_id": stale_target,
                    "target_material": "Prise de courant",
                },
            },
        )

        self.assertEqual(result.excel_scope_id, "")
        self.assertEqual(int(result.table["quantite_avant"].sum()), 0)


class MaterialCacheScopeTests(unittest.TestCase):
    def test_legacy_global_negative_cache_does_not_block_current_scope(self) -> None:
        article = "Objet ZXQ sans équivalent historique"
        target = "Équipement vasculaire dédié"
        storage = {article: None}

        with patch("app.core.compare._load_map_cache", return_value=storage), \
                patch("app.core.compare._save_map_cache"), \
                patch("app.core.compare.llm.suggest_material_mapping", return_value={article: target}) as suggest:
            mapping, llm_used = _match_materials([article], [target])

        suggest.assert_called_once()
        self.assertTrue(llm_used)
        self.assertEqual(mapping[article][0], target)

    def test_negative_cache_isolated_by_material_catalogue(self) -> None:
        article = "Objet ZXQ 991 sans rapprochement"
        first_catalogue = ["Matériel alpha indépendant"]
        second_catalogue = ["Matériel bêta vasculaire"]
        storage = {}

        def save_cache(value):
            snapshot = dict(value)
            storage.clear()
            storage.update(snapshot)

        with patch("app.core.compare._load_map_cache", side_effect=lambda: storage), \
                patch("app.core.compare._save_map_cache", side_effect=save_cache), \
                patch(
                    "app.core.compare.llm.suggest_material_mapping",
                    side_effect=[{}, {article: second_catalogue[0]}],
                ) as suggest:
            first, _ = _match_materials([article], first_catalogue)
            second, _ = _match_materials([article], second_catalogue)

        self.assertEqual(first, {})
        self.assertEqual(second[article][0], second_catalogue[0])
        self.assertEqual(suggest.call_count, 2)
        self.assertTrue(any(key.startswith("v2:") and value is None for key, value in storage.items()))
        self.assertTrue(any(value == second_catalogue[0] for value in storage.values()))


class ScopeApiGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scope_a = excel_scope_id("R+2", "VASCULAIRE ANGIO", "28")
        self.scope_b = excel_scope_id("R+2", "ADMINISTRATION", "12")
        self.analysis = {
            "audit_excel": {"scope_selectionne": self.scope_a},
            "referentiel_excel": {
                "scope_options": [
                    {"id": self.scope_a, "label": "Vasculaire"},
                    {"id": self.scope_b, "label": "Administration"},
                ],
            },
        }

    def test_ftm_rejects_scope_different_from_calculated_comparison(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            _validated_ftm_scope(self.analysis, {"excel_scope_id": self.scope_b})

        self.assertEqual(raised.exception.status_code, 409)

    def test_ftm_rejects_analysis_without_calculated_scope(self) -> None:
        self.analysis["audit_excel"]["scope_selectionne"] = ""
        with self.assertRaises(HTTPException) as raised:
            _validated_ftm_scope(self.analysis, {"excel_scope_id": self.scope_a})

        self.assertEqual(raised.exception.status_code, 409)

    def test_ftm_accepts_only_scope_recorded_in_audit(self) -> None:
        self.assertEqual(
            _validated_ftm_scope(self.analysis, {"excel_scope_id": self.scope_a}),
            self.scope_a,
        )

    def test_corrections_reject_scope_divergence_with_word_form(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            _validate_corrections_document_scope({
                "excel_scope_id": self.scope_a,
                "ftm_document": {"excel_scope_id": self.scope_b},
            })

        self.assertEqual(raised.exception.status_code, 409)


class FtmEndpointRecalculationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ftm_save_recalculates_excel_before_generating_word(self) -> None:
        scope_id = excel_scope_id("R+2", "VASCULAIRE ANGIO", "28")
        analysis = {
            "audit_excel": {"scope_selectionne": scope_id},
            "corrections": {"excel_scope_id": scope_id, "rooms": ["saved"]},
            "referentiel_excel": {"scope_options": [{"id": scope_id}]},
        }
        analysis_path = Mock()
        analysis_path.read_text.return_value = json.dumps(analysis)
        delegated: dict = {}
        expected_response = object()

        async def fake_threadpool(function, *args, **kwargs):
            delegated["function"] = function
            delegated["args"] = args
            delegated["kwargs"] = kwargs
            return expected_response

        document = {
            "excel_scope_id": scope_id,
            "materials": [{
                "mapping_key": object_relation_key("Vasculaire 01", "Prise RJ45"),
                "origin": "pdf",
                "room": "Vasculaire 01",
                "material": "Prise RJ45",
                "comparison_room": excel_room_id(
                    "R+2", "VASCULAIRE ANGIO", "Consultation 1", "28"
                ),
                "comparison_material": "Prise RJ45",
                "quantity_after": "3",
            }],
        }
        with patch("app.main._analysis_file", return_value=analysis_path), \
                patch("app.main._enrich_analysis_for_ui"), \
                patch("app.main._validated_ftm_scope", return_value=scope_id), \
                patch("app.main.run_in_threadpool", side_effect=fake_threadpool):
            response = await api_generate_ftm_word("testjob", document)

        self.assertIs(response, expected_response)
        self.assertIs(delegated["function"], api_save_corrections)
        self.assertEqual(delegated["args"][0], "testjob")
        recalculation = delegated["args"][1]
        self.assertEqual(recalculation["excel_scope_id"], scope_id)
        self.assertEqual(recalculation["rooms"], ["saved"])
        self.assertEqual(recalculation["ftm_document"]["materials"], document["materials"])

class HistoryEnrichmentSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scope_a = excel_scope_id("R+2", "VASCULAIRE ANGIO", "28")
        self.scope_b = excel_scope_id("R+2", "ADMINISTRATION", "12")
        self.frame = pd.concat([
            listing(occupation="VASCULAIRE ANGIO", piece="Attente", numero="28", quantite=2),
            listing(occupation="ADMINISTRATION", piece="Bureau", numero="12", quantite=9),
        ], ignore_index=True)

    def test_enrich_replaces_stale_word_scope_with_validated_comparison_scope(self) -> None:
        data = {
            "niveau_excel_selectionne": "R+2",
            "audit_excel": {"scope_selectionne": self.scope_a},
            "excel_scope_selectionne": self.scope_b,
            "corrections": {"excel_scope_id": self.scope_b},
            "ftm_document": {"excel_scope_id": self.scope_b},
            "traceabilite": [],
            "articles_rapproches": [{}],
            "objets_composes": [{}],
        }
        with patch("app.main.excel_reader.read_listing", return_value=self.frame), \
                patch("app.main._job_excel", return_value=Path("listing.xlsx")), \
                patch("app.main._job_pdf", return_value=Path("CABINET VASCULAIRE.pdf")):
            _enrich_analysis_for_ui("testjob", data)

        self.assertEqual(data["ftm_document"]["excel_scope_id"], self.scope_a)
        self.assertEqual(data["corrections"]["excel_scope_id"], self.scope_a)
        self.assertEqual(data["referentiel_excel"]["selected_scope_id"], self.scope_a)
        self.assertEqual(data["referentiel_excel"]["materiels"], ["Prise de courant"])

    def test_enrich_failure_drops_legacy_global_catalogue(self) -> None:
        safe_piece = {
            "id": "room-a", "scope_id": self.scope_a, "piece": "Attente", "label": "Attente",
        }
        data = {
            "corrections": {"excel_scope_id": self.scope_a},
            "ftm_document": {"excel_scope_id": self.scope_b},
            "referentiel_excel": {
                "pieces": ["Pièce globale dangereuse"],
                "materiels": ["Matériel global dangereux"],
                "piece_options": [safe_piece, {"id": "legacy-without-scope"}],
                "scope_options": [{"id": self.scope_a, "label": "Vasculaire"}],
                "selected_scope_id": self.scope_a,
            },
            "articles_rapproches": [{}],
            "objets_composes": [{}],
        }
        with patch("app.main.excel_reader.read_listing", side_effect=OSError("Excel indisponible")), \
                patch("app.main._job_excel", return_value=Path("missing.xlsx")):
            _enrich_analysis_for_ui("testjob", data)

        referential = data["referentiel_excel"]
        self.assertEqual(referential["pieces"], [])
        self.assertEqual(referential["materiels"], [])
        self.assertEqual(referential["piece_options"], [safe_piece])
        self.assertEqual(data["ftm_document"]["excel_scope_id"], self.scope_a)

    def test_enrich_failure_drops_piece_options_without_safe_scope_catalogue(self) -> None:
        data = {
            "corrections": {"excel_scope_id": self.scope_a},
            "referentiel_excel": {
                "pieces": ["Global"],
                "materiels": ["Global"],
                "piece_options": [{"id": "room-a", "scope_id": self.scope_a}],
                "scope_options": [],
            },
            "articles_rapproches": [{}],
            "objets_composes": [{}],
        }
        with patch("app.main.excel_reader.read_listing", side_effect=OSError("Excel indisponible")), \
                patch("app.main._job_excel", return_value=Path("missing.xlsx")):
            _enrich_analysis_for_ui("testjob", data)

        self.assertEqual(data["referentiel_excel"]["piece_options"], [])
        self.assertEqual(data["referentiel_excel"]["selected_scope_id"], "")


class WordRelationPersistenceTests(unittest.TestCase):
    def test_scope_id_is_persisted_in_word_form_data(self) -> None:
        scope_id = excel_scope_id("R+2", "VASCULAIRE ANGIO", "28")
        document = normalize_ftm_document({"excel_scope_id": scope_id, "materials": []})

        self.assertEqual(document["excel_scope_id"], scope_id)

    def test_ignored_relation_remains_known_and_can_be_restored(self) -> None:
        key = object_relation_key("Attente", "Prise RJ45")
        analysis = {
            "traceabilite": [{
                "room": "Attente",
                "article": "Prise RJ45",
                "categorie": "Électricité",
                "materiel_compare": "Prise RJ45",
                "ignored": True,
            }],
            "comparatif": [],
            "pieces_rapprochees": [],
        }
        restored = {
            "materials_version": 3,
            "materials": [{
                "id": "pdf-1",
                "mapping_key": key,
                "origin": "pdf",
                "room": "Attente",
                "material": "Prise RJ45",
                "comparison_room": "",
                "comparison_material": "",
                "quantity_after": "1",
            }],
        }

        self.assertEqual(all_pdf_relation_keys(analysis), [key])
        self.assertEqual(materials_detected_in_pdf(analysis, {"materials_version": 3, "materials": []}), [])
        self.assertEqual(len(materials_detected_in_pdf(analysis, restored)), 1)


if __name__ == "__main__":
    unittest.main()
