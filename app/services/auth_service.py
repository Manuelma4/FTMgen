# -*- coding: utf-8 -*-
"""Authentification OIDC et sessions opaques persistantes pour FTMgen."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode

import httpx
from fastapi import Request

from .. import config


class AuthUnavailable(RuntimeError):
    """La configuration impose l'authentification mais elle est incomplete."""


class AuthenticationRequired(PermissionError):
    """La requete ne porte pas de session valide."""


class InvalidOAuthResponse(ValueError):
    """Le retour du fournisseur OIDC ne peut pas etre accepte."""


@dataclass(frozen=True)
class UserIdentity:
    sub: str
    username: str = ""
    email: str = ""
    name: str = ""
    is_local: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "sub": self.sub,
            "username": self.username,
            "email": self.email,
            "name": self.name,
        }

    @classmethod
    def from_snapshot(cls, value: dict[str, Any], *, is_local: bool = False) -> "UserIdentity":
        sub = str(value.get("sub") or "").strip()
        if not sub:
            raise ValueError("Identite sans subject")
        return cls(
            sub=sub,
            username=str(value.get("username") or value.get("preferred_username") or "").strip(),
            email=str(value.get("email") or "").strip(),
            name=str(value.get("name") or "").strip(),
            is_local=is_local,
        )


def _safe_redirect_path(value: str | None) -> str:
    value = str(value or "/").strip()
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) != 3:
        raise InvalidOAuthResponse("ID token invalide")
    try:
        raw = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidOAuthResponse("ID token illisible") from exc
    if not isinstance(payload, dict):
        raise InvalidOAuthResponse("Claims OIDC invalides")
    return payload


class AuthSessionStore:
    """SQLite local: etats OIDC a usage unique et sessions cote serveur."""

    def __init__(self, path: Path | str, secret: str):
        self.path = Path(path)
        self.secret = str(secret or "local-session-secret").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        if os.name != "nt":
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS oidc_states (
                    state_hash TEXT PRIMARY KEY,
                    code_verifier TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    redirect_after TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    session_hash TEXT PRIMARY KEY,
                    user_sub TEXT NOT NULL,
                    user_json TEXT NOT NULL,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    id_token TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_sub)"
            )

    def _digest(self, value: str) -> str:
        return hmac.new(self.secret, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def create_state(self, *, ttl_seconds: int, redirect_after: str = "/") -> tuple[str, str, str]:
        now = int(time.time())
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        with self._connect() as connection:
            connection.execute("DELETE FROM oidc_states WHERE expires_at <= ?", (now,))
            connection.execute(
                """INSERT INTO oidc_states
                   (state_hash, code_verifier, nonce, redirect_after, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    self._digest(state), verifier, nonce, _safe_redirect_path(redirect_after),
                    now, now + max(60, int(ttl_seconds)),
                ),
            )
        return state, verifier, nonce

    def consume_state(self, state: str) -> dict[str, Any] | None:
        now = int(time.time())
        state_hash = self._digest(str(state or ""))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM oidc_states WHERE state_hash = ? AND expires_at > ?",
                (state_hash, now),
            ).fetchone()
            connection.execute("DELETE FROM oidc_states WHERE state_hash = ?", (state_hash,))
        return dict(row) if row is not None else None

    def create_session(
        self,
        user: UserIdentity,
        *,
        access_token: str,
        refresh_token: str,
        id_token: str,
        ttl_seconds: int,
    ) -> str:
        now = int(time.time())
        opaque_id = secrets.token_urlsafe(48)
        with self._connect() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
            connection.execute(
                """INSERT INTO auth_sessions
                   (session_hash, user_sub, user_json, access_token, refresh_token, id_token,
                    created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self._digest(opaque_id), user.sub,
                    json.dumps(user.snapshot(), ensure_ascii=False),
                    str(access_token or ""), str(refresh_token or ""), str(id_token or ""),
                    now, now + max(60, int(ttl_seconds)),
                ),
            )
        return opaque_id

    def read_session(self, opaque_id: str | None) -> dict[str, Any] | None:
        if not opaque_id:
            return None
        now = int(time.time())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM auth_sessions WHERE session_hash = ? AND expires_at > ?",
                (self._digest(opaque_id), now),
            ).fetchone()
            if row is None:
                connection.execute(
                    "DELETE FROM auth_sessions WHERE session_hash = ?", (self._digest(opaque_id),)
                )
                return None
        result = dict(row)
        result["user"] = json.loads(result.pop("user_json"))
        return result

    def delete_session(self, opaque_id: str | None) -> dict[str, Any] | None:
        if not opaque_id:
            return None
        session_hash = self._digest(opaque_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM auth_sessions WHERE session_hash = ?", (session_hash,)
            ).fetchone()
            connection.execute("DELETE FROM auth_sessions WHERE session_hash = ?", (session_hash,))
        if row is None:
            return None
        result = dict(row)
        result["user"] = json.loads(result.pop("user_json"))
        return result


