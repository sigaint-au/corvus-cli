#!/usr/bin/python3
"""Self-check for secretserver CLI (no network)."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
_loader = importlib.machinery.SourceFileLoader("secretserver", str(ROOT / "secretserver"))
SPEC = importlib.util.spec_from_loader(_loader.name, _loader)
ss = importlib.util.module_from_spec(SPEC)
_loader.exec_module(ss)


class FakeResp:
    def __init__(self, payload, code=200):
        self._payload = json.dumps(payload).encode()
        self.status = code

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.cfg_dir = home / ".config" / "secretserver"
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

    def test_login_writes_config(self):
        with mock.patch.object(ss, "_request", return_value={"items": []}):
            ss.main(
                [
                    "login",
                    "--url",
                    "https://secrets.example/",
                    "--token",
                    "ss_test",
                    "--project",
                    "11111111-1111-1111-1111-111111111111",
                ]
            )
        text = self.cfg_path.read_text()
        self.assertIn("https://secrets.example", text)
        self.assertIn("ss_test", text)
        self.assertEqual(self.cfg_path.stat().st_mode & 0o777, 0o600)

    def test_list_uses_meta(self):
        calls = []

        def capture(method, path, *, body=None, query=None):
            calls.append((method, path, query))
            return {"items": [{"key": "K"}]}

        with mock.patch.object(ss, "_request", side_effect=capture):
            ss._save_config("http://h", "ss_t", "11111111-1111-1111-1111-111111111111")
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                ss.main(["list", "-q", "api"])
        self.assertEqual(calls[0][0], "GET")
        self.assertEqual(calls[0][2], {"meta": "1", "q": "api"})
        self.assertIn("K", buf.getvalue())

    def test_edit_put_vs_patch(self):
        calls = []

        def capture(method, path, *, body=None, query=None):
            calls.append((method, path, body))
            return {"ok": True, "key": "K"}

        with mock.patch.object(ss, "_request", side_effect=capture):
            ss._save_config("http://h", "ss_t", "11111111-1111-1111-1111-111111111111")
            ss.main(["edit", "K", "--value", "v1"])
            ss.main(["edit", "K", "--note", "n"])
        self.assertEqual(calls[0][0], "PUT")
        self.assertEqual(calls[0][2]["value"], "v1")
        self.assertEqual(calls[1][0], "PATCH")
        self.assertNotIn("value", calls[1][2])

    def test_env_overrides_config(self):
        ss._save_config("http://from-file", "ss_file", "p-file")
        os.environ[ss.ENV_URL] = "http://from-env"
        os.environ[ss.ENV_TOKEN] = "ss_env"
        os.environ[ss.ENV_PROJECT] = "p-env"
        c = ss._load_config()
        self.assertEqual(c["url"], "http://from-env")
        self.assertEqual(c["token"], "ss_env")
        self.assertEqual(c["project"], "p-env")

    def test_pid_alias_for_project(self):
        ss._save_config("http://from-file", "ss_file", "p-file")
        os.environ["PID"] = "p-from-pid"
        c = ss._load_config()
        self.assertEqual(c["project"], "p-from-pid")
        # SS_PROJECT wins over PID
        os.environ[ss.ENV_PROJECT] = "p-prefer"
        self.assertEqual(ss._load_config()["project"], "p-prefer")

    def test_configure_alias(self):
        with mock.patch.object(ss, "_request", return_value={"items": []}):
            ss.main(
                [
                    "configure",
                    "--url",
                    "http://h",
                    "--token",
                    "ss_t",
                    "--project",
                    "11111111-1111-1111-1111-111111111111",
                ]
            )
        self.assertIn("ss_t", self.cfg_path.read_text())

    def test_delete(self):
        with mock.patch.object(ss, "_request", return_value={"ok": True}) as m:
            ss._save_config("http://h", "ss_t", "11111111-1111-1111-1111-111111111111")
            ss.main(["delete", "db/pass"])
        m.assert_called_once()
        self.assertEqual(m.call_args[0][0], "DELETE")
        self.assertIn("db/pass", m.call_args[0][1])


if __name__ == "__main__":
    unittest.main()
