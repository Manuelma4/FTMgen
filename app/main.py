# -*- coding: utf-8 -*-
"""API FTMgen — FastAPI.

Lancement :  .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8060
"""
import uuid
import json
import re
import unicodedata
from io import BytesIO
from datetime import datetime
from pathlib import Path

import fitz
import xlsxwriter
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, pipeline
from .core import word_report
from .extract import excel_reader
from .services import analysis_store

app = FastAPI(title="FTMgen", description="Comparatif maquette / plan de travaux modificatifs")

WEB_DIR = config.BASE_DIR / "web"
FRONTEND_DIST = config.BASE_DIR / "frontend" / "dist"

if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")


@app.get("/")
def index():
    if (FRONTEND_DIST / "index.html").exists():
        return FileResponse(FRONTEND_DIST / "index.html")
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def api_health():
    return {"status": "ok"}


@app.post("/api/excel/inspect")
async def api_excel_inspect(excel: UploadFile = File(...)):
    if not excel.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Le fichier doit être un Excel (.xlsx)")
    try:
        frame = await run_in_threadpool(excel_reader.read_listing, BytesIO(await excel.read()))
    except Exception as exc:
        raise HTTPException(422, f"Excel invalide : {exc}") from exc
    levels = []
    for level, group in frame.groupby("niveau", sort=False):
        levels.append({
            "value": str(level), "pieces": int(group["piece"].nunique()),
            "lignes": int(len(group)), "quantite": int(group["quantite"].sum()),
        })
    return {"niveaux": levels}


@app.post("/api/compare")
async def api_compare(
    excel: UploadFile = File(...), pdf: UploadFile = File(...),
    niveau_excel: str = Form(""), nom_niveau: str = Form(""),
):
    if not excel.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Le premier fichier doit être un Excel (.xlsx)")
    if not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Le second fichier doit être un PDF")

    job = uuid.uuid4().hex[:12]
    xlsx_path = config.UPLOAD_DIR / f"{job}_{Path(excel.filename).name}"
    pdf_path = config.UPLOAD_DIR / f"{job}_{Path(pdf.filename).name}"
    xlsx_path.write_bytes(await excel.read())
    pdf_path.write_bytes(await pdf.read())

    out_path = config.OUTPUT_DIR / f"FTM_comparatif_{job}.xlsx"
    try:
        # pipeline.run est synchrone et peut durer plusieurs minutes (LLM) :
        # l'exécuter dans le threadpool évite de bloquer tout le serveur.
        summary = await run_in_threadpool(
            pipeline.run,
            str(xlsx_path), str(pdf_path), str(out_path),
            niveau_excel=niveau_excel or None, nom_niveau=nom_niveau or None,
        )
    except Exception as exc:  # renvoyer l'erreur lisible côté UI
        raise HTTPException(422, f"Échec du traitement : {exc}") from exc

    summary["download"] = f"/api/download/{out_path.name}"
    summary["job"] = job
    summary["job_status"] = "done"
    summary["created_at"] = datetime.now().isoformat(timespec="seconds")
    summary["excel_name"] = Path(excel.filename).name
    summary["pdf_name"] = Path(pdf.filename).name
    summary["pdf_original"] = f"/api/jobs/{job}/pdf"
    (config.OUTPUT_DIR / f"analysis_{job}.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )
    return JSONResponse(summary)


def _analysis_file(job: str) -> Path:
    if not job.isalnum():
        raise HTTPException(400, "Identifiant d'analyse invalide")
    path = config.OUTPUT_DIR / f"analysis_{job}.json"
    if not path.exists():
        raise HTTPException(404, "Analyse introuvable")
    return path


@app.get("/api/history")
def api_history():
    return {"analyses": analysis_store.list_analyses()}


@app.get("/api/history/{job}")
def api_history_detail(job: str):
    data = json.loads(_analysis_file(job).read_text(encoding="utf-8"))
    data["job"] = job
    data["pdf_original"] = f"/api/jobs/{job}/pdf"
    output = Path(str(data.get("output", "")))
    if output.name:
        data["download"] = f"/api/download/{output.name}"
    word_output = Path(str(data.get("word_output", "")))
    if word_output.name and (config.OUTPUT_DIR / word_output.name).exists():
        data["word_download"] = f"/api/download/{word_output.name}"
    _enrich_analysis_for_ui(job, data)
    return JSONResponse(data)


@app.delete("/api/history/{job}")
def api_history_delete(job: str):
    try:
        removed = analysis_store.delete_analysis(job)
    except analysis_store.InvalidJobId as exc:
        raise HTTPException(400, str(exc)) from exc
    except analysis_store.AnalysisNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"deleted": job, "files": removed}


