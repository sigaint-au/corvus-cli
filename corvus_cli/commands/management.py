"""Management, listing, audit and group commands.

Description:
    Implements ``get``/``create``/``delete``/``restore``/``transfer`` dispatch
    for teams, projects, members, tokens, trash, folders, audit, users and
    groups. Chooses the correct API mount (``/eso/v1`` vs ``/api/v1/manage``),
    maps roles to the right surface, validates ``--scope`` and resource names,
    and requires PAT + ``--yes`` where needed.

Inputs:
    Parsed :class:`argparse.Namespace` (``resource``, ``name``, ``team``,
    ``selector``, ``project``, ``role``, ``scope``, ``yes``, ``status``, …).

Outputs:
    Rendered output via :mod:`corvus_cli.output` or ``sys.exit`` on invalid
    inputs; token values printed once to ``stderr`` on creation.

Example:
    >>> # get teams:
    >>> # cmd_get(Namespace(resource="teams", selector="platform", …))  # doctest: +SKIP
"""

from __future__ import annotations

import re
import sys
import urllib.parse

from corvus_cli.api import _admin_api, _api, _mgmt_api, _mgmt_proj_api, _proj_api
from corvus_cli.config import _require_confirm, _require_pat, _suggest_resource, _valid_role
from corvus_cli.constants import _PROJECT_ROLES, _SERVICE_ROLES, _TEAM_ROLES

from corvus_cli.commands.secrets import _out, cmd_apply
from corvus_cli.output import emit, print_table


