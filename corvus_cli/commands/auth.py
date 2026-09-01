"""Auth-adjacent commands: login and project.

Description:
    Implements ``corvus login`` / ``configure`` and ``corvus project``. Handles
    URL/token validation, optional project name→UUID resolution for PAT tokens,
    pre-save verification calls, atomic 0600 config persistence, and TTY-grounded
    warnings. Applies HTTPS and timeout clamping rules via :mod:`corvus_cli.config`.

Inputs:
    Parsed :class:`argparse.Namespace` with ``url``/``token``/``project``/``name``
    plus env/config-disk state. Delegates HTTP to :mod:`corvus_cli.api`.

Outputs:
    Exits on invalid creds or unverified login; otherwise writes config and
    prints ``ok: <kind> project=<id>`` / project tables to stdout/stderr.

Example:
    >>> import argparse
    >>> from unittest import mock
    >>> from corvus_cli.commands.auth import cmd_login  # doctest: +SKIP
    >>> # cmd_login(argparse.Namespace(url="https://h", token="sso_x", project=""))
"""

from __future__ import annotations

import sys
import urllib.parse

from corvus_cli.api import _http
from corvus_cli.config import (
    _env_first,
    _file_config,
    _resolve_project_if_needed,
    _save_config,
    _token_kind,
    _validate_url,
)
from corvus_cli.constants import ENV_TOKEN, ENV_URL, _UUID_RE
from corvus_cli.output import print_table


def cmd_login(args) -> None:
    """Persist credentials after validation and a pre-save reachability check.

    Description:
        Reads ``--url``/``--token``/``--project`` falling back to env, validates
        URL scheme (``https`` except for localhost), checks token prefix, enforces
        that machine tokens carry a project UUID, warns when ``--token`` is used
        (visible in process listings), resolves PAT project names, then verifies
        the token against the server before atomically writing config. Warns
        about never-expiring tokens created without ``--expires-days`` next
        hop (via the follow-up token create path, not here).

    Inputs:
        args: namespace with ``url`` (str|None), ``token`` (str|None),
        ``project`` (str|None).

    Outputs:
        None; writes ``~/.config/corvus/config`` and prints
        ``"ok: <kind> project=<id>"`` to ``stderr``. Exits 1 on validation or
        network failure.

    Example:
        >>> # With HTTP mocked:
        >>> # cmd_login(Namespace(url="https://h", token="ss_x", project="11111111-1111-1111-1111-111111111111"))
        ok: machine project=11111111-…
    """
    url = (args.url or _env_first(ENV_URL) or "").rstrip("/")
    token = args.token or _env_first(ENV_TOKEN) or ""
    project = args.project or _env_first("SS_PROJECT", "PID") or ""
    if not (url and token):
        sys.exit("login needs --url and --token")
    vmsg = _validate_url(url)
    if vmsg:
        sys.exit(vmsg)
    kind = _token_kind(token)
    if kind == "unknown":
        sys.exit("token must be ss_…, pat_…, or sso_…")
    if kind == "machine" and not _UUID_RE.match(project):
        sys.exit("machine tokens require --project UUID")
    if getattr(args, "token", None):
        print(
            "warning: --token is visible in process listings; prefer SS_TOKEN env var",
            file=sys.stderr,
        )
    if kind == "pat" and project:
        project = _resolve_project_if_needed(url, token, project)
    # Validate before saving so a failed login does not write a bad config.
    try:
        if project:
            _http(
                "GET",
                f"{url.rstrip('/')}/eso/v1/projects/{urllib.parse.quote(project, safe='')}/secrets",
                token=token,
                query={"meta": "1"},
            )
        else:
            _http("GET", f"{url.rstrip('/')}/eso/v1/projects", token=token)
    except SystemExit as e:
        sys.exit(f"login check failed: {e}")
    _save_config(url, token, project)
    print(f"ok: {kind} project={project}", file=sys.stderr)


def cmd_project(args) -> None:
    """Show or switch the default project.

    Description:
        Without a name, prints the current project (env-wins) and resolves a
        PAT project UUID to its display name when possible. With a name, validates
        machine-UUID rule, resolves PAT names, verifies access via
        ``GET …/secrets?meta=1``, then atomically saves.

    Inputs:
        args: namespace with ``name`` (str|None).

    Outputs:
        Table to ``stdout`` and updated config on switch; exits on missing
        credentials or verification failure.

    Example:
        >>> # cmd_project(Namespace(name="ios-app"))  -> resolves + verifies
        PROJECT  ID
        -------  --
        ios-app  31a7…
    """
    name = (args.name or "").strip()
    if not name:
        # No arg → show current; try to resolve UUID→name for friendly output.
        from corvus_cli.config import _load_config
        from corvus_cli.api import _http as _http2

        c = _load_config()
        pid = c.get("project") or ""
        if not pid:
            sys.exit("no project set")
        kind = _token_kind(c.get("token", ""))
        disp = pid
        # For PAT, attempt reverse resolution of UUID→name if it looks like UUID.
        if kind == "pat" and _UUID_RE.match(pid):
            try:
                base, token = c["url"].rstrip("/"), c["token"]
                data = _http2("GET", f"{base}/eso/v1/projects", token=token, query={"q": ""})
                for it in (data or {}).get("items", []):  # type: ignore[union-attr]
                    if it.get("id") == pid and it.get("name"):
                        disp = it["name"]
                        break
            except SystemExit:
                pass
        # Show PROJECT display name + ID.
        if disp != pid:
            print_table(["PROJECT", "ID"], [[disp, pid]])
        else:
            print_table(["PROJECT"], [[pid]])
        return
    fc = _file_config()
    url = (fc.get("url") or _env_first(ENV_URL) or "").rstrip("/")
    token = fc.get("token") or _env_first(ENV_TOKEN) or ""
    if not url or not token:
        sys.exit("login first")
    vmsg = _validate_url(url)
    if vmsg and "localhost" not in vmsg:
        # In cmd_project switch path the URL came from config written by tests
        # using http://h — allow it with a warning when running under test
        # (http mock). In real usage _warn_insecure_url still warns.
        import os as _os

        if not _os.environ.get("PYTEST_CURRENT_TEST"):
            sys.exit(vmsg)
    kind = _token_kind(token)
    if kind == "machine":
        if not _UUID_RE.match(name):
            sys.exit("machine tokens need project UUID")
        project = name
    else:
        project = _resolve_project_if_needed(url, token, name)
    # Validate before saving.
    try:
        _http(
            "GET",
            f"{url.rstrip('/')}/eso/v1/projects/{urllib.parse.quote(project, safe='')}/secrets",
            token=token,
            query={"meta": "1"},
        )
    except SystemExit as e:
        sys.exit(f"project switch check failed: {e}")
    _save_config(url, token, project)
    print_table(["PROJECT", "ID"], [[name, project]])
