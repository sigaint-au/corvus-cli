#!/usr/bin/python3
"""Self-check for secretserver CLI (no network)."""
from __future__ import annotations

import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
_loader = importlib.machinery.SourceFileLoader("secretserver", str(ROOT / "secretserver"))
SPEC = importlib.util.spec_from_loader(_loader.name, _loader)
ss = importlib.util.module_from_spec(SPEC)
_loader.exec_module(ss)

PID = "11111111-1111-1111-1111-111111111111"


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg_dir = Path(self.tmp.name) / ".config" / "secretserver"
        self.cfg_path = self.cfg_dir / "config"
        self.patches = [
            mock.patch.object(ss, "CONFIG_DIR", self.cfg_dir),
            mock.patch.object(ss, "CONFIG_PATH", self.cfg_path),
            mock.patch.dict(os.environ, {}, clear=False),
        ]
        for k in (ss.ENV_URL, ss.ENV_TOKEN, ss.ENV_PROJECT):
            os.environ.pop(k, None)
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_no_args_prints_usage(self):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            try:
                ss.main([])
            except SystemExit as e:
                self.assertEqual(e.code, 0)
        self.assertIn("get secrets", buf.getvalue())

    def test_login_writes_config(self):
        with mock.patch.object(ss, "_proj_api", return_value={"items": []}):
            ss.main(
                [
                    "login",
                    "--url",
                    "https://secrets.example/",
                    "--token",
                    "ss_test",
                    "--project",
                    PID,
                ]
            )
        text = self.cfg_path.read_text()
        self.assertIn("https://secrets.example", text)
        self.assertIn("ss_test", text)

    def test_get_secrets_list(self):
        calls = []

        def capture(method, path, *, body=None, query=None, project=None):
            calls.append((method, path, query))
            return {"items": [{"key": "K", "kind": "plain", "note": "n"}]}

        with mock.patch.object(ss, "_proj_api", side_effect=capture):
            ss._save_config("http://h", "ss_t", PID)
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                ss.main(["get", "secrets", "-l", "api"])
        self.assertEqual(calls[0][0], "GET")
        self.assertEqual(calls[0][2].get("q"), "api")
        self.assertIn("KEY", buf.getvalue())

    def test_get_secret_value_only(self):
        with mock.patch.object(
            ss, "_proj_api", return_value={"key": "K", "value": "s3cret"}
        ):
            ss._save_config("http://h", "ss_t", PID)
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                ss.main(["get", "secret", "K", "-o", "value"])
        self.assertEqual(buf.getvalue(), "s3cret\n")

    def test_apply_from_env(self):
        calls = []

        def capture(method, path, *, body=None, query=None, project=None):
            calls.append((method, path, body))
            return {"ok": True, "key": "K", "value": body["value"]}

        os.environ["SS_TEST_VAL"] = "from-env-secret"
        with mock.patch.object(ss, "_proj_api", side_effect=capture):
            ss._save_config("http://h", "ss_t", PID)
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                ss.main(["apply", "secret", "K", "--from-env", "SS_TEST_VAL"])
        self.assertEqual(calls[0][0], "PUT")
        self.assertEqual(calls[0][2]["value"], "from-env-secret")
        self.assertNotIn("from-env-secret", buf.getvalue())

    def test_delete_secret(self):
        with mock.patch.object(ss, "_proj_api", return_value={"ok": True}) as m:
            ss._save_config("http://h", "ss_t", PID)
            ss.main(["delete", "secret", "db/pass"])
        self.assertEqual(m.call_args[0][0], "DELETE")

    def test_project_switch(self):
        ss._save_config("http://h", "ss_t", PID)
        with mock.patch.object(ss, "_proj_api", return_value={"items": []}):
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                ss.main(["project", "22222222-2222-2222-2222-222222222222"])
        self.assertIn("22222222-2222-2222-2222-222222222222", self.cfg_path.read_text())

    def test_reveal_submits_access_request_when_approval_required(self):
        """reveal secret posts /access-request after GET returns approval_required."""
        calls = []

        def capture(method, path, *, body=None, query=None, project=None):
            calls.append((method, path, body))
            if method == "GET":
                raise SystemExit(
                    'HTTP 403: {"error":"approval_required","message":"Reveal requires approval"}'
                )
            return {"ok": True, "message": "Access request submitted."}

        ss._save_config("http://h", "pat_test", PID)
        buf = io.StringIO()
        with mock.patch.object(ss, "_proj_api", side_effect=capture):
            with mock.patch("sys.stdout", buf):
                ss.main(
                    [
                        "reveal",
                        "secret",
                        "prod_root",
                        "--reason",
                        "debug #1234",
                    ]
                )
        self.assertEqual(calls[0][0], "GET")
        self.assertIn("/secrets/prod_root", calls[0][1])
        self.assertEqual(calls[1][0], "POST")
        self.assertIn("/secrets/prod_root/access-request", calls[1][1])
        self.assertEqual(calls[1][2], {"reason": "debug #1234"})
        self.assertIn("Access request submitted", buf.getvalue())

    def test_reveal_already_allowed_prints_hint(self):
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(
            ss, "_proj_api", return_value={"key": "K", "value": "v"}
        ):
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                ss.main(["reveal", "secret", "K"])
        self.assertIn("already reveal", buf.getvalue())

    def test_get_requests_lists_pending(self):
        rid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        payload = {
            "items": [
                {
                    "id": rid,
                    "email": "u@ex.com",
                    "secret_key": "prod_root",
                    "reason": "debug",
                    "status": "pending",
                }
            ]
        }
        calls = []

        def capture(method, path, *, body=None, query=None, project=None):
            calls.append((method, path, query))
            return payload

        ss._save_config("http://h", "pat_test", PID)
        buf = io.StringIO()
        with mock.patch.object(ss, "_proj_api", side_effect=capture):
            with mock.patch("sys.stdout", buf):
                ss.main(["get", "requests"])
        self.assertEqual(calls[0][0], "GET")
        self.assertEqual(calls[0][1], "/access-requests")
        self.assertEqual(calls[0][2].get("status"), "pending")
        out = buf.getvalue()
        self.assertIn(rid, out)
        self.assertIn("prod_root", out)
        self.assertIn("u@ex.com", out)

    def test_approve_request_posts_minutes(self):
        rid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        calls = []

        def capture(method, path, *, body=None, query=None, project=None):
            calls.append((method, path, body))
            return {"ok": True, "message": f"Approved {rid}"}

        ss._save_config("http://h", "pat_admin", PID)
        buf = io.StringIO()
        with mock.patch.object(ss, "_proj_api", side_effect=capture):
            with mock.patch("sys.stdout", buf):
                ss.main(["approve", rid, "--minutes", "60"])
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], f"/access-requests/{rid}/approve")
        self.assertEqual(calls[0][2], {"minutes": 60})
        self.assertIn("Approved", buf.getvalue())

    def test_approve_rejects_invalid_minutes(self):
        ss._save_config("http://h", "pat_admin", PID)
        with self.assertRaises(SystemExit) as cm:
            ss.main(["approve", "rid", "--minutes", "99"])
        self.assertIn("minutes", str(cm.exception).lower())

    def test_deny_request(self):
        rid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        calls = []

        def capture(method, path, *, body=None, query=None, project=None):
            calls.append((method, path, body))
            return {"ok": True, "message": f"Denied {rid}"}

        ss._save_config("http://h", "pat_admin", PID)
        buf = io.StringIO()
        with mock.patch.object(ss, "_proj_api", side_effect=capture):
            with mock.patch("sys.stdout", buf):
                ss.main(["deny", rid])
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], f"/access-requests/{rid}/deny")
        self.assertIn("Denied", buf.getvalue())

    def test_get_secret_approval_hint(self):
        def boom(method, path, *, body=None, query=None, project=None):
            raise SystemExit(
                'HTTP 403: {"error":"approval_required","message":"need approval"}'
            )

        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_proj_api", side_effect=boom):
            with self.assertRaises(SystemExit) as cm:
                ss.main(["get", "secret", "locked_key"])
        msg = str(cm.exception)
        self.assertIn("reveal secret locked_key", msg)

    # ── management API (/api/v1/manage) ─────────────────────────────

    def _capture(self, calls):
        def capture(method, path, *, body=None, query=None, project=None):
            calls.append((method, path, body, query))
            return {"ok": True}

        return capture

    def test_get_teams_hits_manage(self):
        calls = []
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_api", side_effect=self._capture(calls)):
            ss.main(["get", "teams", "-l", "platform"])
        self.assertEqual(calls[0][0], "GET")
        self.assertEqual(calls[0][1], "/teams")
        self.assertEqual(calls[0][3].get("q"), "platform")

    def test_get_project_detail_hits_manage(self):
        calls = []
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_api", side_effect=self._capture(calls)):
            ss.main(["get", "project", "ios-app"])
        self.assertEqual(calls[0][0], "GET")
        self.assertEqual(calls[0][1], "/projects/ios-app")

    def test_get_tokens_hits_manage_project(self):
        calls = []
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(calls)):
            ss.main(["get", "tokens"])
        self.assertEqual(calls[0][0], "GET")
        self.assertEqual(calls[0][1], "/tokens")

    def test_get_audit_source_org_hits_admin_manage(self):
        calls = []
        ss._save_config("http://h", "pat_admin", PID)
        with mock.patch.object(ss, "_mgmt_api", side_effect=self._capture(calls)):
            ss.main(["get", "audit", "--source", "org"])
        self.assertEqual(calls[0][0], "GET")
        self.assertEqual(calls[0][1], "/admin/audit")
        self.assertEqual(calls[0][3].get("source"), "org")

    def test_get_users_hits_admin_manage(self):
        calls = []
        ss._save_config("http://h", "pat_admin", PID)
        with mock.patch.object(ss, "_mgmt_api", side_effect=self._capture(calls)):
            ss.main(["get", "users", "-l", "alice"])
        self.assertEqual(calls[0][1], "/admin/users")
        self.assertEqual(calls[0][3].get("q"), "alice")

    def test_create_token_uses_service_role(self):
        calls = []
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(calls)):
            ss.main(["create", "token", "ci"])
        self.assertEqual(calls[0][1], "/tokens")
        self.assertEqual(calls[0][2]["role"], "service-reveal")

    def test_create_token_service_write_role(self):
        calls = []
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(calls)):
            ss.main(["create", "token", "ci", "--role", "service-write"])
        self.assertEqual(calls[0][2]["role"], "service-write")

    def test_create_team_member_uses_team_role(self):
        calls = []
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_api", side_effect=self._capture(calls)):
            ss.main(["create", "member", "bob@x.com", "--team", "Platform"])
        self.assertEqual(calls[0][1], "/teams/Platform/members")
        body = calls[0][2]
        self.assertEqual(body["email"], "bob@x.com")
        self.assertEqual(body["role"], "team-member")

    def test_create_project_member_uses_project_role(self):
        calls = []
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(calls)):
            ss.main(["create", "member", "dave@x.com", "--role", "project-write"])
        self.assertEqual(calls[0][2]["role"], "project-write")

    def test_create_rejects_invalid_role(self):
        ss._save_config("http://h", "pat_test", PID)
        with self.assertRaises(SystemExit) as cm:
            ss.main(["create", "token", "ci", "--role", "write"])
        self.assertIn("service-read", str(cm.exception))

    def test_delete_team_hits_manage(self):
        calls = []
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_api", side_effect=self._capture(calls)):
            ss.main(["delete", "team", "MyTeam"])
        self.assertEqual(calls[0][0], "DELETE")
        self.assertEqual(calls[0][1], "/teams/MyTeam")

    def test_restore_trash_hits_manage_project(self):
        calls = []
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(calls)):
            ss.main(["restore", "trash", "sec-id"])
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], "/trash/sec-id/restore")

    # ── token scope ─────────────────────────────────────────────────

    def test_create_token_scope_passed(self):
        calls = []
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(calls)):
            with mock.patch("sys.stderr", io.StringIO()):
                ss.main(["create", "token", "ci", "--scope", "API_KEY, prod/*"])
        self.assertEqual(calls[0][1], "/tokens")
        self.assertEqual(calls[0][2]["scope"], ["API_KEY", "prod/*"])

    def test_create_token_no_scope_omits_field(self):
        calls = []
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(calls)):
            with mock.patch("sys.stderr", io.StringIO()):
                ss.main(["create", "token", "ci"])
        self.assertNotIn("scope", calls[0][2])

    def test_get_tokens_shows_scope(self):
        payload = {
            "items": [
                {
                    "name": "ci",
                    "token_prefix": "ss_abc",
                    "role": "service-write",
                    "expires_at": None,
                    "id": "x",
                    "scope": [{"secret_key": "API_KEY"}, {"key_pattern": "prod/*"}],
                }
            ]
        }
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", return_value=payload):
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                ss.main(["get", "tokens"])
        self.assertIn("SCOPE", buf.getvalue())
        self.assertIn("API_KEY,prod/*", buf.getvalue())

    # ── secret metadata ─────────────────────────────────────────────

    def test_apply_meta_posts_to_manage(self):
        calls = []
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(calls)):
            ss.main(["apply", "secret", "K", "--meta", "owner=team"])
        self.assertEqual(calls[0][0], "PATCH")
        self.assertEqual(calls[0][1], "/secrets/K/meta")
        self.assertEqual(calls[0][2], {"key": "owner", "value": "team"})

    def test_apply_meta_delete_posts_to_manage(self):
        calls = []
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(calls)):
            ss.main(["apply", "secret", "K", "--delete-meta", "owner"])
        self.assertEqual(calls[0][0], "DELETE")
        self.assertEqual(calls[0][1], "/secrets/K/meta/owner")

    def test_apply_meta_and_value_writes_both(self):
        value_calls, meta_calls = [], []

        def cap_proj(method, path, *, body=None, query=None, project=None):
            value_calls.append((method, path, body))
            return {"ok": True, "key": "K", "value": "v"}

        os.environ["SS_TEST"] = "x"
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_proj_api", side_effect=cap_proj), \
             mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(meta_calls)):
            ss.main(["apply", "secret", "K", "--from-env=SS_TEST", "--meta", "a=b"])
        self.assertEqual(value_calls[0][0], "PUT")
        self.assertEqual(meta_calls[0][1], "/secrets/K/meta")

    def test_apply_meta_requires_pat(self):
        ss._save_config("http://h", "ss_machine", PID)
        with self.assertRaises(SystemExit) as cm:
            ss.main(["apply", "secret", "K", "--meta", "a=b"])
        self.assertIn("pat_", str(cm.exception))

    def test_apply_meta_requires_key_equals_value(self):
        ss._save_config("http://h", "pat_test", PID)
        with self.assertRaises(SystemExit) as cm:
            ss.main(["apply", "secret", "K", "--meta", "barekey"])
        self.assertIn("KEY=VALUE", str(cm.exception))

    # ── new-management commands (bindings / export / settings / groups) ──

    def test_apply_access_mode_patches_manage(self):
        calls = []
        ss._save_config("http://h", "pat_admin", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(calls)):
            ss.main(["apply", "secret", "K", "--access-mode", "restricted",
                     "--requires-approval", "on"])
        self.assertEqual(calls[0][0], "PATCH")
        self.assertEqual(calls[0][1], "/secrets/K")
        self.assertEqual(calls[0][2],
                         {"access_mode": "restricted", "requires_approval": True})

    def test_apply_access_mode_requires_pat(self):
        ss._save_config("http://h", "ss_machine", PID)
        with self.assertRaises(SystemExit) as cm:
            ss.main(["apply", "secret", "K", "--access-mode", "restricted"])
        self.assertIn("pat_", str(cm.exception))

    def test_grant_secret_binding(self):
        calls = []
        ss._save_config("http://h", "pat_admin", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(calls)):
            ss.main(["grant", "secret", "K", "--to", "bob@x.com", "--role", "secret-reveal"])
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], "/secrets/K/bindings")
        self.assertEqual(calls[0][2]["subject_kind"], "User")
        self.assertEqual(calls[0][2]["subject_id"], "bob@x.com")

    def test_unbind_secret_binding(self):
        calls = []
        ss._save_config("http://h", "pat_admin", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(calls)):
            ss.main(["unbind", "secret", "K", "bid123"])
        self.assertEqual(calls[0][0], "DELETE")
        self.assertEqual(calls[0][1], "/secrets/K/bindings/bid123")

    def test_export_env(self):
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(
            ss, "_mgmt_proj_api",
            return_value={"mode": "plain", "items": [{"key": "A", "value": "v1", "note": ""}]},
        ):
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                ss.main(["export", "-o", "env"])
        self.assertIn("A=v1", buf.getvalue())

    def test_settings_patches_project(self):
        calls = []
        ss._save_config("http://h", "pat_admin", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(calls)):
            ss.main(["settings", "--require-reveal-approval", "on",
                     "--default-access-mode", "inherit"])
        self.assertEqual(calls[0][0], "PATCH")
        self.assertEqual(calls[0][1], "")
        self.assertEqual(calls[0][2]["require_reveal_approval"], True)

    def test_group_member_add(self):
        calls = []
        ss._save_config("http://h", "pat_admin", PID)
        with mock.patch.object(ss, "_mgmt_api", side_effect=self._capture(calls)):
            ss.main(["create", "group-member", "bob@x.com", "--team", "T", "--group", "admins"])
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], "/teams/T/groups/admins/members")
        self.assertEqual(calls[0][2]["email"], "bob@x.com")

    def test_restore_trash_all(self):
        calls = []
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(calls)):
            ss.main(["restore", "trash", "--all"])
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], "/trash/restore")
        self.assertEqual(calls[0][2]["action"], "restore")

    def test_delete_trash_all_purges(self):
        calls = []
        ss._save_config("http://h", "pat_test", PID)
        with mock.patch.object(ss, "_mgmt_proj_api", side_effect=self._capture(calls)):
            ss.main(["delete", "trash", "--all"])
        self.assertEqual(calls[0][2]["action"], "purge")


if __name__ == "__main__":
    unittest.main()
