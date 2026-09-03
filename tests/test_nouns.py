#!/usr/bin/python3
"""Noun-first syntax, KEY/VALUE truncation, and color behavior (no network)."""

from __future__ import annotations

from unittest import mock

import pytest

from corvus_cli import output as out_mod
from corvus_cli.cli import _rewrite_noun_first, main
from corvus_cli.config import _save_config

PID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    import corvus_cli.config as cfgmod

    d = tmp_path / ".config" / "corvus"
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", d)
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", d / "config")
    for env in ("SS_URL", "SS_TOKEN", "SS_PROJECT", "PID"):
        monkeypatch.delenv(env, raising=False)
    _save_config("http://h", "ss_t", PID)
    return d


def _proj_calls(mod_name, calls, payload=None):
    import importlib

    mod = importlib.import_module(mod_name)
    value = {"ok": True} if payload is None else payload

    def wrapped(method, path, *, body=None, query=None, project=None, token=None):
        calls.append((method, path, body, query, project))
        return value

    return mock.patch.object(mod, "_proj_api", side_effect=wrapped)


def test_rewrite_spot_checks():
    assert _rewrite_noun_first(["secret", "list", "-l", "x"]) == ["get", "secrets", "-l", "x"]
    assert _rewrite_noun_first(["secret", "set", "K"]) == ["apply", "secret", "K"]
    assert _rewrite_noun_first(["secret", "export", "--yes"]) == ["export", "--yes"]
    assert _rewrite_noun_first(["binding", "revoke", "K", "b1"]) == ["unbind", "secret", "K", "b1"]
    assert _rewrite_noun_first(["trash", "purge", "--all"]) == ["delete", "trash", "--all"]
    assert _rewrite_noun_first(["project", "use", "ios-app"]) == ["project", "ios-app"]
    assert _rewrite_noun_first(["get", "secrets"]) == ["get", "secrets"]
    # legacy project switch untouched
    assert _rewrite_noun_first(["project", "ios-app"]) == ["project", "ios-app"]
    assert _rewrite_noun_first(["group", "member", "add", "a@x"]) == ["create", "group-member", "a@x"]


def test_rewrite_bad_verb_exits():
    with pytest.raises(SystemExit):
        _rewrite_noun_first(["secret", "frobnicate"])
    with pytest.raises(SystemExit):
        _rewrite_noun_first(["secret"])


def test_secret_list(cfg, capsys):
    calls = []
    payload = {"items": [{"key": "K", "kind": "plain", "note": "n"}]}
    with _proj_calls("corvus_cli.commands.management", calls, payload):
        main(["secret", "list", "-l", "api"])
    assert calls[0][0] == "GET"
    assert calls[0][3].get("q") == "api"
    out = capsys.readouterr().out
    assert "KEY" in out and "\033" not in out  # plain under pipe


def test_secret_set(cfg, capsys, monkeypatch):
    calls = []
    monkeypatch.setenv("SS_TEST_VAL", "from-env-secret")
    with _proj_calls("corvus_cli.commands.secrets", calls):
        main(["secret", "set", "K", "--from-env", "SS_TEST_VAL"])
    assert calls[0][0] == "PUT"
    assert calls[0][2]["value"] == "from-env-secret"
    assert "from-env-secret" not in capsys.readouterr().out


def test_secret_get_value_never_truncated(cfg, capsys):
    long_value = "v" * 4000
    payload = {"key": "SSH_KEY", "value": long_value}
    with _proj_calls("corvus_cli.commands.management", [], payload):
        main(["secret", "get", "SSH_KEY"])
    assert long_value in capsys.readouterr().out


def test_table_key_value_full_note_truncated(capsys):
    out_mod.set_no_trunc(False)
    out_mod.print_table(
        ["KEY", "NOTE", "VALUE"],
        [["K" * 100, "n" * 100, "v" * 100]],
    )
    out = capsys.readouterr().out
    assert "K" * 100 in out
    assert "v" * 100 in out
    assert "n" * 100 not in out


def test_team_transfer_to_flag(cfg):
    import importlib

    mgmt = importlib.import_module("corvus_cli.commands.management")
    calls = []
    with mock.patch.object(
        mgmt, "_mgmt_api", side_effect=lambda m, p, **kw: calls.append((m, p, kw.get("body")))
    ):
        main(["team", "transfer", "Platform", "--to", "new@x"])
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/transfer")
    assert calls[0][2] == {"email": "new@x"}


def test_trash_purge_all(cfg):
    import importlib

    _save_config("http://h", "pat_test", PID)
    mgmt = importlib.import_module("corvus_cli.commands.management")
    calls = []
    with mock.patch.object(
        mgmt,
        "_mgmt_proj_api",
        side_effect=lambda m, p, **kw: calls.append((m, p, kw.get("body"))),
    ):
        main(["trash", "purge", "--all", "--yes"])
    assert calls[0][0] == "POST"
    assert calls[0][2] == {"action": "purge"}
