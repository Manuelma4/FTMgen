# -*- coding: utf-8 -*-
"""Configuration FTMgen — lit les clés LLM depuis etc/.env du projet."""
from pathlib import Path
import os
from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent          # C:\Projet WEB\FTMgen
DATA_DIR = BASE_DIR / "app" / "data"
OUTPUT_DIR = BASE_DIR / "output"
UPLOAD_DIR = OUTPUT_DIR / "uploads"

_ENV_CANDIDATES = [
    BASE_DIR.parent / "etc" / ".env",                      # C:\Projet WEB\etc\.env
    BASE_DIR / ".env",
]


def _load_env() -> dict:
    for p in _ENV_CANDIDATES:
        if p.exists():
            values = {k: v for k, v in dotenv_values(p).items() if v is not None}
            values.update(os.environ)
            return values
    return dict(os.environ)


_env = _load_env()

LIHA_CHAT_COMPLETIONS_URL = _env.get("LIHA_CHAT_COMPLETIONS_URL", "")
LIHA_CHAT_MODEL = _env.get("LIHA_CHAT_MODEL", "")
LIHA_CHAT_TOKEN = _env.get("LIHA_CHAT_TOKEN", "")
LIHA_TIMEOUT_SECONDS = float(_env.get("LIHA_CHAT_TIMEOUT_SECONDS", "15") or 15)
# le mapping de noms est un appel lourd (modèle raisonnant, service lent) :
# timeout dédié, même ordre de grandeur que LIHA_RULES_SUGGEST_TIMEOUT_SECONDS
LLM_MAPPING_TIMEOUT_SECONDS = float(_env.get("FTM_LLM_TIMEOUT", "600") or 600)

# Le LLM est optionnel : sans clé, le matching retombe sur le fuzzy matching local.
USE_LLM = bool(LIHA_CHAT_TOKEN and LIHA_CHAT_COMPLETIONS_URL) \
    and _env.get("FTM_USE_LLM", "false").strip().lower() in ("true", "1", "yes")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