@app.get("/api/download/{name}")
def api_download(name: str):
    path = (config.OUTPUT_DIR / name).resolve()
    if path.parent != config.OUTPUT_DIR.resolve() or not path.exists():
        raise HTTPException(404, "Fichier introuvable")
    media_types = {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pdf": "application/pdf",
    }
    return FileResponse(path, media_type=media_types.get(path.suffix.lower(), "application/octet-stream"), filename=name)


def _job_pdf(job: str) -> Path:
    if not job.isalnum():
        raise HTTPException(400, "Identifiant de traitement invalide")
    matches = list(config.UPLOAD_DIR.glob(f"{job}_*.pdf"))
    if len(matches) != 1:
        raise HTTPException(404, "PDF du traitement introuvable")
    return matches[0]


def _job_excel(job: str) -> Path:
    if not job.isalnum():
        raise HTTPException(400, "Identifiant de traitement invalide")
    matches = list(config.UPLOAD_DIR.glob(f"{job}_*.xls*"))
    if len(matches) != 1:
        raise HTTPException(404, "Excel du traitement introuvable")
    return matches[0]


def _norm_token(value: str) -> str:
    value = unicodedata.normalize("NFD", str(value or ""))
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def _enrich_analysis_for_ui(job: str, data: dict) -> None:
    """Ajoute les référentiels manquants aux anciennes analyses sauvegardées."""
    corrections = analysis_store.normalize_corrections(data.get("corrections") or {})
    data["corrections"] = corrections

    referential = data.get("referentiel_excel") or {}
    if not referential.get("pieces") or not referential.get("materiels"):
        try:
            frame = excel_reader.read_listing(_job_excel(job))
            level = data.get("niveau_excel_selectionne")
            if level:
                selected = frame[frame["niveau"].str.casefold() == str(level).strip().casefold()]
                if not selected.empty:
                    frame = selected
            data["referentiel_excel"] = {
                "pieces": sorted(str(item) for item in frame["piece"].dropna().unique().tolist() if str(item).strip()),
                "materiels": sorted(str(item) for item in frame["materiel"].dropna().unique().tolist() if str(item).strip()),
            }
        except Exception:
            data["referentiel_excel"] = {
                "pieces": referential.get("pieces") or [],
                "materiels": referential.get("materiels") or [],
            }

    if not data.get("articles_rapproches"):
        mapped = {}
        for item in data.get("traceabilite") or []:
            article = str(item.get("article") or item.get("original_article") or "").strip()
            material = str(item.get("materiel_compare") or "").strip()
            if article and material:
                mapped[article] = material
        data["articles_rapproches"] = [
            {"plan": article, "maquette": material, "methode": "analyse sauvegardée", "score": 1.0}
            for article, material in sorted(mapped.items())
        ]

    if not data.get("objets_composes"):
        detected = {
            _norm_token(item.get("article") or item.get("original_article") or "")
            for item in data.get("traceabilite") or []
        }
        try:
            rules = json.loads((config.DATA_DIR / "material_rules.json").read_text(encoding="utf-8"))
        except Exception:
            rules = {}
        data["objets_composes"] = [
            {
                "article": str(rule.get("article") or ""),
                "items": [
                    {
                        "article": str(entry.get("article") or ""),
                        "categorie": str(entry.get("categorie") or ""),
                        "quantity": int(entry.get("quantity") or 1),
                    }
                    for entry in rule.get("items", [])
                    if str(entry.get("article") or "").strip()
                ],
            }
            for rule in rules.get("components", [])
            if _norm_token(rule.get("article") or "") in detected
        ]


