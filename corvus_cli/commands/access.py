"""Access-control and export commands.

Description:
    Implements ``reveal``, ``approve``/``deny``, ``grant``/``unbind``, ``export``,
    ``settings`` and ``group-member``. Enforces PAT gates, validates grant
    durations against server choices, requires ``--yes`` for bulk export, and
    handles approval polling hints.

Inputs:
    Parsed :class:`argparse.Namespace` with per-command args (``request_id``,
    ``minutes``, ``to``/``group``/``sa``/``role``, ``requires_approval``, …)
    plus creds/API plumbing via :mod:`corvus_cli.config`/:mod:`corvus_cli.api`.

Outputs:
    Status messages / tables / JSON / env dumps; exits on invalid flags or
    insufficient token.

Example:
    >>> # approve with custom window:
    >>> # main(["approve", "req-id", "--minutes", "60"])  # doctest: +SKIP
"""

from __future__ import annotations

import sys
import urllib.parse

from corvus_cli.api import _mgmt_proj_api, _mgmt_api, _proj_api
from corvus_cli.config import _expect_confirm_export, _require_pat, _valid_role
from corvus_cli.constants import _SECRET_ROLES
from corvus_cli.output import emit, print_json, print_table


def _out(args) -> str:
    """Return output mode defaulting to ``table``.

    Description:
        Small local copy to avoid importing from management and forming a cycle
        on hot paths (group-member ↔ access). Same semantics as
        :func:`corvus_cli.commands.management._out`.

    Inputs:
        args: namespace with optional ``.output``.

    Outputs:
        Output mode string.

    Example:
        >>> import argparse; _out(argparse.Namespace(output="json"))
        'json'
    """
    return getattr(args, "output", None) or "table"


def cmd_reveal(args) -> None:
    """Request reveal approval or print the value when already allowed.

    Description:
        Tries a direct ``GET`` first; on allowed secrets prints the value/table
        immediately. Otherwise filters 403 variants (``reveal access`` vs ACL
        denial) with hints, or posts ``/secrets/<key>/access-request`` with the
        reason.

    Inputs:
        args: namespace with ``resource`` (default ``secret``), ``name`` (key),
        ``reason`` (str), ``project``, ``output``.

    Outputs:
        Secret value/table on immediate allow; else message about pending
        request. Exits with ACL/reveal-access hints.

    Example:
        >>> # cmd_reveal(Namespace(resource="secret", name="API_KEY", reason="need"))  # doctest: +SKIP
    """
    res = (getattr(args, "resource", "secret") or "secret").lower()
    name = getattr(args, "name", None)
    if res not in ("secret", "secrets") or not name:
        sys.exit("usage: reveal secret NAME [--reason …]")
    key = urllib.parse.quote(name, safe="/")
    reason = (getattr(args, "reason", None) or "").strip()
    out = _out(args)
    project = getattr(args, "project", None)
    try:
        data = _proj_api("GET", f"/secrets/{key}", project=project)
        if out == "value" and isinstance(data, dict) and "value" in data:
            emit(data, "value")
            return
        if out == "json":
            emit(data, out)
            return
        print(f"You can already reveal {name}.")
        if isinstance(data, dict) and data.get("value") is not None and out == "table":
            print("(use: get secret NAME -o value)")
        return
    except SystemExit as e:
        msg = str(e)
        low = msg.lower()
        if "reveal access" in low:
            sys.exit(f"{msg}\nhint: this token cannot reveal secrets (service-read). Use a service-reveal token or a pat_…/sso_… token.")
        if "forbidden" in low and "approval" not in low:
            sys.exit(f"{msg}\nhint: ACL denies reveal; ask a project admin for permission.")
        if "403" not in msg and "approval" not in low:
            raise
    body = {"reason": reason} if reason else {}
    data = _proj_api("POST", f"/secrets/{key}/access-request", body=body or None, project=project)
    if out == "json":
        emit(data, out)
        return
    print((data or {}).get("message") or "Access request submitted. You'll be notified when approved.")  # type: ignore[union-attr]


