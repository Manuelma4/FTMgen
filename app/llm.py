# -*- coding: utf-8 -*-
"""Client LLM (endpoint LIHA compatible OpenAI) — utilisé uniquement pour
faire correspondre les libellés du plan PDF aux noms de matériel de la maquette.
Le comptage des symboles reste 100% algorithmique et vérifiable."""
import json
import re

import httpx

from . import config


def _chat(messages: list[dict], max_tokens: int = 4000, timeout: float | None = None) -> str:
    resp = httpx.post(
        config.LIHA_CHAT_COMPLETIONS_URL,
        headers={
            "Authorization": f"Bearer {config.LIHA_CHAT_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.LIHA_CHAT_MODEL,
            "messages": messages,
            "temperature": 0,
            # gpt-oss raisonne avant de répondre : budget large obligatoire
            "max_tokens": max_tokens,
        },
        timeout=timeout or config.LIHA_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _extract_json(text: str) -> dict:
    """Récupère le premier objet JSON dans une réponse LLM (avec ou sans ```json)."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def suggest_material_mapping(pdf_articles: list[str], excel_materials: list[str]) -> dict[str, str] | None:
    """Demande au LLM d'associer chaque article détecté sur le plan à un nom de
    matériel existant de la maquette. Retourne {article_pdf: materiel_excel}
    (articles sans équivalent omis), ou None si l'appel LLM a échoué."""
    if not config.USE_LLM or not pdf_articles or not excel_materials:
        return None
    prompt = (
        "Tu aides à rapprocher des libellés d'équipements d'un plan de travaux "
        "modificatifs (électricité / CVC / plomberie) avec les noms de matériel "
        "d'une maquette numérique de bâtiment.\n\n"
        "Libellés du plan PDF :\n"
        + "\n".join(f"- {a}" for a in pdf_articles)
        + "\n\nNoms de matériel de la maquette (Excel) :\n"
        + "\n".join(f"- {m}" for m in excel_materials)
        + "\n\nRéponds UNIQUEMENT avec un objet JSON qui associe chaque libellé du "
        "plan au nom EXACT (copié tel quel) d'un matériel de la maquette qui désigne "
        "le même équipement, ou null si aucun équivalent fiable n'existe. "
        "Ne rapproche que des équipements de la même famille technique — attention "
        "aux faux amis (ex. un « Vidéophone » n'est pas un « Vidoir »). "
        'Format : {"libellé plan": "nom matériel maquette" | null, ...}'
    )
    try:
        answer = _chat([{"role": "user", "content": prompt}],
                       max_tokens=8000, timeout=config.LLM_MAPPING_TIMEOUT_SECONDS)
    except Exception:
        return None
    mapping = _extract_json(answer)
    if not mapping:
        return None                     # réponse illisible = échec, ne pas mémoriser
    # ne garder que les valeurs qui existent vraiment côté Excel
    valid = set(excel_materials)
    return {k: v for k, v in mapping.items() if isinstance(v, str) and v in valid and k in set(pdf_articles)}
