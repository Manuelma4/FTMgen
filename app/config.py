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


def _env_bool(name: str, default: bool = False) -> bool:
    value = str(_env.get(name, "") or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}

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

# Authentification OIDC. Sans configuration, le mode non authentifie n'est
# autorise qu'en environnement local afin de conserver une installation de
# developpement simple tout en echouant de maniere fermee en production.
FTM_ENVIRONMENT = str(_env.get("FTM_ENVIRONMENT", "local") or "local").strip().lower()
FTM_LOCAL_MODE = FTM_ENVIRONMENT in {"local", "dev", "development", "test"}
FTM_AUTH_REQUIRED = _env_bool("FTM_AUTH_REQUIRED", False)

OIDC_ISSUER_URL = str(
    _env.get("OIDC_ISSUER_URL") or _env.get("OIDC_ISSUER") or ""
).strip().rstrip("/")
OIDC_CLIENT_ID = str(_env.get("OIDC_CLIENT_ID", "") or "").strip()
OIDC_CLIENT_SECRET = str(_env.get("OIDC_CLIENT_SECRET", "") or "").strip()
FTM_PUBLIC_URL = str(_env.get("FTM_PUBLIC_URL", "") or "").strip().rstrip("/")
OIDC_REDIRECT_URI = str(
    _env.get("OIDC_REDIRECT_URI")
    or (f"{FTM_PUBLIC_URL}/api/auth/callback" if FTM_PUBLIC_URL else "")
).strip()
OIDC_POST_LOGOUT_REDIRECT_URI = str(
    _env.get("OIDC_POST_LOGOUT_REDIRECT_URI")
    or (f"{FTM_PUBLIC_URL}/" if FTM_PUBLIC_URL else "")
).strip()
OIDC_SCOPES = str(_env.get("OIDC_SCOPES", "openid profile email") or "openid profile email").strip()
OIDC_HTTP_TIMEOUT_SECONDS = float(_env.get("OIDC_HTTP_TIMEOUT_SECONDS", "10") or 10)

SESSION_SECRET = str(_env.get("SESSION_SECRET", "") or "").strip()
FTM_SESSION_COOKIE_NAME = str(
    _env.get("FTM_SESSION_COOKIE_NAME", "ftmgen_session") or "ftmgen_session"
).strip()
FTM_OIDC_STATE_COOKIE_NAME = str(
    _env.get("FTM_OIDC_STATE_COOKIE_NAME", "ftmgen_oidc_state") or "ftmgen_oidc_state"
).strip()
FTM_SESSION_COOKIE_SECURE = _env_bool("FTM_SESSION_COOKIE_SECURE", not FTM_LOCAL_MODE)
FTM_SESSION_TTL_SECONDS = int(_env.get("FTM_SESSION_TTL_SECONDS", "28800") or 28800)
FTM_OIDC_STATE_TTL_SECONDS = int(_env.get("FTM_OIDC_STATE_TTL_SECONDS", "600") or 600)
FTM_AUTH_DB_PATH = Path(
    str(_env.get("FTM_AUTH_DB_PATH", OUTPUT_DIR / "auth.sqlite3") or OUTPUT_DIR / "auth.sqlite3")
)
FTM_LEGACY_OWNER_SUB = str(_env.get("FTM_LEGACY_OWNER_SUB", "") or "").strip()
FTM_LOCAL_USER_SUB = str(_env.get("FTM_LOCAL_USER_SUB", "local") or "local").strip()
FTM_LOCAL_USER_NAME = str(_env.get("FTM_LOCAL_USER_NAME", "Utilisateur local") or "Utilisateur local").strip()
FTM_LOCAL_USER_EMAIL = str(_env.get("FTM_LOCAL_USER_EMAIL", "") or "").strip()

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