@app.post("/api/history/{job}/corrections")
def api_save_corrections(job: str, payload: dict = Body(...)):
    """Enregistre les corrections utilisateur et recalcule le comparatif."""
    analysis_path = _analysis_file(job)
    previous = json.loads(analysis_path.read_text(encoding="utf-8"))
    excel_path = _job_excel(job)
    pdf_path = _job_pdf(job)
    output = Path(str(previous.get("output") or ""))
    if not output.name:
        output = config.OUTPUT_DIR / f"FTM_comparatif_{job}.xlsx"
    elif not output.is_absolute():
        output = config.OUTPUT_DIR / output.name

    corrections = {
        "rooms": payload.get("rooms") or [],
        "manual_objects": payload.get("manual_objects") or [],
        "edited_objects": payload.get("edited_objects") or {},
        "room_mappings": payload.get("room_mappings") or {},
        "material_mappings": payload.get("material_mappings") or {},
        "validated_articles": payload.get("validated_articles") or [],
    }
    try:
        summary = pipeline.run(
            str(excel_path), str(pdf_path), str(output),
            niveau_excel=previous.get("niveau_excel_selectionne"),
            nom_niveau=previous.get("niveau"),
            corrections=corrections,
        )
    except Exception as exc:
        raise HTTPException(422, f"Échec du recalcul : {exc}") from exc

    summary["download"] = f"/api/download/{Path(summary['output']).name}"
    summary["job"] = job
    summary["job_status"] = "done"
    summary["created_at"] = previous.get("created_at") or datetime.now().isoformat(timespec="seconds")
    summary["updated_at"] = datetime.now().isoformat(timespec="seconds")
    summary["excel_name"] = previous.get("excel_name") or excel_path.name.split("_", 1)[-1]
    summary["pdf_name"] = previous.get("pdf_name") or pdf_path.name.split("_", 1)[-1]
    summary["pdf_original"] = f"/api/jobs/{job}/pdf"
    if previous.get("ftm_document"):
        summary["ftm_document"] = previous["ftm_document"]
    if previous.get("word_output"):
        word_output = Path(str(previous["word_output"]))
        summary["word_output"] = str(config.OUTPUT_DIR / word_output.name)
        if (config.OUTPUT_DIR / word_output.name).exists():
            summary["word_download"] = f"/api/download/{word_output.name}"
    analysis_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return JSONResponse(summary)


@app.post("/api/history/{job}/ftm")
async def api_generate_ftm_word(job: str, payload: dict = Body(...)):
    """Enregistre les champs contrôlés par l'utilisateur et génère le Word FTM."""
    analysis_path = _analysis_file(job)
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    word_path = config.OUTPUT_DIR / f"FTM_{job}.docx"
    try:
        controlled_payload = {
            **payload,
            "materials_version": 2,
            "materials": word_report.materials_detected_in_pdf(analysis, payload),
        }
        normalized = await run_in_threadpool(word_report.write_ftm_document, word_path, controlled_payload)
    except Exception as exc:
        raise HTTPException(422, f"Échec de la génération Word : {exc}") from exc

    updated_at = datetime.now().isoformat(timespec="seconds")
    analysis["ftm_document"] = normalized
    analysis["word_output"] = str(word_path)
    analysis["word_download"] = f"/api/download/{word_path.name}"
    analysis["updated_at"] = updated_at
    analysis_path.write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
    return JSONResponse({
        "ftm_document": normalized,
        "word_download": f"/api/download/{word_path.name}",
        "updated_at": updated_at,
    })


@app.post("/api/history/{job}/corrections/draft")
def api_save_corrections_draft(job: str, payload: dict = Body(...)):
    try:
        data = analysis_store.save_corrections_draft(job, payload)
    except analysis_store.InvalidJobId as exc:
        raise HTTPException(400, str(exc)) from exc
    except analysis_store.AnalysisNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return JSONResponse({
        "job": job,
        "updated_at": data["updated_at"],
        "corrections": data["corrections"],
    })


@app.get("/api/jobs/{job}/pdf")
def api_job_pdf(job: str):
    path = _job_pdf(job)
    return FileResponse(path, media_type="application/pdf", filename=path.name.split("_", 1)[-1])


@app.get("/api/jobs/{job}/pdf/pages/{page_number}.png")
def api_pdf_page(job: str, page_number: int, x: float | None = None, y: float | None = None,
                 annotated: bool = True):
    """Apercu PNG d'une page, avec repere rouge optionnel sur la source."""
    path = _job_pdf(job)
    document = fitz.open(path)
    try:
        if page_number < 1 or page_number > document.page_count:
            raise HTTPException(404, "Page PDF introuvable")
        page = document[page_number - 1]
        analysis_path = config.OUTPUT_DIR / f"analysis_{job}.json"
        if annotated and analysis_path.exists():
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            for symbol in analysis.get("traceabilite", []):
                if int(symbol["page"]) != page_number:
                    continue
                point = fitz.Point(float(symbol["x"]), float(symbol["y"]))
                page.draw_circle(point, 7, color=(0.05, 0.35, 0.75), fill=(1, 1, 1), width=1.5, overlay=True)
                page.insert_text(
                    fitz.Point(point.x - 2.5, point.y + 2.5), str(symbol.get("marker", "")),
                    fontsize=6, color=(0.05, 0.25, 0.65), overlay=True,
                )
        if x is not None and y is not None:
            point = fitz.Point(x, y)
            page.draw_circle(point, 10, color=(1, 0, 0), width=3, overlay=True)
            page.draw_circle(point, 3, color=(1, 0, 0), fill=(1, 0, 0), overlay=True)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
        return Response(pixmap.tobytes("png"), media_type="image/png")
    finally:
        document.close()


