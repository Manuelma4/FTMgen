# -*- coding: utf-8 -*-
"""API FTMgen — FastAPI.

Lancement :  .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8060
"""
import uuid
import json
import hmac
import re
import unicodedata
from io import BytesIO
from datetime import datetime
from pathlib import Path

import fitz
import xlsxwriter
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, pipeline
from .core import compare as compare_mod
from .core import relations, word_report
from .extract import excel_reader
from .services import analysis_store, auth_service

app = FastAPI(title="FTMgen", description="Comparatif maquette / plan de travaux modificatifs")

WEB_DIR = config.BASE_DIR / "web"
FRONTEND_DIST = config.BASE_DIR / "frontend" / "dist"

if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")


def _authenticated_user(request: Request | None) -> auth_service.UserIdentity:
    try:
        return auth_service.service.require_user(request)
    except auth_service.AuthUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except auth_service.AuthenticationRequired as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "OIDC"}) from exc


def _owned_analysis(
    job: str, request: Request | None
) -> tuple[dict, auth_service.UserIdentity]:
    user = _authenticated_user(request)
    # Compatibilite des appels internes/tests historiques, uniquement dans le
    # mode local explicite. Les requetes HTTP fournissent toujours Request.
    if request is None and user.is_local:
        return json.loads(_analysis_file(job).read_text(encoding="utf-8")), user
    try:
        data = analysis_store.read_analysis(
            job,
            owner_sub=user.sub,
            allow_legacy=auth_service.service.can_access_legacy(user),
        )
    except analysis_store.InvalidJobId as exc:
        raise HTTPException(400, str(exc)) from exc
    except analysis_store.AnalysisNotFound as exc:
        # Un job appartenant a un autre compte est volontairement indistinguable
        # d'un identifiant absent.
        raise HTTPException(404, str(exc)) from exc
    return data, user


@app.get("/")
def index():
    if (FRONTEND_DIST / "index.html").exists():
        return FileResponse(FRONTEND_DIST / "index.html")
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def api_health():
    if auth_service.service.mode == "unavailable":
        return JSONResponse({"status": "error", "auth_mode": "unavailable"}, status_code=503)
    return {"status": "ok", "auth_mode": auth_service.service.mode}


@app.get("/api/auth/me")
def api_auth_me(request: Request):
    try:
        user = auth_service.service.optional_user(request)
    except auth_service.AuthUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if user is None:
        raise HTTPException(401, "Authentification requise", headers={"WWW-Authenticate": "OIDC"})
    identity = user.snapshot()
    identity["preferred_username"] = identity.get("username") or ""
    return identity


@app.get("/api/auth/login")
async def api_auth_login(redirect_after: str = "/"):
    if auth_service.service.mode == "local":
        return RedirectResponse(auth_service._safe_redirect_path(redirect_after), status_code=302)
    try:
        url, state = await auth_service.service.begin_login(redirect_after)
    except auth_service.AuthUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Fournisseur OIDC indisponible : {exc}") from exc
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        config.FTM_OIDC_STATE_COOKIE_NAME,
        state,
        max_age=config.FTM_OIDC_STATE_TTL_SECONDS,
        httponly=True,
        secure=config.FTM_SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/api/auth/callback",
    )
    return response