def cmd_approve(args) -> None:
    """Approve a pending reveal request for a bounded window.

    Description:
        Validates ``--minutes`` against server-allowed choices
        ``(15, 60, 240, 1440)``; requires PAT.

    Inputs:
        args: namespace with ``request_id``/``name`` (request ID),
        ``minutes`` (int), ``project``, ``output``.

    Outputs:
        Confirmation message or JSON; exits when minutes invalid or token is
        ``ss_``.

    Example:
        >>> # cmd_approve(Namespace(request_id="rid", minutes=60))  # doctest: +SKIP
        Approved request rid.
    """
    _require_pat()
    req_id = (getattr(args, "request_id", None) or getattr(args, "name", None) or "").strip()
    if not req_id:
        sys.exit("usage: approve <request-id> [--minutes 15]")
    allowed = (15, 60, 240, 1440)
    minutes = int(getattr(args, "minutes", None) or 15)
    if minutes not in allowed:
        sys.exit(f"--minutes must be one of {', '.join(str(m) for m in allowed)} (got {minutes})")
    body = {"minutes": minutes}
    data = _proj_api("POST", f"/access-requests/{req_id}/approve", body=body, project=getattr(args, "project", None))
    out = _out(args)
    if out == "json":
        emit(data, out)
        return
    print((data or {}).get("message") or f"Approved request {req_id}.")


def cmd_deny(args) -> None:
    """Deny a pending reveal request.

    Description:
        PAT-only; posts to ``/access-requests/<id>/deny``.

    Inputs:
        args: namespace with ``request_id``/``name``, ``project``, ``output``.

    Outputs:
        Confirmation message or JSON; exits when token is ``ss_``.

    Example:
        >>> # cmd_deny(Namespace(request_id="rid"))  # doctest: +SKIP
        Denied request rid.
    """
    _require_pat()
    req_id = (getattr(args, "request_id", None) or getattr(args, "name", None) or "").strip()
    if not req_id:
        sys.exit("usage: deny <request-id>")
    data = _proj_api("POST", f"/access-requests/{req_id}/deny", body={}, project=getattr(args, "project", None))
    out = _out(args)
    if out == "json":
        emit(data, out)
        return
    print((data or {}).get("message") or f"Denied request {req_id}.")


def cmd_grant(args) -> None:
    """Add a secret-scope role binding.

    Description:
        Targets a single secret (``--to EMAIL`` | ``--group ID`` | ``--sa TOKEN_ID``)
        with a validated secret role; PAT + project admin on the server.

    Inputs:
        args: namespace with ``resource`` (``secret``), ``name`` (key), ``to``,
        ``group``, ``sa``, ``role``, ``project``, ``output``.

    Outputs:
        Server response via emit; exits on missing subject or invalid role.

    Example:
        >>> # cmd_grant(Namespace(resource="secret", name="K", to="a@x", role="secret-reveal"))  # doctest: +SKIP
    """
    res = (getattr(args, "resource", "secret") or "secret").lower()
    if res not in ("secret", "secrets") or not getattr(args, "name", None):
        sys.exit("usage: grant secret NAME --to EMAIL|--group ID|--sa ID --role ROLE")
    if getattr(args, "to", None):
        kind, sid = "User", args.to
    elif getattr(args, "group", None):
        kind, sid = "Group", args.group
    elif getattr(args, "sa", None):
        kind, sid = "ServiceAccount", args.sa
    else:
        sys.exit("grant needs --to EMAIL, --group ID, or --sa TOKEN_ID")
    role = _valid_role(getattr(args, "role", None), "secret-reveal", _SECRET_ROLES)
    _require_pat()
    key = urllib.parse.quote(args.name, safe="/")
    emit(
        _mgmt_proj_api(
            "POST",
            f"/secrets/{key}/bindings",
            body={"subject_kind": kind, "subject_id": sid, "role": role},
            project=getattr(args, "project", None),
        ),
        _out(args),
    )


def cmd_unbind(args) -> None:
    """Remove a secret-scope role binding.

    Description:
        PAT-only delete of ``/secrets/<key>/bindings/<bindingId>``.

    Inputs:
        args: namespace with ``resource`` (``secret``), ``name`` (key),
        ``binding`` (binding id), ``project``, ``output``.

    Outputs:
        Server response; exits on missing key/binding.

    Example:
        >>> # cmd_unbind(Namespace(resource="secret", name="K", binding="bid1"))  # doctest: +SKIP
    """
    res = (getattr(args, "resource", "secret") or "secret").lower()
    if res not in ("secret", "secrets") or not getattr(args, "name", None) or not getattr(args, "binding", None):
        sys.exit("usage: unbind secret NAME <binding-id>")
    _require_pat()
    key = urllib.parse.quote(args.name, safe="/")
    emit(
        _mgmt_proj_api(
            "DELETE",
            f"/secrets/{key}/bindings/{urllib.parse.quote(args.binding, safe='')}",
            project=getattr(args, "project", None),
        ),
        _out(args),
    )


