# -*- coding: utf-8 -*-
"""Stockage des analyses et corrections.

Cette couche garde l'API indépendante du format actuel de persistance. Pour le
moment les analyses restent en JSON dans output/, mais le reste de l'application
peut évoluer vers SQLite/PostgreSQL sans réécrire les routes.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import config


class AnalysisNotFound(FileNotFoundError):
    pass


class InvalidJobId(ValueError):
    pass


def validate_job_id(job: str) -> str:
    if not str(job or "").isalnum():
        raise InvalidJobId("Identifiant d'analyse invalide")
    return str(job)


def analysis_path(job: str) -> Path:
    job = validate_job_id(job)
    path = config.OUTPUT_DIR / f"analysis_{job}.json"
    if not path.exists():
        raise AnalysisNotFound("Analyse introuvable")
    return path


def read_analysis(job: str) -> dict[str, Any]:
    return json.loads(analysis_path(job).read_text(encoding="utf-8"))


def write_analysis(job: str, data: dict[str, Any]) -> None:
    job = validate_job_id(job)
    path = config.OUTPUT_DIR / f"analysis_{job}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def list_analyses() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    paths = sorted(config.OUTPUT_DIR.glob("analysis_*.json"),
                   key=lambda item: item.stat().st_mtime, reverse=True)
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        job = data.get("job") or path.stem.replace("analysis_", "")
        items.append({
            "job": job,
            "created_at": data.get("created_at") or datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            "updated_at": data.get("updated_at"),
            "excel_name": data.get("excel_name") or "Excel non renseigné",
            "pdf_name": data.get("pdf_name") or "PDF non renseigné",
            "niveau": data.get("niveau"),
            "job_status": data.get("job_status") or "done",
            "symboles_detectes": data.get("symboles_detectes", 0),
            "lignes": data.get("lignes", 0),
        })
    return items


def normalize_corrections(payload: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or {}
    return {
        "rooms": payload.get("rooms", previous.get("rooms") or []),
        "manual_objects": payload.get("manual_objects", previous.get("manual_objects") or []),
        "edited_objects": payload.get("edited_objects", previous.get("edited_objects") or {}),
        "room_mappings": payload.get("room_mappings", previous.get("room_mappings") or {}),
        "material_mappings": payload.get("material_mappings", previous.get("material_mappings") or {}),
        "validated_articles": payload.get("validated_articles", previous.get("validated_articles") or []),
    }


def save_corrections_draft(job: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = read_analysis(job)
    data["corrections"] = normalize_corrections(payload, data.get("corrections") or {})
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    write_analysis(job, data)
    return data


def delete_analysis(job: str) -> list[str]:
    path = analysis_path(job)
    data = json.loads(path.read_text(encoding="utf-8"))
    removed: list[str] = []

    output = Path(str(data.get("output", "")))
    candidates = [path]
    if output.name:
        candidates.append(config.OUTPUT_DIR / output.name)
    candidates.extend(config.UPLOAD_DIR.glob(f"{validate_job_id(job)}_*"))

    for candidate in candidates:
        resolved = candidate.resolve()
        allowed = (
            resolved.parent == config.OUTPUT_DIR.resolve()
            or resolved.parent == config.UPLOAD_DIR.resolve()
        )
        if allowed and resolved.exists() and resolved.is_file():
            resolved.unlink()
            removed.append(resolved.name)
    return removed