@app.get("/api/auth/callback")
async def api_auth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    def callback_error(status_code: int, detail: str) -> JSONResponse:
        response = JSONResponse({"detail": detail}, status_code=status_code)
        response.delete_cookie(
            config.FTM_OIDC_STATE_COOKIE_NAME,
            path="/api/auth/callback",
            httponly=True,
            secure=config.FTM_SESSION_COOKIE_SECURE,
            samesite="lax",
        )
        return response

    if error:
        return callback_error(400, error_description or error)
    if not code or not state:
        return callback_error(400, "Retour OIDC incomplet")
    browser_state = str(request.cookies.get(config.FTM_OIDC_STATE_COOKIE_NAME) or "")
    if not browser_state or not hmac.compare_digest(browser_state, state):
        return callback_error(400, "Etat OIDC non lie a ce navigateur")
    try:
        opaque_id, _user, redirect_after = await auth_service.service.complete_login(
            code=code, state=state
        )
    except auth_service.AuthUnavailable as exc:
        return callback_error(503, str(exc))
    except (auth_service.InvalidOAuthResponse, ValueError) as exc:
        return callback_error(400, str(exc))
    except Exception as exc:
        return callback_error(502, f"Echec de la connexion OIDC : {exc}")
    response = RedirectResponse(redirect_after, status_code=302)
    response.delete_cookie(
        config.FTM_OIDC_STATE_COOKIE_NAME,
        path="/api/auth/callback",
        httponly=True,
        secure=config.FTM_SESSION_COOKIE_SECURE,
        samesite="lax",
    )
    response.set_cookie(
        config.FTM_SESSION_COOKIE_NAME,
        opaque_id,
        max_age=config.FTM_SESSION_TTL_SECONDS,
        httponly=True,
        secure=config.FTM_SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/api/auth/logout")
async def api_auth_logout(request: Request):
    try:
        url = await auth_service.service.logout_url(request)
    except auth_service.AuthUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Echec de la deconnexion OIDC : {exc}") from exc
    response = RedirectResponse(url or "/", status_code=302)
    response.delete_cookie(
        config.FTM_SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=config.FTM_SESSION_COOKIE_SECURE,
        samesite="lax",
    )
    return response


@app.post("/api/excel/inspect")
async def api_excel_inspect(request: Request, excel: UploadFile = File(...)):
    _authenticated_user(request)
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
            "scope_options": relations.excel_scope_options(group),
        })
    return {"niveaux": levels}


@app.post("/api/compare")
async def api_compare(
    request: Request,
    excel: UploadFile = File(...), pdf: UploadFile = File(...),
    niveau_excel: str = Form(""), nom_niveau: str = Form(""),
    excel_scope_id: str = Form(""),
):
    user = _authenticated_user(request)
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
            corrections={"excel_scope_id": excel_scope_id} if excel_scope_id else None,
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
    analysis_store.attach_owner(summary, user.snapshot())
    analysis_store.write_analysis(job, summary)
    return JSONResponse(summary)


def _analysis_file(job: str) -> Path:
    if not job.isalnum():
        raise HTTPException(400, "Identifiant d'analyse invalide")
    path = config.OUTPUT_DIR / f"analysis_{job}.json"
    if not path.exists():
        raise HTTPException(404, "Analyse introuvable")
    return path


@app.get("/api/history")
def api_history(request: Request):
    user = _authenticated_user(request)
    return {"analyses": analysis_store.list_analyses(
        owner_sub=user.sub,
        allow_legacy=auth_service.service.can_access_legacy(user),
    )}


@app.get("/api/history/{job}")
def api_history_detail(job: str, request: Request):
    data, _user = _owned_analysis(job, request)
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
def api_history_delete(job: str, request: Request):
    user = _authenticated_user(request)
    try:
        removed = analysis_store.delete_analysis(
            job,
            owner_sub=user.sub,
            allow_legacy=auth_service.service.can_access_legacy(user),
        )
    except analysis_store.InvalidJobId as exc:
        raise HTTPException(400, str(exc)) from exc
    except analysis_store.AnalysisNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"deleted": job, "files": removed}


