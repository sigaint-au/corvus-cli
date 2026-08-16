#!/usr/bin/python3
"""Pytest suite for the secretserver CLI (no network)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
PID = "11111111-1111-1111-1111-111111111111"


def _load_script() -> object:
    loader = importlib.machinery.SourceFileLoader("secretserver", str(ROOT / "secretserver"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ss():
    """The secretserver CLI loaded as a module."""
    return _load_script()


@pytest.fixture(autouse=True)
def cli_env(ss, tmp_path, monkeypatch):
    """Point the CLI at a throwaway config dir and clear env credentials."""
    cfg_dir = tmp_path / ".config" / "secretserver"
    monkeypatch.setattr(ss, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(ss, "CONFIG_PATH", cfg_dir / "config")
    for env in (ss.ENV_URL, ss.ENV_TOKEN, ss.ENV_PROJECT):
        monkeypatch.delenv(env, raising=False)
    return cfg_dir


@pytest.fixture()
def cfg_path(cli_env):
    return cli_env / "config"


def write_config(ss, token="ss_t", project=PID, url="http://h"):
    ss._save_config(url, token, project)


def capture(calls, value=None):
    value = {"ok": True} if value is None else value

    def wrapped(method, path, *, body=None, query=None, project=None):
        calls.append((method, path, body, query))
        return value

    return wrapped


def test_no_args_prints_usage(ss, capsys):
    with pytest.raises(SystemExit) as e:
        ss.main([])
    assert e.value.code == 0
    assert "get secret" in capsys.readouterr().out


def test_login_writes_config(ss, cfg_path):
    with mock.patch.object(ss, "_http", return_value={"items": []}):
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
    text = cfg_path.read_text()
    assert "https://secrets.example" in text
    assert "ss_test" in text


def test_get_secrets_list(ss, capsys):
    calls = []
    payload = {"items": [{"key": "K", "kind": "plain", "note": "n"}]}
    with mock.patch.object(ss, "_proj_api", side_effect=capture(calls, payload)):
        write_config(ss)
        ss.main(["get", "secrets", "-l", "api"])
    assert calls[0][0] == "GET"
    assert calls[0][3].get("q") == "api"
    assert "KEY" in capsys.readouterr().out


def test_get_secret_value_only(ss, capsys):
    with mock.patch.object(ss, "_proj_api", return_value={"key": "K", "value": "s3cret"}):
        write_config(ss)
        ss.main(["get", "secret", "K", "-o", "value"])
    assert capsys.readouterr().out == "s3cret\n"


def test_apply_from_env(ss, capsys, monkeypatch):
    calls = []
    monkeypatch.setenv("SS_TEST_VAL", "from-env-secret")
    with mock.patch.object(ss, "_proj_api", side_effect=capture(calls)):
        write_config(ss)
        ss.main(["apply", "secret", "K", "--from-env", "SS_TEST_VAL"])
    assert calls[0][0] == "PUT"
    assert calls[0][2]["value"] == "from-env-secret"
    assert "from-env-secret" not in capsys.readouterr().out


def test_delete_secret(ss):
    with mock.patch.object(ss, "_proj_api", return_value={"ok": True}) as m:
        write_config(ss)
        ss.main(["delete", "secret", "db/pass"])
    assert m.call_args[0][0] == "DELETE"


def test_project_switch(ss, cfg_path):
    write_config(ss)
    with mock.patch.object(ss, "_http", return_value={"items": []}):
        ss.main(["project", "22222222-2222-2222-2222-222222222222"])
    assert "22222222-2222-2222-2222-222222222222" in cfg_path.read_text()


def test_reveal_submits_access_request_when_approval_required(ss, capsys, monkeypatch):
    calls = []

    def wrapped(method, path, *, body=None, query=None, project=None):
        calls.append((method, path, body))
        if method == "GET":
            raise SystemExit(
                'HTTP 403: {"error":"approval_required","message":"Reveal requires approval"}'
            )
        return {"ok": True, "message": "Access request submitted."}

    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_proj_api", side_effect=wrapped):
        ss.main(["reveal", "secret", "prod_root", "--reason", "debug #1234"])
    assert calls[0][0] == "GET"
    assert "/secrets/prod_root" in calls[0][1]
    assert calls[1][0] == "POST"
    assert "/secrets/prod_root/access-request" in calls[1][1]
    assert calls[1][2] == {"reason": "debug #1234"}
    assert "Access request submitted" in capsys.readouterr().out


def test_reveal_already_allowed_prints_hint(ss, capsys):
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_proj_api", return_value={"key": "K", "value": "v"}):
        ss.main(["reveal", "secret", "K"])
    assert "already reveal" in capsys.readouterr().out


def test_get_requests_lists_pending(ss, capsys):
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
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_proj_api", side_effect=capture(calls, payload)):
        ss.main(["get", "requests"])
    assert calls[0][0] == "GET"
    assert calls[0][1] == "/access-requests"
    assert calls[0][3].get("status") == "pending"
    out = capsys.readouterr().out
    assert rid in out
    assert "prod_root" in out
    assert "u@ex.com" in out


def test_approve_request_posts_minutes(ss, capsys):
    rid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    calls = []
    write_config(ss, token="pat_admin")
    with mock.patch.object(ss, "_proj_api", side_effect=capture(calls)):
        ss.main(["approve", rid, "--minutes", "60"])
    assert calls[0][0] == "POST"
    assert calls[0][1] == f"/access-requests/{rid}/approve"
    assert calls[0][2] == {"minutes": 60}
    assert "Approved" in capsys.readouterr().out


def test_approve_rejects_invalid_minutes(ss):
    write_config(ss, token="pat_admin")
    with pytest.raises(SystemExit) as e:
        ss.main(["approve", "rid", "--minutes", "99"])
    assert "minutes" in str(e.value).lower()


def test_deny_request(ss, capsys):
    rid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    calls = []
    write_config(ss, token="pat_admin")
    with mock.patch.object(ss, "_proj_api", side_effect=capture(calls)):
        ss.main(["deny", rid])
    assert calls[0][0] == "POST"
    assert calls[0][1] == f"/access-requests/{rid}/deny"
    assert "Denied" in capsys.readouterr().out


def test_get_secret_approval_hint(ss):
    def boom(method, path, *, body=None, query=None, project=None):
        raise SystemExit('HTTP 403: {"error":"approval_required","message":"need approval"}')

    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_proj_api", side_effect=boom):
        with pytest.raises(SystemExit) as e:
            ss.main(["get", "secret", "locked_key"])
    assert "reveal secret locked_key" in str(e.value)


# ── management API (/api/v1/manage) ─────────────────────────────────


def test_get_teams_hits_manage(ss):
    calls = []
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_api", side_effect=capture(calls)):
        ss.main(["get", "teams", "-l", "platform"])
    assert calls[0][0] == "GET"
    assert calls[0][1] == "/teams"
    assert calls[0][3].get("q") == "platform"


def test_get_project_detail_hits_manage(ss):
    calls = []
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_api", side_effect=capture(calls)):
        ss.main(["get", "project", "ios-app"])
    assert calls[0][0] == "GET"
    assert calls[0][1] == "/projects/ios-app"


def test_get_tokens_hits_manage_project(ss):
    calls = []
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(calls)):
        ss.main(["get", "tokens"])
    assert calls[0][0] == "GET"
    assert calls[0][1] == "/tokens"


def test_get_audit_source_org_hits_admin_manage(ss):
    calls = []
    write_config(ss, token="pat_admin")
    with mock.patch.object(ss, "_mgmt_api", side_effect=capture(calls)):
        ss.main(["get", "audit", "--source", "org"])
    assert calls[0][0] == "GET"
    assert calls[0][1] == "/admin/audit"
    assert calls[0][3].get("source") == "org"


def test_get_users_hits_admin_manage(ss):
    calls = []
    write_config(ss, token="pat_admin")
    with mock.patch.object(ss, "_mgmt_api", side_effect=capture(calls)):
        ss.main(["get", "users", "-l", "alice"])
    assert calls[0][1] == "/admin/users"
    assert calls[0][3].get("q") == "alice"


def test_create_token_uses_service_role(ss):
    calls = []
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(calls)):
        ss.main(["create", "token", "ci"])
    assert calls[0][1] == "/tokens"
    assert calls[0][2]["role"] == "service-reveal"


def test_create_token_service_write_role(ss):
    calls = []
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(calls)):
        ss.main(["create", "token", "ci", "--role", "service-write"])
    assert calls[0][2]["role"] == "service-write"


def test_create_team_member_uses_team_role(ss):
    calls = []
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_api", side_effect=capture(calls)):
        ss.main(["create", "member", "bob@x.com", "--team", "Platform"])
    assert calls[0][1] == "/teams/Platform/members"
    assert calls[0][2]["email"] == "bob@x.com"
    assert calls[0][2]["role"] == "team-member"


def test_create_project_member_uses_project_role(ss):
    calls = []
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(calls)):
        ss.main(["create", "member", "dave@x.com", "--role", "project-write"])
    assert calls[0][2]["role"] == "project-write"


def test_create_rejects_invalid_role(ss):
    write_config(ss, token="pat_test")
    with pytest.raises(SystemExit) as e:
        ss.main(["create", "token", "ci", "--role", "write"])
    assert "service-read" in str(e.value)


def test_delete_team_hits_manage(ss):
    calls = []
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_api", side_effect=capture(calls)):
        ss.main(["delete", "team", "MyTeam"])
    assert calls[0][0] == "DELETE"
    assert calls[0][1] == "/teams/MyTeam"


def test_restore_trash_hits_manage_project(ss):
    calls = []
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(calls)):
        ss.main(["restore", "trash", "sec-id"])
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/trash/sec-id/restore"


# ── token scope ─────────────────────────────────────────────────────


def test_create_token_scope_passed(ss):
    calls = []
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(calls)):
        ss.main(["create", "token", "ci", "--scope", "API_KEY, prod/*"])
    assert calls[0][1] == "/tokens"
    assert calls[0][2]["scope"] == ["API_KEY", "prod/*"]


def test_create_token_no_scope_omits_field(ss):
    calls = []
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(calls)):
        ss.main(["create", "token", "ci"])
    assert "scope" not in calls[0][2]


def test_get_tokens_shows_scope(ss, capsys):
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
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_proj_api", return_value=payload):
        ss.main(["get", "tokens"])
    out = capsys.readouterr().out
    assert "SCOPE" in out
    assert "API_KEY,prod/*" in out


# ── secret metadata ─────────────────────────────────────────────────


def test_apply_meta_posts_to_manage(ss):
    calls = []
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(calls)):
        ss.main(["apply", "secret", "K", "--meta", "owner=team"])
    assert calls[0][0] == "PATCH"
    assert calls[0][1] == "/secrets/K/meta"
    assert calls[0][2] == {"key": "owner", "value": "team"}


def test_apply_meta_delete_posts_to_manage(ss):
    calls = []
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(calls)):
        ss.main(["apply", "secret", "K", "--delete-meta", "owner"])
    assert calls[0][0] == "DELETE"
    assert calls[0][1] == "/secrets/K/meta/owner"


def test_apply_meta_and_value_writes_both(ss, monkeypatch):
    value_calls, meta_calls = [], []
    monkeypatch.setenv("SS_TEST", "x")
    write_config(ss, token="pat_test")
    with (
        mock.patch.object(ss, "_proj_api", side_effect=capture(value_calls)),
        mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(meta_calls)),
    ):
        ss.main(["apply", "secret", "K", "--from-env=SS_TEST", "--meta", "a=b"])
    assert value_calls[0][0] == "PUT"
    assert meta_calls[0][1] == "/secrets/K/meta"


def test_apply_meta_requires_pat(ss):
    write_config(ss, token="ss_machine")
    with pytest.raises(SystemExit) as e:
        ss.main(["apply", "secret", "K", "--meta", "a=b"])
    assert "pat_" in str(e.value)


def test_apply_meta_requires_key_equals_value(ss):
    write_config(ss, token="pat_test")
    with pytest.raises(SystemExit) as e:
        ss.main(["apply", "secret", "K", "--meta", "barekey"])
    assert "KEY=VALUE" in str(e.value)


# ── new-management commands (bindings / export / settings / groups) ─


def test_apply_access_mode_patches_manage(ss):
    calls = []
    write_config(ss, token="pat_admin")
    with mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(calls)):
        ss.main(
            ["apply", "secret", "K", "--access-mode", "restricted", "--requires-approval", "on"]
        )
    assert calls[0][0] == "PATCH"
    assert calls[0][1] == "/secrets/K"
    assert calls[0][2] == {"access_mode": "restricted", "requires_approval": True}


def test_apply_access_mode_requires_pat(ss):
    write_config(ss, token="ss_machine")
    with pytest.raises(SystemExit) as e:
        ss.main(["apply", "secret", "K", "--access-mode", "restricted"])
    assert "pat_" in str(e.value)


def test_grant_secret_binding(ss):
    calls = []
    write_config(ss, token="pat_admin")
    with mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(calls)):
        ss.main(["grant", "secret", "K", "--to", "bob@x.com", "--role", "secret-reveal"])
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/secrets/K/bindings"
    assert calls[0][2]["subject_kind"] == "User"
    assert calls[0][2]["subject_id"] == "bob@x.com"


def test_unbind_secret_binding(ss):
    calls = []
    write_config(ss, token="pat_admin")
    with mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(calls)):
        ss.main(["unbind", "secret", "K", "bid123"])
    assert calls[0][0] == "DELETE"
    assert calls[0][1] == "/secrets/K/bindings/bid123"


def test_export_env(ss, capsys):
    write_config(ss, token="pat_test")
    with mock.patch.object(
        ss,
        "_mgmt_proj_api",
        return_value={"mode": "plain", "items": [{"key": "A", "value": "v1", "note": ""}]},
    ):
        ss.main(["export", "-o", "env"])
    assert "A=v1" in capsys.readouterr().out


def test_settings_patches_project(ss):
    calls = []
    write_config(ss, token="pat_admin")
    with mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(calls)):
        ss.main(["settings", "--require-reveal-approval", "on", "--default-access-mode", "inherit"])
    assert calls[0][0] == "PATCH"
    assert calls[0][1] == ""
    assert calls[0][2]["require_reveal_approval"] is True


def test_group_member_add(ss):
    calls = []
    write_config(ss, token="pat_admin")
    with mock.patch.object(ss, "_mgmt_api", side_effect=capture(calls)):
        ss.main(["create", "group-member", "bob@x.com", "--team", "T", "--group", "admins"])
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/teams/T/groups/admins/members"
    assert calls[0][2]["email"] == "bob@x.com"


def test_restore_trash_all(ss):
    calls = []
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(calls)):
        ss.main(["restore", "trash", "--all"])
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/trash/restore"
    assert calls[0][2]["action"] == "restore"


def test_delete_trash_all_purges(ss):
    calls = []
    write_config(ss, token="pat_test")
    with mock.patch.object(ss, "_mgmt_proj_api", side_effect=capture(calls)):
        ss.main(["delete", "trash", "--all"])
    assert calls[0][2]["action"] == "purge"