class AuthService:
    def __init__(
        self,
        *,
        db_path: Path | str = config.FTM_AUTH_DB_PATH,
        issuer_url: str = config.OIDC_ISSUER_URL,
        client_id: str = config.OIDC_CLIENT_ID,
        client_secret: str = config.OIDC_CLIENT_SECRET,
        redirect_uri: str = config.OIDC_REDIRECT_URI,
        post_logout_redirect_uri: str = config.OIDC_POST_LOGOUT_REDIRECT_URI,
        session_secret: str = config.SESSION_SECRET,
        local_mode: bool = config.FTM_LOCAL_MODE,
        auth_required: bool = config.FTM_AUTH_REQUIRED,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.issuer_url = str(issuer_url or "").strip().rstrip("/")
        self.client_id = str(client_id or "").strip()
        self.client_secret = str(client_secret or "").strip()
        self.redirect_uri = str(redirect_uri or "").strip()
        self.post_logout_redirect_uri = str(post_logout_redirect_uri or "").strip()
        self.local_mode = bool(local_mode)
        self.auth_required = bool(auth_required)
        self.transport = transport
        self._metadata: dict[str, Any] | None = None
        self._metadata_lock = asyncio.Lock()
        supplied = any((self.issuer_url, self.client_id, self.client_secret, self.redirect_uri))
        confidential_required = self.auth_required or not self.local_mode
        complete = all((self.issuer_url, self.client_id, self.redirect_uri, session_secret)) \
            and (bool(self.client_secret) or not confidential_required)
        self.mode = "oidc" if complete else "local"
        self.configuration_error = ""
        if not complete and (self.auth_required or not self.local_mode or supplied):
            self.mode = "unavailable"
            self.configuration_error = (
                "Configuration OIDC incomplete: OIDC_ISSUER_URL, OIDC_CLIENT_ID, "
                "OIDC_REDIRECT_URI et SESSION_SECRET sont obligatoires; "
                "OIDC_CLIENT_SECRET l'est en production"
            )
        if complete and not self.local_mode and (
            not self.issuer_url.startswith("https://")
            or not self.redirect_uri.startswith("https://")
        ):
            self.mode = "unavailable"
            self.configuration_error = "OIDC exige des URL HTTPS hors environnement local"
        self.store = AuthSessionStore(db_path, session_secret or "ftmgen-local-session")

    def local_identity(self) -> UserIdentity:
        return UserIdentity(
            sub=config.FTM_LOCAL_USER_SUB,
            username=config.FTM_LOCAL_USER_NAME,
            email=config.FTM_LOCAL_USER_EMAIL,
            name=config.FTM_LOCAL_USER_NAME,
            is_local=True,
        )

    def optional_user(self, request: Request | None) -> UserIdentity | None:
        if self.mode == "unavailable":
            raise AuthUnavailable(self.configuration_error)
        if self.mode == "local":
            return self.local_identity()
        opaque_id = request.cookies.get(config.FTM_SESSION_COOKIE_NAME) if request is not None else None
        session = self.store.read_session(opaque_id)
        if session is None:
            return None
        return UserIdentity.from_snapshot(session["user"])

    def require_user(self, request: Request | None) -> UserIdentity:
        user = self.optional_user(request)
        if user is None:
            raise AuthenticationRequired("Authentification requise")
        return user

    def can_access_legacy(self, user: UserIdentity) -> bool:
        return user.is_local or bool(config.FTM_LEGACY_OWNER_SUB and user.sub == config.FTM_LEGACY_OWNER_SUB)

    async def discovery(self) -> dict[str, Any]:
        if self.mode != "oidc":
            raise AuthUnavailable(self.configuration_error or "OIDC n'est pas active")
        if self._metadata is not None:
            return self._metadata
        async with self._metadata_lock:
            if self._metadata is not None:
                return self._metadata
            url = f"{self.issuer_url}/.well-known/openid-configuration"
            async with httpx.AsyncClient(
                timeout=config.OIDC_HTTP_TIMEOUT_SECONDS, transport=self.transport
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                metadata = response.json()
            if str(metadata.get("issuer") or "").rstrip("/") != self.issuer_url:
                raise AuthUnavailable("L'issuer OIDC publie ne correspond pas a la configuration")
            for field in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
                if not str(metadata.get(field) or "").startswith("https://") and not self.local_mode:
                    raise AuthUnavailable(f"Endpoint OIDC non securise ou absent: {field}")
                if not metadata.get(field):
                    raise AuthUnavailable(f"Endpoint OIDC absent: {field}")
            self._metadata = metadata
            return metadata

    async def begin_login(self, redirect_after: str = "/") -> tuple[str, str]:
        metadata = await self.discovery()
        state, verifier, nonce = self.store.create_state(
            ttl_seconds=config.FTM_OIDC_STATE_TTL_SECONDS,
            redirect_after=redirect_after,
        )
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        query = urlencode({
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": config.OIDC_SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        return f"{metadata['authorization_endpoint']}?{query}", state

    def _validate_id_claims(self, id_token: str, expected_nonce: str) -> dict[str, Any]:
        claims = _decode_jwt_payload(id_token)
        now = int(time.time())
        if str(claims.get("iss") or "").rstrip("/") != self.issuer_url:
            raise InvalidOAuthResponse("Issuer de l'ID token invalide")
        audience = claims.get("aud") or []
        audiences = [audience] if isinstance(audience, str) else list(audience)
        if self.client_id not in audiences:
            raise InvalidOAuthResponse("Audience de l'ID token invalide")
        if int(claims.get("exp") or 0) <= now:
            raise InvalidOAuthResponse("ID token expire")
        if int(claims.get("iat") or now) > now + 60:
            raise InvalidOAuthResponse("ID token emis dans le futur")
        if not hmac.compare_digest(str(claims.get("nonce") or ""), expected_nonce):
            raise InvalidOAuthResponse("Nonce OIDC invalide")
        if not str(claims.get("sub") or "").strip():
            raise InvalidOAuthResponse("ID token sans subject")
        return claims

    async def complete_login(self, *, code: str, state: str) -> tuple[str, UserIdentity, str]:
        saved_state = self.store.consume_state(state)
        if saved_state is None:
            raise InvalidOAuthResponse("Etat OIDC invalide, expire ou deja utilise")
        metadata = await self.discovery()
        form = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "code": str(code or ""),
            "redirect_uri": self.redirect_uri,
            "code_verifier": saved_state["code_verifier"],
        }
        if self.client_secret:
            form["client_secret"] = self.client_secret
        async with httpx.AsyncClient(
            timeout=config.OIDC_HTTP_TIMEOUT_SECONDS, transport=self.transport
        ) as client:
            token_response = await client.post(metadata["token_endpoint"], data=form)
            token_response.raise_for_status()
            tokens = token_response.json()
            access_token = str(tokens.get("access_token") or "")
            id_token = str(tokens.get("id_token") or "")
            if not access_token or not id_token:
                raise InvalidOAuthResponse("Reponse token OIDC incomplete")
            claims = self._validate_id_claims(id_token, saved_state["nonce"])
            user_response = await client.get(
                metadata["userinfo_endpoint"],
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_response.raise_for_status()
            userinfo = user_response.json()
        if str(userinfo.get("sub") or "") != str(claims.get("sub") or ""):
            raise InvalidOAuthResponse("Le subject userinfo differe de l'ID token")
        identity = UserIdentity.from_snapshot({
            **claims,
            **userinfo,
            "username": userinfo.get("preferred_username") or claims.get("preferred_username"),
        })
        opaque_id = self.store.create_session(
            identity,
            access_token=access_token,
            refresh_token=str(tokens.get("refresh_token") or ""),
            id_token=id_token,
            ttl_seconds=config.FTM_SESSION_TTL_SECONDS,
        )
        return opaque_id, identity, _safe_redirect_path(saved_state["redirect_after"])

    async def logout_url(self, request: Request | None) -> str:
        redirect_uri = self.post_logout_redirect_uri or "/"
        if self.mode != "oidc":
            return redirect_uri
        opaque_id = request.cookies.get(config.FTM_SESSION_COOKIE_NAME) if request is not None else None
        session = self.store.delete_session(opaque_id)
        metadata = await self.discovery()
        endpoint = str(metadata.get("end_session_endpoint") or "")
        if not endpoint:
            return redirect_uri
        params: dict[str, str] = {
            "client_id": self.client_id,
            "post_logout_redirect_uri": redirect_uri,
        }
        if session and session.get("id_token"):
            params["id_token_hint"] = str(session["id_token"])
        return f"{endpoint}?{urlencode(params)}"


service = AuthService()