@app.get("/api/download/{name}")
def api_download(name: str, request: Request):
    user = _authenticated_user(request)
    try:
        analysis_store.analysis_for_output(
            name,
            owner_sub=user.sub,
            allow_legacy=auth_service.service.can_access_legacy(user),
        )
    except analysis_store.AnalysisNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
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

    # Migration douce : les versions précédentes sauvegardaient les choix dans
    # ftm_document, mais laissaient corrections vide. On les réexpose désormais
    # comme relations objet par objet afin qu'un prochain Apply les utilise.
    if not corrections.get("object_relations") and (data.get("ftm_document") or {}).get("materials"):
        corrections = analysis_store.corrections_with_ftm_materials(
            corrections,
            corrections,
            data["ftm_document"]["materials"],
            None,
        )

    referential = data.get("referentiel_excel") or {}
    try:
        frame = excel_reader.read_listing(_job_excel(job))
        level = data.get("niveau_excel_selectionne")
        if level:
            selected = frame[frame["niveau"].str.casefold() == str(level).strip().casefold()]
            if not selected.empty:
                frame = selected
        scope_options = relations.excel_scope_options(frame)
        piece_options = relations.excel_room_options(frame)
        # Le scope du comparatif calculé est la référence. Les brouillons et le
        # formulaire Word ne peuvent pas le remplacer lors d'un simple GET.
        requested_scope = (data.get("audit_excel") or {}).get("scope_selectionne") \
            or data.get("excel_scope_selectionne") \
            or corrections.get("excel_scope_id") \
            or (data.get("ftm_document") or {}).get("excel_scope_id")
        selected_scope = relations.resolve_excel_scope(requested_scope, scope_options)
        if selected_scope is None:
            pdf_rooms = {
                _norm_token(item.get("room"))
                for item in (data.get("traceabilite") or [])
                if isinstance(item, dict) and _norm_token(item.get("room"))
            }
            pdf_relation_keys = {
                str(item.get("mapping_key") or relations.object_relation_key(
                    item.get("room"), item.get("article") or item.get("original_article")
                ))
                for item in (data.get("traceabilite") or [])
                if isinstance(item, dict)
            }
            relation_targets = [
                relation.get("target_room_id")
                for key, relation in (corrections.get("object_relations") or {}).items()
                if str(key) in pdf_relation_keys
                and isinstance(relation, dict)
                and relation.get("target_room_id")
            ]
            relation_targets.extend(
                target for source, target in (corrections.get("room_mappings") or {}).items()
                if _norm_token(source) in pdf_rooms and str(target or "").strip()
            )
            selected_scope, _method = relations.infer_excel_scope(
                scope_options,
                piece_options,
                relation_targets,
                [data.get("pdf_name") or "", _job_pdf(job).stem, (data.get("ftm_document") or {}).get("pole") or ""],
            )
        if selected_scope:
            active_frame = relations.filter_excel_scope(frame, selected_scope)
        elif len(scope_options) > 1:
            active_frame = frame.iloc[0:0].copy()
        else:
            active_frame = frame
        selected_scope_id = str((selected_scope or {}).get("id") or "")
        data["referentiel_excel"] = {
            "pieces": sorted(
                str(item) for item in active_frame["piece"].dropna().unique().tolist()
                if str(item).strip()
            ),
            "materiels": sorted(
                str(item) for item in active_frame["materiel"].dropna().unique().tolist()
                if str(item).strip() and not compare_mod._is_invalid_excel_material(item)
            ),
            "piece_options": piece_options,
            "scope_options": scope_options,
            "selected_scope_id": selected_scope_id,
            "selected_scope": selected_scope,
        }
        if selected_scope_id:
            corrections["excel_scope_id"] = selected_scope_id
            if str(data.get("excel_scope_selectionne") or "") == selected_scope_id:
                data["excel_scope"] = selected_scope
        else:
            corrections["excel_scope_id"] = ""
        if isinstance(data.get("ftm_document"), dict):
            # Le formulaire Word ne doit jamais conserver un scope obsolète ou
            # absent du référentiel Excel actuellement validé.
            data["ftm_document"]["excel_scope_id"] = selected_scope_id
    except Exception:
        safe_scope_options = [
            dict(item) for item in (referential.get("scope_options") or [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
        safe_scope_ids = {str(item["id"]) for item in safe_scope_options}
        safe_piece_options = [
            dict(item) for item in (referential.get("piece_options") or [])
            if isinstance(item, dict)
            and bool(safe_scope_ids)
            and str(item.get("scope_id") or "").strip()
            and str(item.get("scope_id")) in safe_scope_ids
        ]
        requested_safe_scope = str(
            (data.get("audit_excel") or {}).get("scope_selectionne")
            or data.get("excel_scope_selectionne")
            or corrections.get("excel_scope_id")
            or ""
        ).strip()
        if requested_safe_scope not in safe_scope_ids:
            requested_safe_scope = str(referential.get("selected_scope_id") or "").strip()
        if requested_safe_scope not in safe_scope_ids:
            requested_safe_scope = ""
        selected_safe_scope = next(
            (item for item in safe_scope_options if str(item["id"]) == requested_safe_scope), None
        )
        data["referentiel_excel"] = {
            # Échec fermé : les anciens catalogues globaux ne sont pas sûrs.
            "pieces": [],
            "materiels": [],
            "piece_options": safe_piece_options,
            "scope_options": safe_scope_options,
            "selected_scope_id": requested_safe_scope,
            "selected_scope": selected_safe_scope,
        }
        corrections["excel_scope_id"] = requested_safe_scope
        if isinstance(data.get("ftm_document"), dict):
            data["ftm_document"]["excel_scope_id"] = requested_safe_scope
    data["corrections"] = corrections

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


def _validated_ftm_scope(analysis: dict, payload: dict) -> str:
    """Retourne le scope réellement calculé ou refuse une génération divergente."""
    comparison_scope_id = str(
        (analysis.get("audit_excel") or {}).get("scope_selectionne") or ""
    ).strip()
    scope_options = (analysis.get("referentiel_excel") or {}).get("scope_options") or []
    valid_scope_ids = {
        str(item.get("id") or "").strip()
        for item in scope_options if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    requested_scope_id = str(payload.get("excel_scope_id") or "").strip()
    if not comparison_scope_id:
        raise HTTPException(
            409,
            "Le comparatif enregistré ne possède pas de pôle/lot calculé. "
            "Utilisez « Appliquer » pour recalculer Excel avant de générer le Word.",
        )
    if comparison_scope_id not in valid_scope_ids:
        raise HTTPException(
            422,
            "Le pôle/lot du comparatif n'existe plus dans le référentiel Excel. "
            "Recalculez l'analyse avant de générer le Word.",
        )
    if requested_scope_id and requested_scope_id not in valid_scope_ids:
        raise HTTPException(422, "Le pôle/lot demandé n'existe pas dans le référentiel Excel")
    if requested_scope_id and requested_scope_id != comparison_scope_id:
        raise HTTPException(
            409,
            "Le pôle/lot du Word diffère du comparatif calculé. "
            "Utilisez « Appliquer » pour recalculer Excel avec ce périmètre.",
        )
    return comparison_scope_id


def _validate_corrections_document_scope(payload: dict) -> tuple[str, str]:
    submitted_document = payload.get("ftm_document") if isinstance(payload.get("ftm_document"), dict) else None
    correction_scope_id = str(payload.get("excel_scope_id") or "").strip()
    document_scope_id = str((submitted_document or {}).get("excel_scope_id") or "").strip()
    if correction_scope_id and document_scope_id and correction_scope_id != document_scope_id:
        raise HTTPException(
            409,
            "Le pôle/lot des corrections diffère de celui du document Word. "
            "Sélectionnez un seul périmètre puis relancez le calcul.",
        )
    return correction_scope_id, document_scope_id


@app.post("/api/history/{job}/corrections")
def api_save_corrections(
    job: str, payload: dict = Body(...), request: Request = None
):
    """Enregistre les corrections utilisateur et recalcule le comparatif."""
    previous, user = _owned_analysis(job, request)
    excel_path = _job_excel(job)
    pdf_path = _job_pdf(job)
    output = Path(str(previous.get("output") or ""))
    if not output.name:
        output = config.OUTPUT_DIR / f"FTM_comparatif_{job}.xlsx"
    elif not output.is_absolute():
        output = config.OUTPUT_DIR / output.name

    submitted_document = payload.get("ftm_document") if isinstance(payload.get("ftm_document"), dict) else None
    _correction_scope_id, document_scope_id = _validate_corrections_document_scope(payload)

    corrections = analysis_store.normalize_corrections(payload, previous.get("corrections") or {})
    if submitted_document is not None:
        if document_scope_id:
            corrections["excel_scope_id"] = document_scope_id
        corrections = analysis_store.corrections_with_ftm_materials(
            corrections,
            previous.get("corrections") or {},
            [item for item in (submitted_document.get("materials") or []) if isinstance(item, dict)],
            word_report.all_pdf_relation_keys(previous),
        )
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
    if previous.get("owner_sub"):
        analysis_store.preserve_owner(summary, previous)
    else:
        # Un job historique ne peut être ouvert en production que par le
        # subject explicitement configuré dans FTM_LEGACY_OWNER_SUB. Sa
        # première modification le rattache définitivement à ce compte.
        analysis_store.attach_owner(summary, user.snapshot())
    ftm_source = submitted_document or previous.get("ftm_document")
    if isinstance(ftm_source, dict):
        word_path = config.OUTPUT_DIR / f"FTM_{job}.docx"
        calculated_scope_id = str(
            (summary.get("audit_excel") or {}).get("scope_selectionne") or ""
        ).strip()
        controlled_payload = {
            **ftm_source,
            "excel_scope_id": calculated_scope_id,
            "materials_version": 3,
            "materials": word_report.materials_detected_in_pdf(summary, {
                **ftm_source,
                "materials_version": 3,
            }),
        }
        try:
            summary["ftm_document"] = word_report.write_ftm_document(word_path, controlled_payload)
            summary["word_output"] = str(word_path)
            summary["word_download"] = f"/api/download/{word_path.name}"
            summary["corrections"] = analysis_store.corrections_with_ftm_materials(
                summary.get("corrections") or {},
                summary.get("corrections") or {},
                summary["ftm_document"]["materials"],
                word_report.all_pdf_relation_keys(summary),
            )
        except Exception as exc:
            raise HTTPException(422, f"Comparatif calculé, mais échec de la génération Word : {exc}") from exc
    analysis_store.write_analysis(job, summary)
    return JSONResponse(summary)


@app.post("/api/history/{job}/ftm")
async def api_generate_ftm_word(
    job: str, payload: dict = Body(...), request: Request = None
):
    """Enregistre le formulaire puis recalcule Excel et Word ensemble.

    D'anciens clients appelaient cette route après avoir modifié les relations
    objet/pièce. Générer uniquement le Word conservait alors un comparatif
    obsolète : les cibles étaient visibles dans le formulaire, mais ``Avant``
    restait à zéro. La route réutilise désormais exactement le même recalcul
    atomique que le bouton « Appliquer ».
    """
    analysis, _user = _owned_analysis(job, request)
    _enrich_analysis_for_ui(job, analysis)
    comparison_scope_id = _validated_ftm_scope(analysis, payload)
    recalculation_payload = {
        **(analysis.get("corrections") or {}),
        "excel_scope_id": comparison_scope_id,
        "ftm_document": {
            **payload,
            "excel_scope_id": comparison_scope_id,
            "materials_version": 3,
        },
    }
    return await run_in_threadpool(api_save_corrections, job, recalculation_payload, request)


@app.post("/api/history/{job}/corrections/draft")
def api_save_corrections_draft(
    job: str, request: Request, payload: dict = Body(...)
):
    user = _authenticated_user(request)
    try:
        data = analysis_store.save_corrections_draft(
            job,
            payload,
            owner_sub=user.sub,
            allow_legacy=auth_service.service.can_access_legacy(user),
        )
        if not data.get("owner_sub"):
            analysis_store.attach_owner(data, user.snapshot())
            analysis_store.write_analysis(job, data)
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
def api_job_pdf(job: str, request: Request):
    _owned_analysis(job, request)
    path = _job_pdf(job)
    return FileResponse(path, media_type="application/pdf", filename=path.name.split("_", 1)[-1])


@app.get("/api/jobs/{job}/pdf/pages/{page_number}.png")
def api_pdf_page(
    job: str, page_number: int, request: Request,
    x: float | None = None, y: float | None = None, annotated: bool = True,
):
    """Apercu PNG d'une page, avec repere rouge optionnel sur la source."""
    analysis, _user = _owned_analysis(job, request)
    path = _job_pdf(job)
    document = fitz.open(path)
    try:
        if page_number < 1 or page_number > document.page_count:
            raise HTTPException(404, "Page PDF introuvable")
        page = document[page_number - 1]
        if annotated:
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
def api_pdf_markers(job: str, page_number: int, request: Request):
    """Coordonnees normalisees pour la couche interactive du plan."""
    analysis, _user = _owned_analysis(job, request)
    path = _job_pdf(job)
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
def api_template_excel(request: Request):
    """Modele documente pour un import et des correspondances controlables."""
    _authenticated_user(request)
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

