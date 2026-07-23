# -*- coding: utf-8 -*-
"""Identifiants stables pour les relations PDF -> Excel.

Les noms de pièces ne sont pas des identifiants : un même étage peut contenir
plusieurs ``Consultation`` appartenant à des occupations et numéros différents.
Ce module centralise les clés utilisées par le comparatif, l'API et le Word.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Iterable


def normalize_token(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def object_relation_key(room: Any, material: Any) -> str:
    """Clé lisible et reproductible d'un objet PDF dans une pièce."""
    return f"v1:{normalize_token(room)}::{normalize_token(material)}"


def excel_scope_id(niveau: Any, occupation: Any, numero: Any) -> str:
    """Identifiant stable d'un lot/pôle physique de la maquette.

    Le nom de la pièce ne fait volontairement pas partie de cette clé : toutes
    les salles d'un même lot (par exemple ``VASCULAIRE ANGIO`` n° 28) doivent
    partager le même périmètre de comparaison.
    """
    identity = "|".join(normalize_token(value) for value in (niveau, occupation, numero))
    return f"excel-scope-{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:16]}"


def excel_scope_label(niveau: Any, occupation: Any, numero: Any) -> str:
    parts = [str(occupation or "Pôle non renseigné").strip() or "Pôle non renseigné"]
    if str(numero or "").strip():
        parts.append(f"lot n° {str(numero).strip()}")
    label = " · ".join(parts)
    if str(niveau or "").strip():
        label += f" ({str(niveau).strip()})"
    return label


def _valid_material_label(value: Any) -> bool:
    text = str(value or "").strip().upper()
    return bool(text) and not (
        text.startswith("#REF")
        or text in {"#N/A", "#VALUE!", "#NAME?", "#DIV/0!"}
    )


def excel_scope_options(frame) -> list[dict[str, Any]]:
    """Retourne les périmètres physiques disponibles dans un niveau Excel.

    Chaque option contient son catalogue de matériaux. Le front peut donc
    changer de pôle sans exposer des articles appartenant à un autre lot.
    """
    required = {"niveau", "occupation", "numero"}
    if not required.issubset(frame.columns):
        return []
    working = frame.copy()
    for column in ("niveau", "occupation", "numero", "piece", "materiel"):
        if column not in working.columns:
            working[column] = ""
        working[column] = working[column].fillna("").astype(str).str.strip()
    working["scope_id"] = working.apply(
        lambda row: excel_scope_id(row["niveau"], row["occupation"], row["numero"]), axis=1,
    )
    options: list[dict[str, Any]] = []
    for scope_id, group in working.groupby("scope_id", sort=False):
        first = group.iloc[0]
        materials = sorted({
            str(value).strip() for value in group["materiel"] if _valid_material_label(value)
        }, key=normalize_token)
        pieces = sorted({
            str(value).strip() for value in group["piece"] if str(value).strip()
        }, key=normalize_token)
        options.append({
            "id": str(scope_id),
            "value": str(scope_id),
            "label": excel_scope_label(first["niveau"], first["occupation"], first["numero"]),
            "niveau": str(first["niveau"]),
            "occupation": str(first["occupation"]),
            "numero": str(first["numero"]),
            "pieces": pieces,
            "piece_count": len(pieces),
            "materiels": materials,
        })
    return sorted(options, key=lambda item: (
        normalize_token(item["niveau"]), normalize_token(item["occupation"]),
        normalize_token(item["numero"]),
    ))


