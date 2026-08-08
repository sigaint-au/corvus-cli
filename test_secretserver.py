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


if __name__ == "__main__":
    unittest.main()