def cmd_export(args) -> None:
    """Dump all plaintext secrets for a project (audited; needs --yes).

    Description:
        PAT-only bulk export via ``GET …/export?mode=plain``. Requires ``--yes``
        to prevent accidental exfiltration. Project may be positional or
        ``--project`` override; defaults to current project. Supports
        ``env``/``value`` line format (`KEY=VALUE`), ``json``, ``csv``, ``table``.

    Inputs:
        args: namespace with ``name`` (project positional), ``project`` (kw),
        ``output`` (default ``env``), ``yes`` (bool).

    Outputs:
        Secrets dump to ``stdout`` in chosen format; emits
        ``warning: N secrets exported`` to ``stderr`` and a pagination notice
        when applicable. Exits when ``--yes`` missing.

    Example:
        >>> # corvus export -o env --yes | grep API_KEY  # shell
    """
    _require_pat()
    _expect_confirm_export(args)
    out = _out(args) or "env"
    # ``--project`` is canonical; positional ``name`` is alias.
    proj = (getattr(args, "project", None) or getattr(args, "name", None) or "").strip() or None
    data = _mgmt_proj_api("GET", "/export", query={"mode": "plain"}, project=proj)
    items = (data or {}).get("items") or []  # type: ignore[union-attr]
    if getattr(data or {}, "get", lambda *_: None)("next") or getattr(data or {}, "get", lambda *_: None)("has_more"):
        print("warning: export may be paginated; results truncated", file=sys.stderr)
    print(f"warning: exporting {len(items)} secret(s) as plaintext (audited)", file=sys.stderr)
    if out == "json":
        print_json(data)
        return
    if out == "csv":
        print_table(["KEY", "VALUE"], [[it.get("key", ""), it.get("value", "")] for it in items])
        return
    if out in ("value", "env"):
        for it in items:
            sys.stdout.write(f"{it.get('key','')}={it.get('value','')}\n")
        return
    print_table(["KEY", "NOTE"], [[it.get("key", ""), it.get("note", "")] for it in items])


def cmd_settings(args) -> None:
    """Read/write project settings (PAT, project admin).

    Description:
        Currently patches ``require_reveal_approval`` (``on``/``off`` aliases),
        ``default_access_mode`` (``inherit``/``restricted``), and ``description``.
        At least one must be set.

    Inputs:
        args: namespace with ``require_reveal_approval``, ``default_access_mode``,
        ``description``, ``project``, ``output``.

    Outputs:
        Patched project as table/JSON; exits when nothing to set.

    Example:
        >>> # corvus settings --require-reveal-approval on  # shell
    """
    _require_pat()
    body: dict = {}
    if getattr(args, "require_reveal_approval", None) is not None:
        body["require_reveal_approval"] = args.require_reveal_approval in ("on", "true", "yes", "1")
    if getattr(args, "default_access_mode", None):
        body["default_access_mode"] = args.default_access_mode
    if getattr(args, "description", None) is not None:
        body["description"] = args.description
    if not body:
        sys.exit("no settings to set (--require-reveal-approval, --default-access-mode, --description)")
    emit(_mgmt_proj_api("PATCH", "", body=body, project=getattr(args, "project", None)), _out(args))


def cmd_group_member(args, *, remove: bool) -> None:
    """Add or remove a user from a team group.

    Description:
        PAT-only; operates on ``/teams/<team>/groups/<group>/members``.

    Inputs:
        args: namespace with ``team``, ``group``, ``name``/``email``, ``output``.
        remove: ``True`` to delete, ``False`` to add.

    Outputs:
        Server response via emit; exits on missing identifiers.

    Example:
        >>> # corvus create group-member bob@x --team Platform --group admins  # shell
    """
    team = getattr(args, "team", None)
    group = getattr(args, "group", None)
    email = (getattr(args, "name", None) or getattr(args, "email", None) or "").strip()
    if not team or not group or not email:
        sys.exit("usage: create|delete group-member EMAIL --team T --group G")
    _require_pat()
    if remove:
        emit(
            _mgmt_api(
                "DELETE",
                f"/teams/{urllib.parse.quote(team, safe='')}/groups/{urllib.parse.quote(group, safe='')}/members/{urllib.parse.quote(email, safe='')}",
            ),
            _out(args),
        )
    else:
        emit(
            _mgmt_api(
                "POST",
                f"/teams/{urllib.parse.quote(team, safe='')}/groups/{urllib.parse.quote(group, safe='')}/members",
                body={"email": email},
            ),
            _out(args),
        )