@app.get("/api/jobs/{job}/pdf/pages/{page_number}/markers")
def api_pdf_markers(job: str, page_number: int):
    """Coordonnees normalisees pour la couche interactive du plan."""
    path = _job_pdf(job)
    analysis_path = config.OUTPUT_DIR / f"analysis_{job}.json"
    if not analysis_path.exists():
        raise HTTPException(404, "Analyse introuvable")
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    document = fitz.open(path)
    try:
        if page_number < 1 or page_number > document.page_count:
            raise HTTPException(404, "Page PDF introuvable")
        page = document[page_number - 1]
        width, height = float(page.rect.width), float(page.rect.height)

        def convert(item, kind):
            point = fitz.Point(float(item["x"]), float(item["y"])) * page.rotation_matrix
            return {
                **item, "kind": kind,
                "left": round(point.x / width * 100, 4),
                "top": round(point.y / height * 100, 4),
            }

        counted = [convert(item, "counted") for item in analysis.get("traceabilite", [])
                   if int(item["page"]) == page_number]
        ignored = [convert(item, "uncatalogued") for item in analysis.get("non_comptes", [])
                   if int(item["page"]) == page_number]
        return {"page": page_number, "width": width, "height": height,
                "counted": counted, "uncatalogued": ignored}
    finally:
        document.close()


@app.get("/api/template-excel")
def api_template_excel():
    """Modele documente pour un import et des correspondances controlables."""
    stream = BytesIO()
    workbook = xlsxwriter.Workbook(stream, {"in_memory": True})
    header = workbook.add_format({"bold": True, "bg_color": "#17365D", "font_color": "white", "border": 1})
    note = workbook.add_format({"text_wrap": True, "valign": "top", "bg_color": "#EAF2F8"})

    columns = ["N° LOT", "Occupation", "Nom de la pièce", "Catégorie", "Code article", "Matériel", "Quantité"]
    for level, sample in [
        ("RDC", [4, "TEP SCAN", "WC Personnel", "Sanitaire", "SAN-WC-001", "WC", 1]),
        ("R+1", [27, "CHIRURGIE ESTHETIQUE", "Consultation", "Électricité", "ELEC-PC-001", "Prise de courant", 8]),
        ("R+2", [47, "ANESTHESISTE", "Consultation", "CVC", "CVC-BR-001", "Bouche de reprise", 1]),
    ]:
        sheet = workbook.add_worksheet(level)
        sheet.write(0, 0, level)
        sheet.write_row(5, 0, columns, header)
        sheet.write_row(6, 0, sample)
        sheet.set_column("A:A", 12); sheet.set_column("B:B", 24); sheet.set_column("C:C", 28)
        sheet.set_column("D:D", 18); sheet.set_column("E:E", 18); sheet.set_column("F:F", 42); sheet.set_column("G:G", 10)

    rooms = workbook.add_worksheet("Correspondance pièces")
    rooms.write_row(0, 0, ["Pièce plan (PDF)", "Pièce existante (Excel)", "Commentaire"], header)
    rooms.write_row(1, 0, ["Vasculaire 01", "Consultation 1", "À confirmer avec le maître d'œuvre"])
    rooms.set_column("A:B", 30); rooms.set_column("C:C", 48)

    articles = workbook.add_worksheet("Correspondance articles")
    articles.write_row(0, 0, ["Article plan (PDF)", "Matériel existant (Excel)", "Commentaire"], header)
    articles.write_row(1, 0, ["PC 10/16A 2P+T", "Prise de courant 16A 2P+T", "Même article, libellé différent"])
    articles.set_column("A:B", 40); articles.set_column("C:C", 48)

    help_sheet = workbook.add_worksheet("MODE D'EMPLOI")
    help_sheet.set_column("A:A", 110)
    help_sheet.write(0, 0, "Format recommandé : une feuille par niveau (RDC, R+1, R+2...). Colonnes obligatoires : Occupation, Nom de la pièce, Catégorie, Matériel, Quantité. N° LOT/Numéro et Code article sont optionnels.", note)
    help_sheet.write(2, 0, "Si une pièce change de nom entre la maquette et le plan, renseigner la feuille Correspondance pièces. Sans cette information, FTMgen ne doit pas inventer la relation.", note)
    help_sheet.write(4, 0, "Si le même objet porte deux libellés différents, renseigner la feuille Correspondance articles avec le nom EXACT présent dans chaque source.", note)
    workbook.close()
    return Response(
        stream.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="Modele_FTMgen.xlsx"'},
    )
