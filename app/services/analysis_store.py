# -*- coding: utf-8 -*-
"""Stockage des analyses et corrections.

Cette couche garde l'API indépendante du format actuel de persistance. Pour le
moment les analyses restent en JSON dans output/, mais le reste de l'application
peut évoluer vers SQLite/PostgreSQL sans réécrire les routes.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import config
from ..core.relations import object_relation_key


class AnalysisNotFound(FileNotFoundError):
    pass


class InvalidJobId(ValueError):
    pass


def owner_can_access(
    data: dict[str, Any],
    owner_sub: str | None,
    *,
    allow_legacy: bool = False,
) -> bool:
    """Retourne False sans reveler si un job d'un autre utilisateur existe.

    ``owner_sub=None`` reste reserve aux appels internes et aux migrations afin
    de ne pas casser les traitements historiques. Les routes HTTP passent
    toujours un subject explicite.
    """
    if owner_sub is None:
        return True
    stored_owner = str(data.get("owner_sub") or "").strip()
    if stored_owner:
        return stored_owner == str(owner_sub).strip()
    return bool(allow_legacy)


def attach_owner(data: dict[str, Any], owner: dict[str, Any]) -> dict[str, Any]:
    """Ajoute le subject immuable et une photographie d'affichage du compte."""
    owner_sub = str(owner.get("sub") or "").strip()
    if not owner_sub:
        raise ValueError("Le proprietaire doit contenir un subject")
    data["owner_sub"] = owner_sub
    data["owner"] = {
        "sub": owner_sub,
        "username": str(owner.get("username") or "").strip(),
        "email": str(owner.get("email") or "").strip(),
        "name": str(owner.get("name") or "").strip(),
    }
    return data


def preserve_owner(data: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    """Un recalcul ne doit jamais permettre de changer le proprietaire."""
    if previous.get("owner_sub"):
        data["owner_sub"] = previous["owner_sub"]
        data["owner"] = dict(previous.get("owner") or {"sub": previous["owner_sub"]})
    else:
        data.pop("owner_sub", None)
        data.pop("owner", None)
    return data


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


def read_analysis(
    job: str,
    *,
    owner_sub: str | None = None,
    allow_legacy: bool = False,
) -> dict[str, Any]:
    data = json.loads(analysis_path(job).read_text(encoding="utf-8"))
    if not owner_can_access(data, owner_sub, allow_legacy=allow_legacy):
        raise AnalysisNotFound("Analyse introuvable")
    return data


def write_analysis(job: str, data: dict[str, Any]) -> None:
    job = validate_job_id(job)
    path = config.OUTPUT_DIR / f"analysis_{job}.json"
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def list_analyses(
    *,
    owner_sub: str | None = None,
    allow_legacy: bool = False,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    paths = sorted(config.OUTPUT_DIR.glob("analysis_*.json"),
                   key=lambda item: item.stat().st_mtime, reverse=True)
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not owner_can_access(data, owner_sub, allow_legacy=allow_legacy):
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
            "owner_sub": data.get("owner_sub"),
            "owner": data.get("owner"),
        })
    return items


def analysis_for_output(
    name: str,
    *,
    owner_sub: str | None = None,
    allow_legacy: bool = False,
) -> dict[str, Any]:
    """Retrouve le job possedant un fichier exporte, sans exposer les voisins."""
    safe_name = Path(str(name or "")).name
    if safe_name != str(name or ""):
        raise AnalysisNotFound("Fichier introuvable")
    for path in config.OUTPUT_DIR.glob("analysis_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        candidates = {
            Path(str(data.get("output") or "")).name,
            Path(str(data.get("word_output") or "")).name,
        }
        if safe_name in candidates and owner_can_access(
            data, owner_sub, allow_legacy=allow_legacy
        ):
            return data
    raise AnalysisNotFound("Fichier introuvable")


def normalize_corrections(payload: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or {}
    return {
        "excel_scope_id": str(
            payload.get("excel_scope_id", previous.get("excel_scope_id") or "") or ""
        ).strip(),
        "rooms": payload.get("rooms", previous.get("rooms") or []),
        "manual_objects": payload.get("manual_objects", previous.get("manual_objects") or []),
        "edited_objects": payload.get("edited_objects", previous.get("edited_objects") or {}),
        "room_mappings": payload.get("room_mappings", previous.get("room_mappings") or {}),
        "material_mappings": payload.get("material_mappings", previous.get("material_mappings") or {}),
        "validated_articles": payload.get("validated_articles", previous.get("validated_articles") or []),
        "object_relations": payload.get("object_relations", previous.get("object_relations") or {}),
        "manual_lines": payload.get("manual_lines", previous.get("manual_lines") or []),
        "excluded_relations": payload.get("excluded_relations", previous.get("excluded_relations") or []),
    }


def corrections_with_ftm_materials(
    payload: dict[str, Any],
    previous: dict[str, Any] | None,
    materials: list[dict[str, Any]],
    all_pdf_relation_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Fusionne les choix visibles dans la table Word avec les corrections.

    Historiquement le Word et le comparatif conservaient deux états séparés.
    Cette fonction fait de la table Word la source des relations objet par objet.
    """
    corrections = normalize_corrections(payload, previous)
    object_relations: dict[str, dict[str, Any]] = {}
    manual_lines: list[dict[str, Any]] = []
    submitted_pdf_keys: set[str] = set()
    for raw in materials or []:
        if not isinstance(raw, dict):
            continue
        origin = str(raw.get("origin") or "pdf").strip().lower()
        if origin == "manual":
            manual_lines.append(dict(raw))
            continue
        key = str(raw.get("mapping_key") or object_relation_key(raw.get("room"), raw.get("material"))).strip()
        if not key:
            continue
        submitted_pdf_keys.add(key)
        object_relations[key] = {
            "target_room_id": str(raw.get("comparison_room") or "").strip(),
            "target_material": str(raw.get("comparison_material") or "").strip(),
            "is_addition": bool(raw.get("is_addition", False)),
        }
    corrections["object_relations"] = object_relations
    corrections["manual_lines"] = manual_lines
    if all_pdf_relation_keys is not None:
        corrections["excluded_relations"] = sorted(set(all_pdf_relation_keys) - submitted_pdf_keys)
    return corrections


def save_corrections_draft(
    job: str,
    payload: dict[str, Any],
    *,
    owner_sub: str | None = None,
    allow_legacy: bool = False,
) -> dict[str, Any]:
    data = read_analysis(job, owner_sub=owner_sub, allow_legacy=allow_legacy)
    data["corrections"] = normalize_corrections(payload, data.get("corrections") or {})
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    write_analysis(job, data)
    return data


def delete_analysis(
    job: str,
    *,
    owner_sub: str | None = None,
    allow_legacy: bool = False,
) -> list[str]:
    path = analysis_path(job)
    data = read_analysis(job, owner_sub=owner_sub, allow_legacy=allow_legacy)
    removed: list[str] = []

    output = Path(str(data.get("output", "")))
    candidates = [path]
    if output.name:
        candidates.append(config.OUTPUT_DIR / output.name)
    word_output = Path(str(data.get("word_output", "")))
    if word_output.name:
        candidates.append(config.OUTPUT_DIR / word_output.name)
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