def cmd_get(args) -> None:
    """List or fetch resources by name.

    Description:
        Routes ``resource`` to the appropriate endpoint, forwarding ``-l``
        selector, ``--team`` scoping, ``--status`` for requests, and
        ``--project`` override. Maps friendly aliases (``requests``/``access-requests``).
        On ``get secret`` surfaces approval/forbidden hints. Emits to the
        requested output mode (``table``/``json``/``value``/``name``).

    Inputs:
        args: namespace with ``resource`` (str), ``name`` (str|None),
        ``selector``, ``team``, ``status``, ``source``, ``actor``, ``action``,
        ``since``, ``until``, ``admin``, ``project``, ``output``.

    Outputs:
        Table/JSON/value to ``stdout``; exits on unknown resource (with
        did-you-mean) or forbidden/approval errors.

    Example:
        >>> # cmd_get(Namespace(resource="secrets", selector="api", output="json"))  # doctest: +SKIP
    """
    res = (getattr(args, "resource", "") or "").lower()
    out = _out(args)
    name = getattr(args, "name", None)
    team = getattr(args, "team", None)
    q = getattr(args, "selector", None)
    project = getattr(args, "project", None)

    if res in ("secrets",):
        emit(_proj_api("GET", "/secrets", query={"meta": "1", "q": q}, project=project), out)
        return
    if res in ("secret",) and name:
        key = urllib.parse.quote(name, safe="/")
        try:
            emit(_proj_api("GET", f"/secrets/{key}", project=project), out)
        except SystemExit as e:
            msg = str(e)
            low = msg.lower()
            if "approval_required" in low or ("403" in msg and "approval" in low):
                sys.exit(f"{msg}\nhint: request approval with:\n  corvus reveal secret {name} --reason \"...\"")
            if "reveal access" in low:
                sys.exit(
                    f"{msg}\nhint: this token cannot reveal secrets (service-read). "
                    f"Use a service-reveal token or a pat_…/sso_… token."
                )
            if "forbidden" in low or "403" in msg:
                sys.exit(f"{msg}\nhint: you lack reveal permission on this secret (ACL / project role). Ask a project admin.")
            raise
        return
    if res in ("secret", "secrets") and not name:
        sys.exit("usage: get secrets | get secret NAME")
    if res in ("requests", "request", "access-requests"):
        _require_pat()
        status = getattr(args, "status", None) or "pending"
        data = _proj_api(
            "GET",
            "/access-requests",
            query={"status": status if status != "all" else None},
            project=project,
        )
        if out == "json":
            emit(data, out)
            return
        items = (data or {}).get("items") or []  # type: ignore[union-attr]
        if not items:
            print("(empty)")
            return
        print_table(
            ["ID", "REQUESTER", "SECRET", "REASON", "STATUS"],
            [[it.get("id", ""), it.get("email") or it.get("name") or "", it.get("secret_key", ""), it.get("reason", ""), it.get("status", "")] for it in items],
        )
        return
    if res in ("projects",):
        emit(_api("GET", "/projects", query={"q": q or name}, pat_only=True), out)
        return
    if res in ("project",) and name:
        emit(_mgmt_api("GET", f"/projects/{urllib.parse.quote(name, safe='')}"), out)
        return
    if res in ("teams",):
        emit(_mgmt_api("GET", "/teams", query={"q": q or name}), out)
        return
    if res in ("team",) and name:
        emit(_mgmt_api("GET", f"/teams/{urllib.parse.quote(name, safe='')}"), out)
        return
    if res == "groups":
        if not team:
            sys.exit("get groups --team TEAM")
        emit(_mgmt_api("GET", f"/teams/{urllib.parse.quote(team, safe='')}/groups"), out)
        return
    if res in ("members", "member"):
        if team:
            path = f"/teams/{urllib.parse.quote(team, safe='')}/members"
            emit(_mgmt_api("GET", path), out)
        else:
            emit(_mgmt_proj_api("GET", "/members", project=project), out)
        return
    if res in ("tokens", "token"):
        emit(_mgmt_proj_api("GET", "/tokens", project=project), out)
        return
    if res in ("trash",):
        emit(_mgmt_proj_api("GET", "/trash", project=project), out)
        return
    if res in ("folders",):
        emit(_mgmt_proj_api("GET", "/folders", project=project), out)
        return
    if res in ("history",) and name:
        key = urllib.parse.quote(name, safe="/")
        emit(_mgmt_proj_api("GET", f"/secrets/{key}/history", project=project), out)
        return
    if res in ("audit",):
        source = getattr(args, "source", None) or "project"
        if source in ("org", "secret", "access") or getattr(args, "admin", False):
            if source == "project":
                source = "org"
            if getattr(args, "action", None):
                print("warning: --action is ignored for org/secret/access audit", file=sys.stderr)
            emit(
                _admin_api(
                    "GET",
                    "/admin/audit",
                    query={
                        "source": source,
                        "q": q,
                        "actor": getattr(args, "actor", None),
                        "since": getattr(args, "since", None),
                        "until": getattr(args, "until", None),
                    },
                ),
                out,
            )
            return
        if getattr(args, "since", None) or getattr(args, "until", None):
            print("warning: --since/--until are ignored for project audit", file=sys.stderr)
        emit(
            _mgmt_proj_api(
                "GET",
                "/audit",
                query={"q": q, "actor": getattr(args, "actor", None), "action": getattr(args, "action", None)},
                project=project,
            ),
            out,
        )
        return
    if res in ("users", "user"):
        emit(_admin_api("GET", "/admin/users", query={"q": q or name}), out)
        return
    sug = _suggest_resource(res) if res else None
    hint = f"; did you mean {sug!r}?" if sug else ""
    valid = ", ".join(sorted(set(["secrets","secret","projects","project","teams","team","members","tokens","trash","folders","history","requests","audit","users","groups"])))
    sys.exit(f"unknown resource {res!r}{hint} (valid: {valid})")


