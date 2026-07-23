from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import analysis_store
from app.services.auth_service import (
    AuthService,
    AuthSessionStore,
    AuthUnavailable,
    InvalidOAuthResponse,
    UserIdentity,
)


def unsigned_token(payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    return f"e30.{encoded}.signature"


class AuthSessionStoreTests(unittest.TestCase):
    def test_state_is_single_use_and_session_cookie_is_opaque(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AuthSessionStore(Path(directory) / "auth.sqlite3", "test-secret")
            state, verifier, nonce = store.create_state(ttl_seconds=600, redirect_after="/history")

            saved = store.consume_state(state)
            self.assertIsNotNone(saved)
            self.assertEqual(saved["code_verifier"], verifier)
            self.assertEqual(saved["nonce"], nonce)
            self.assertEqual(saved["redirect_after"], "/history")
            self.assertIsNone(store.consume_state(state))

            user = UserIdentity("subject-a", "alice", "alice@example.test", "Alice")
            opaque = store.create_session(
                user,
                access_token="access-secret",
                refresh_token="refresh-secret",
                id_token="id-secret",
                ttl_seconds=600,
            )
            self.assertNotIn("alice", opaque)
            self.assertNotIn("subject-a", opaque)
            session = store.read_session(opaque)
            self.assertEqual(session["user_sub"], "subject-a")
            self.assertEqual(session["access_token"], "access-secret")
            store.delete_session(opaque)
            self.assertIsNone(store.read_session(opaque))

    def test_required_auth_with_missing_oidc_configuration_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = AuthService(
                db_path=Path(directory) / "auth.sqlite3",
                issuer_url="",
                client_id="",
                redirect_uri="",
                session_secret="",
                local_mode=False,
                auth_required=True,
            )
            self.assertEqual(service.mode, "unavailable")
            with self.assertRaises(AuthUnavailable):
                service.require_user(None)

    def test_production_confidential_client_requires_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = AuthService(
                db_path=Path(directory) / "auth.sqlite3",
                issuer_url="https://auth.example.test/realms/moduo",
                client_id="ftmgen",
                client_secret="",
                redirect_uri="https://ftm.example.test/api/auth/callback",
                session_secret="session-secret",
                local_mode=False,
                auth_required=True,
            )
            self.assertEqual(service.mode, "unavailable")
            self.assertIn("OIDC_CLIENT_SECRET", service.configuration_error)


class BrowserStateBindingTests(unittest.TestCase):
    def test_login_sets_http_only_binding_and_callback_rejects_other_state(self) -> None:
        class FakeOidcService:
            mode = "oidc"

            async def begin_login(self, _redirect_after: str):
                return "https://auth.example.test/authorize?state=browser-state", "browser-state"

            async def complete_login(self, **_kwargs):  # pragma: no cover - must not be called
                raise AssertionError("callback accepted an unbound state")

        with patch("app.main.auth_service.service", FakeOidcService()):
            client = TestClient(app)
            login = client.get("/api/auth/login", follow_redirects=False)
            self.assertEqual(login.status_code, 302)
            cookie = login.headers["set-cookie"].lower()
            self.assertIn("ftmgen_oidc_state=browser-state", cookie)
            self.assertIn("httponly", cookie)
            self.assertIn("samesite=lax", cookie)
            self.assertIn("path=/api/auth/callback", cookie)

            callback = client.get(
                "/api/auth/callback?code=code&state=attacker-state",
                follow_redirects=False,
            )
            self.assertEqual(callback.status_code, 400)
            self.assertIn("navigateur", callback.json()["detail"])
            self.assertIn("max-age=0", callback.headers["set-cookie"].lower())


class OidcFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_pkce_nonce_code_exchange_and_server_session(self) -> None:
        issuer = "https://auth.example.test/realms/moduo"
        observed: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/.well-known/openid-configuration"):
                return httpx.Response(200, json={
                    "issuer": issuer,
                    "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
                    "token_endpoint": f"{issuer}/protocol/openid-connect/token",
                    "userinfo_endpoint": f"{issuer}/protocol/openid-connect/userinfo",
                    "end_session_endpoint": f"{issuer}/protocol/openid-connect/logout",
                })
            if request.url.path.endswith("/token"):
                form = parse_qs(request.content.decode("utf-8"))
                self.assertEqual(form["grant_type"], ["authorization_code"])
                self.assertTrue(form["code_verifier"][0])
                token = unsigned_token({
                    "iss": issuer,
                    "aud": "ftmgen",
                    "sub": "user-42",
                    "nonce": observed["nonce"],
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 300,
                })
                return httpx.Response(200, json={
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "id_token": token,
                })
            if request.url.path.endswith("/userinfo"):
                self.assertEqual(request.headers["Authorization"], "Bearer access-token")
                return httpx.Response(200, json={
                    "sub": "user-42",
                    "preferred_username": "alice",
                    "email": "alice@example.test",
                    "name": "Alice",
                })
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as directory:
            service = AuthService(
                db_path=Path(directory) / "auth.sqlite3",
                issuer_url=issuer,
                client_id="ftmgen",
                client_secret="client-secret",
                redirect_uri="https://ftm.example.test/api/auth/callback",
                post_logout_redirect_uri="https://ftm.example.test/",
                session_secret="session-secret",
                local_mode=False,
                auth_required=True,
                transport=httpx.MockTransport(handler),
            )
            login_url, browser_state = await service.begin_login("/history")
            query = parse_qs(urlparse(login_url).query)
            self.assertEqual(browser_state, query["state"][0])
            observed["nonce"] = query["nonce"][0]
            self.assertEqual(query["code_challenge_method"], ["S256"])
            self.assertTrue(query["code_challenge"][0])
            self.assertEqual(query["response_type"], ["code"])

            opaque, identity, destination = await service.complete_login(
                code="authorization-code", state=query["state"][0]
            )
            self.assertEqual(identity.sub, "user-42")
            self.assertEqual(identity.username, "alice")
            self.assertEqual(destination, "/history")
            self.assertEqual(service.store.read_session(opaque)["user_sub"], "user-42")
            with self.assertRaises(InvalidOAuthResponse):
                await service.complete_login(code="replay", state=query["state"][0])


class AnalysisOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old_output = config.OUTPUT_DIR
        self.old_upload = config.UPLOAD_DIR
        config.OUTPUT_DIR = Path(self.temp.name) / "output"
        config.UPLOAD_DIR = config.OUTPUT_DIR / "uploads"
        config.OUTPUT_DIR.mkdir()
        config.UPLOAD_DIR.mkdir()

    def tearDown(self) -> None:
        config.OUTPUT_DIR = self.old_output
        config.UPLOAD_DIR = self.old_upload
        self.temp.cleanup()

    def _job(self, job: str, owner_sub: str | None, output_name: str) -> None:
        exported = config.OUTPUT_DIR / output_name
        exported.write_text(job, encoding="utf-8")
        data = {"job": job, "output": str(exported), "excel_name": f"{job}.xlsx"}
        if owner_sub:
            analysis_store.attach_owner(data, {"sub": owner_sub, "username": owner_sub})
        analysis_store.write_analysis(job, data)

    def test_history_detail_download_and_delete_are_isolated_by_subject(self) -> None:
        self._job("joba", "user-a", "FTM_comparatif_joba.xlsx")
        self._job("jobb", "user-b", "FTM_comparatif_jobb.xlsx")
        self._job("legacy", None, "FTM_comparatif_legacy.xlsx")

        visible = analysis_store.list_analyses(owner_sub="user-a")
        self.assertEqual([item["job"] for item in visible], ["joba"])
        with self.assertRaises(analysis_store.AnalysisNotFound):
            analysis_store.read_analysis("jobb", owner_sub="user-a")
        with self.assertRaises(analysis_store.AnalysisNotFound):
            analysis_store.analysis_for_output(
                "FTM_comparatif_jobb.xlsx", owner_sub="user-a"
            )
        with self.assertRaises(analysis_store.AnalysisNotFound):
            analysis_store.delete_analysis("jobb", owner_sub="user-a")
        self.assertTrue((config.OUTPUT_DIR / "analysis_jobb.json").exists())

        local_visible = analysis_store.list_analyses(
            owner_sub="local", allow_legacy=True
        )
        self.assertEqual([item["job"] for item in local_visible], ["legacy"])

    def test_recalculation_preserves_owner_and_ignores_payload_identity(self) -> None:
        previous = {"owner_sub": "user-a", "owner": {"sub": "user-a", "name": "Alice"}}
        recalculated = {"owner_sub": "attacker", "owner": {"sub": "attacker"}}
        analysis_store.preserve_owner(recalculated, previous)
        self.assertEqual(recalculated["owner_sub"], "user-a")
        self.assertEqual(recalculated["owner"]["name"], "Alice")

    def test_atomic_write_leaves_no_temporary_history_file(self) -> None:
        analysis_store.write_analysis("atomicjob", {"job": "atomicjob", "owner_sub": "user-a"})
        self.assertEqual(analysis_store.read_analysis("atomicjob")["owner_sub"], "user-a")
        self.assertEqual(list(config.OUTPUT_DIR.glob(".analysis_atomicjob.json.*.tmp")), [])

    def test_all_job_routes_return_404_for_another_user(self) -> None:
        self._job("privatejob", "user-b", "FTM_comparatif_privatejob.xlsx")

        class UserAService:
            mode = "oidc"

            def require_user(self, _request):
                return UserIdentity("user-a", "alice")

            def can_access_legacy(self, _user):
                return False

        routes = [
            ("get", "/api/history/privatejob", None),
            ("delete", "/api/history/privatejob", None),
            ("post", "/api/history/privatejob/corrections", {}),
            ("post", "/api/history/privatejob/ftm", {}),
            ("post", "/api/history/privatejob/corrections/draft", {}),
            ("get", "/api/jobs/privatejob/pdf", None),
            ("get", "/api/jobs/privatejob/pdf/pages/1.png", None),
            ("get", "/api/jobs/privatejob/pdf/pages/1/markers", None),
            ("get", "/api/download/FTM_comparatif_privatejob.xlsx", None),
        ]
        with patch("app.main.auth_service.service", UserAService()):
            client = TestClient(app)
            history = client.get("/api/history")
            self.assertEqual(history.status_code, 200)
            self.assertEqual(history.json()["analyses"], [])
            for method, url, body in routes:
                response = getattr(client, method)(url, json=body) if body is not None \
                    else getattr(client, method)(url)
                self.assertEqual(response.status_code, 404, f"{method.upper()} {url}")


if __name__ == "__main__":
    unittest.main()