def resolve_excel_scope(target: Any, options: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Résout un identifiant de scope, avec compatibilité pour les anciens libellés."""
    if isinstance(target, dict):
        target = target.get("id") or target.get("value") or target.get("label")
    value = str(target or "").strip()
    if not value:
        return None
    items = list(options)
    by_id = {str(item.get("id") or ""): item for item in items}
    if value in by_id:
        return by_id[value]
    normalized = normalize_token(value)
    for field in ("label", "occupation"):
        candidates = [item for item in items if normalize_token(item.get(field)) == normalized]
        if len(candidates) == 1:
            return candidates[0]
    return None


def scope_for_room_option(room: dict[str, Any]) -> str:
    return str(room.get("scope_id") or excel_scope_id(
        room.get("niveau"), room.get("occupation"), room.get("numero")
    ))


def infer_excel_scope(
    scope_options: Iterable[dict[str, Any]],
    room_options: Iterable[dict[str, Any]] = (),
    room_targets: Iterable[Any] = (),
    hints: Iterable[Any] = (),
) -> tuple[dict[str, Any] | None, str]:
    """Infère un scope seulement lorsqu'un choix unique est démontrable.

    Les relations de salles sauvegardées sont prioritaires. À défaut, les mots
    distinctifs du nom du PDF peuvent sélectionner un pôle (``CABINET
    VASCULAIRE`` -> ``VASCULAIRE ANGIO``). Une égalité reste volontairement
    non résolue afin d'éviter de mélanger des lots.
    """
    scopes = list(scope_options)
    rooms = list(room_options)
    if len(scopes) == 1:
        return scopes[0], "seul pôle du niveau"
    target_scope_ids: set[str] = set()
    for target in room_targets:
        resolved_room = resolve_excel_room(target, rooms)
        if resolved_room is not None:
            target_scope_ids.add(scope_for_room_option(resolved_room))
    if len(target_scope_ids) == 1:
        scope = resolve_excel_scope(next(iter(target_scope_ids)), scopes)
        if scope is not None:
            return scope, "relations sauvegardées"

    generic = {
        "cabinet", "plan", "plans", "projet", "pole", "local", "locaux",
        "niveau", "etage", "indice", "ind", "fiche", "ftm", "travaux",
        "modificative", "modificatif", "maison", "medicale", "medical",
    }
    hint_tokens: set[str] = set()
    for hint in hints:
        hint_tokens.update(
            token for token in normalize_token(hint).split()
            if len(token) >= 4 and token not in generic and not token.isdigit()
        )
    if not hint_tokens:
        return None, ""

    scored: list[tuple[int, int, dict[str, Any]]] = []
    for scope in scopes:
        occupation_tokens = {
            token for token in normalize_token(scope.get("occupation")).split()
            if len(token) >= 4 and token not in generic
        }
        shared = hint_tokens & occupation_tokens
        if shared:
            # Le premier score privilégie le nombre de mots distinctifs ; le
            # second préfère le libellé dont la plus grande part est reconnue.
            scored.append((len(shared), round(1000 * len(shared) / max(1, len(occupation_tokens))), scope))
    if not scored:
        return None, ""
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best = scored[0]
    if len(scored) > 1 and scored[1][:2] == best[:2]:
        return None, ""
    return best[2], "nom du PDF"


def filter_excel_scope(frame, scope: Any):
    """Filtre un DataFrame sur un scope résolu, sans mutation de l'original."""
    scope_id = str(scope.get("id") if isinstance(scope, dict) else scope or "").strip()
    if not scope_id:
        return frame
    mask = frame.apply(
        lambda row: excel_scope_id(
            row.get("niveau"), row.get("occupation"), row.get("numero")
        ) == scope_id,
        axis=1,
    )
    return frame.loc[mask].copy()


def excel_room_id(niveau: Any, occupation: Any, piece: Any, numero: Any) -> str:
    identity = "|".join(normalize_token(value) for value in (niveau, occupation, piece, numero))
    return f"excel-room-{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:16]}"


def excel_room_label(niveau: Any, occupation: Any, piece: Any, numero: Any) -> str:
    parts = []
    if str(occupation or "").strip():
        parts.append(str(occupation).strip())
    parts.append(str(piece or "Pièce sans nom").strip())
    if str(numero or "").strip():
        parts.append(f"n° {str(numero).strip()}")
    label = " · ".join(parts)
    if str(niveau or "").strip():
        label += f" ({str(niveau).strip()})"
    return label


def excel_room_options(frame) -> list[dict[str, Any]]:
    """Retourne chaque pièce physique avec son inventaire ``Avant``.

    Le catalogue par pièce permet au front d'afficher immédiatement la bonne
    quantité lorsqu'une salle PDF est associée, sans dépendre d'un comparatif
    calculé avant cette sélection.
    """
    identity_columns = ["niveau", "occupation", "piece", "numero"]
    if "piece" not in frame.columns:
        return []

    working = frame.copy()
    for column in identity_columns + ["categorie", "materiel"]:
        if column not in working.columns:
            working[column] = ""
        working[column] = working[column].fillna("").astype(str).str.strip()
    if "quantite" not in working.columns:
        working["quantite"] = 0
    working["quantite"] = working["quantite"].fillna(0)

    options: list[dict[str, Any]] = []
    for identity, group in working.groupby(identity_columns, sort=False, dropna=False):
        niveau, occupation, piece, numero = (str(value).strip() for value in identity)
        if not piece:
            continue
        materials: list[dict[str, Any]] = []
        usable = group[group["materiel"].map(_valid_material_label)]
        for (material, category), entries in usable.groupby(
            ["materiel", "categorie"], sort=False, dropna=False,
        ):
            materials.append({
                "name": str(material).strip(),
                "category": str(category).strip(),
                "quantity": int(entries["quantite"].sum()),
            })
        materials.sort(key=lambda item: (
            normalize_token(item["name"]), normalize_token(item["category"]),
        ))
        options.append({
            "id": excel_room_id(niveau, occupation, piece, numero),
            "scope_id": excel_scope_id(niveau, occupation, numero),
            "label": excel_room_label(niveau, occupation, piece, numero),
            "niveau": niveau,
            "occupation": occupation,
            "piece": piece,
            "numero": numero,
            "materiels": sorted({item["name"] for item in materials}, key=normalize_token),
            "materials": materials,
        })
    return sorted(options, key=lambda item: (
        normalize_token(item["niveau"]), normalize_token(item["occupation"]),
        normalize_token(item["piece"]), normalize_token(item["numero"]),
    ))


def resolve_excel_room(target: Any, options: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Résout une nouvelle clé ou une ancienne valeur lisible.

    Un ancien nom brut n'est accepté que s'il désigne une seule pièce physique ;
    ainsi ``Consultation`` ne peut plus agréger silencieusement douze locaux.
    """
    value = str(target or "").strip()
    if not value:
        return None
    items = list(options)
    by_id = {item["id"]: item for item in items}
    if value in by_id:
        return by_id[value]
    normalized = normalize_token(value)
    by_label = [item for item in items if normalize_token(item.get("label")) == normalized]
    if len(by_label) == 1:
        return by_label[0]
    by_piece = [item for item in items if normalize_token(item.get("piece")) == normalized]
    if len(by_piece) == 1:
        return by_piece[0]
    return None