def cmd_delete(args) -> None:
    """Delete resources (team/project/trash require --yes).

    Description:
        Routes ``resource``/``name``/``team``/``--all`` to the correct delete.
        ``delete trash --all`` is the purge variant.

    Inputs:
        args: namespace with ``resource``, ``name``, ``team``, ``group``,
        ``all``, ``project``, ``yes``, ``output``.

    Outputs:
        Emits server response; exits when resource unknown or ``--yes`` missing.

    Example:
        >>> # cmd_delete(Namespace(resource="secret", name="OLD_KEY"))  # doctest: +SKIP
    """
    res = (getattr(args, "resource", "") or "").lower()
    name = getattr(args, "name", None)
    out = _out(args)
    team = getattr(args, "team", None)
    project = getattr(args, "project", None)
    if res in ("secret", "secrets"):
        if not name:
            sys.exit("delete secret NAME")
        key = urllib.parse.quote(name, safe="/")
        data = _proj_api("DELETE", f"/secrets/{key}", project=project)
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k != "value"}
        emit(data, out)
        return
    if res in ("team", "teams"):
        if not name:
            sys.exit("delete team NAME")
        _require_confirm(args, "delete team")
        emit(_mgmt_api("DELETE", f"/teams/{urllib.parse.quote(name, safe='')}"), out)
        return
    if res in ("project", "projects"):
        if not name:
            sys.exit("delete project NAME")
        _require_confirm(args, "delete project")
        emit(_mgmt_api("DELETE", f"/projects/{urllib.parse.quote(name, safe='')}"), out)
        return
    if res == "group":
        if not name or not team:
            sys.exit("delete group NAME --team TEAM")
        emit(_mgmt_api("DELETE", f"/teams/{urllib.parse.quote(team, safe='')}/groups/{urllib.parse.quote(name, safe='')}"), out)
        return
    if res in ("folder", "folders"):
        if not name:
            sys.exit("delete folder ID")
        emit(_mgmt_proj_api("DELETE", f"/folders/{urllib.parse.quote(name, safe='')}", project=project), out)
        return
    if res == "group-member":
        # defer to dedicated handler
        from corvus_cli.commands.access import cmd_group_member

        cmd_group_member(args, remove=True)
        return
    if res in ("member", "members"):
        if not name:
            sys.exit("delete member EMAIL [--team T]")
        if team:
            path = f"/teams/{urllib.parse.quote(team, safe='')}/members/{urllib.parse.quote(name, safe='')}"
            emit(_mgmt_api("DELETE", path), out)
        else:
            emit(_mgmt_proj_api("DELETE", f"/members/{urllib.parse.quote(name, safe='')}", project=project), out)
        return
    if res in ("token", "tokens"):
        if not name:
            sys.exit("delete token TOKEN_ID")
        emit(_mgmt_proj_api("DELETE", f"/tokens/{urllib.parse.quote(name, safe='')}", project=project), out)
        return
    if res in ("trash",):
        if getattr(args, "all", False):
            _require_pat()
            _require_confirm(args, "purge all trash")
            emit(_mgmt_proj_api("POST", "/trash/restore", body={"action": "purge"}, project=project), out)
            return
        if not name:
            sys.exit("delete trash SECRET_ID  (permanent purge)")
        _require_confirm(args, "purge trash item")
        emit(_mgmt_proj_api("DELETE", f"/trash/{urllib.parse.quote(name, safe='')}", project=project), out)
        return
    sug = _suggest_resource(res)
    hint = f"; did you mean {sug!r}?" if sug else ""
    sys.exit(f"cannot delete {res!r}{hint}")


def cmd_create(args) -> None:
    """Create resources (teams/groups/members/tokens/folders/projects).

    Description:
        Dispatches to the manage or project-scoped API, validates roles against
        the corresponding catalog (team/project/service), handles token
        ``--scope`` and ``--expires-days`` (warns when absent for long-lived
        tokens), and prints the raw token to ``stderr`` once when returned.

    Inputs:
        args: namespace with ``resource``, ``name``, ``team``, ``role``, ``email``,
        ``group``, ``source``, ``external_key``, ``scope``, ``expires_days``,
        ``project``, ``output`` plus value flags when creating a secret.

    Outputs:
        Table/JSON of created object; exits on unknown resource or invalid role.

    Example:
        >>> # cmd_create(Namespace(resource="token", name="ci", role="service-write"))  # doctest: +SKIP
        token (copy now): ss_…
    """
    res = (getattr(args, "resource", "") or "").lower()
    name = getattr(args, "name", None)
    out = _out(args)
    team = getattr(args, "team", None)
    role = getattr(args, "role", None)
    email = getattr(args, "email", None)
    project = getattr(args, "project", None)

    if res in ("team", "teams"):
        if not name:
            sys.exit("create team NAME")
        emit(_mgmt_api("POST", "/teams", body={"name": name}), out)
        return
    if res == "group":
        if not name or not team:
            sys.exit("create group NAME --team TEAM")
        body: dict = {"name": name}
        if getattr(args, "source", None):
            body["source"] = args.source
        if getattr(args, "external_key", None):
            body["external_key"] = args.external_key
        emit(_mgmt_api("POST", f"/teams/{urllib.parse.quote(team, safe='')}/groups", body=body), out)
        return
    if res == "group-member":
        from corvus_cli.commands.access import cmd_group_member

        cmd_group_member(args, remove=False)
        return
    if res in ("folder", "folders"):
        if not name:
            sys.exit("create folder PATH")
        emit(_mgmt_proj_api("POST", "/folders", body={"path": name}, project=project), out)
        return
    if res in ("project", "projects"):
        if not name or not team:
            sys.exit("create project NAME --team TEAM")
        path = f"/teams/{urllib.parse.quote(team, safe='')}/projects"
        emit(_mgmt_api("POST", path, body={"name": name}), out)
        return
    if res in ("member", "members"):
        email = name or email
        if not email:
            sys.exit("create member EMAIL --role ROLE [--team T]")
        if team:
            role = _valid_role(role, "team-member", _TEAM_ROLES)
            emit(_mgmt_api("POST", f"/teams/{urllib.parse.quote(team, safe='')}/members", body={"email": email, "role": role}), out)
        else:
            role = _valid_role(role, "project-read", _PROJECT_ROLES)
            emit(_mgmt_proj_api("POST", "/members", body={"email": email, "role": role}, project=project), out)
        return
    if res in ("token", "tokens"):
        role = _valid_role(role, "service-reveal", _SERVICE_ROLES)
        if not name:
            print("using default token name 'cli'", file=sys.stderr)
        body = {"name": name or "cli", "role": role}
        if getattr(args, "expires_days", None) is not None:
            ed = args.expires_days
            if ed <= 0:
                sys.exit("--expires-days must be a positive integer")
            body["expires_days"] = ed
        else:
            print("warning: no --expires-days; token will not expire (consider --expires-days 90)", file=sys.stderr)
        scope = getattr(args, "scope", None)
        if scope:
            keys = [k for k in re.split(r"[\s,]+", scope) if k]
            body["scope"] = keys
        data = _mgmt_proj_api("POST", "/tokens", body=body, project=project)
        if isinstance(data, dict) and data.get("token"):
            extra = ""
            if body.get("scope"):
                extra = f" scope={','.join(body['scope'])}"  # type: ignore[index]
            print(f"token (copy now): {data['token']}{extra}", file=sys.stderr)
        emit(data, out)
        return
    if res in ("secret", "secrets"):
        setattr(args, "resource", "secret")
        cmd_apply(args)
        return
    sug = _suggest_resource(res)
    hint = f"; did you mean {sug!r}?" if sug else ""
    sys.exit(f"cannot create {res!r}{hint}")


def cmd_restore(args) -> None:
    """Restore soft-deleted secrets from trash.

    Description:
        Supports ``restore trash <id>`` and ``restore trash --all`` (PAT-only).

    Inputs:
        args: namespace with ``resource``, ``name``, ``all``, ``project``,
        ``output``.

    Outputs:
        Server response via emit; exits on bad resource.

    Example:
        >>> # cmd_restore(Namespace(resource="trash", name="sec-id"))  # doctest: +SKIP
    """
    if (getattr(args, "resource", "") or "").lower() not in ("trash",):
        sys.exit("restore trash SECRET_ID")
    project = getattr(args, "project", None)
    if getattr(args, "all", False):
        _require_pat()
        emit(_mgmt_proj_api("POST", "/trash/restore", body={"action": "restore"}, project=project), _out(args))
        return
    if not getattr(args, "name", None):
        sys.exit("restore trash SECRET_ID  (or restore trash --all)")
    emit(
        _mgmt_proj_api(
            "POST",
            f"/trash/{urllib.parse.quote(args.name, safe='')}/restore",
            body={},
            project=project,
        ),
        _out(args),
    )


def cmd_transfer(args) -> None:
    """Transfer team ownership.

    Description:
        POSTs ``/teams/<name>/transfer`` with target ``email``.

    Inputs:
        args: namespace with ``resource``, ``name`` (team), ``email``,
        ``output``.

    Outputs:
        Server response via emit; exits on missing email/name.

    Example:
        >>> # cmd_transfer(Namespace(resource="team", name="Platform", email="a@x"))  # doctest: +SKIP
    """
    if (getattr(args, "resource", "") or "").lower() not in ("team", "teams"):
        sys.exit("transfer team NAME --email user@x")
    if not getattr(args, "name", None) or not getattr(args, "email", None):
        sys.exit("transfer team NAME --email user@x")
    path = f"/teams/{urllib.parse.quote(args.name, safe='')}/transfer"
    emit(_mgmt_api("POST", path, body={"email": args.email}), _out(args))
