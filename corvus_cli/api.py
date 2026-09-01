"""HTTP and API helpers for Corvus.

Description:
    Thin wrappers around :mod:`urllib.request` for the Corvus server:
    ``/eso/v1`` (machine + PAT) and ``/api/v1/manage`` (PAT only). Provides
    credential-aware routing, project scoping, timeout clamping, and JSON
    error extraction. No third-party deps.

Inputs:
    Credentials via :mod:`corvus_cli.config` (env/config file), plus method,
    path, optional ``body``/``query``/``project`` args.

Outputs:
    Parsed JSON payloads (``dict``/``list``/``{}``); calls ``sys.exit`` with
    ``HTTP {code}: {error}`` or ``request failed: …`` on transport failure.

Example:
    >>> from corvus_cli.api import _api  # doctest: +SKIP
    >>> _api("GET", "/projects", pat_only=True)  # doctest: +SKIP
    {'items': […]}
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from corvus_cli.config import _require_creds, _require_pat, _timeout
from corvus_cli.constants import _UUID_RE


def _http(
    method: str,
    url: str,
    *,
    token: str,
    body: dict | None = None,
    query: dict | None = None,
) -> object:
    """Perform a JSON HTTP request and return the parsed body.

    Description:
        Encodes *query* (skipping ``None``/``""``), JSON-encodes *body* when
        present, sets ``Authorization: Bearer`` and ``Accept: application/json``,
        applies ``SS_TIMEOUT``-derived timeout, and normalizes ``HTTPError`` to
        ``sys.exit("HTTP {code}: {error}")`` where ``error`` is the server's
        ``error`` field when available.

    Inputs:
        method: ``GET``/``POST``/``PATCH``/``PUT``/``DELETE``. url: absolute URL.
        token: bearer token. body: JSON-serialisable dict or ``None``. query:
        dict of query params; ``None``/``""`` values are stripped.

    Outputs:
        Parsed JSON (often ``dict`` with ``items`` or a single object), or
        ``{}`` when body empty. Exits on network/HTTP error.

    Example:
        >>> _http("GET", "https://h/eso/v1/projects", token="pat_…")  # doctest: +SKIP
        {'items': []}
    """
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None and v != ""})
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace")[:500]
        try:
            msg = json.loads(err_body).get("error", err_body)
        except json.JSONDecodeError:
            msg = err_body or e.reason
        sys.exit(f"HTTP {e.code}: {msg}")
    except urllib.error.URLError as e:
        sys.exit(f"request failed: {e.reason}")


def _api(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    query: dict | None = None,
    pat_only: bool = False,
) -> object:
    """Call ``/eso/v1{path}`` with current credentials.

    Description:
        Resolves base URL/token via :func:`_require_creds`; when ``pat_only``
        is set, exits if the token is ``ss_``.

    Inputs:
        method, path (with leading ``/``), optional body/query, pat_only flag.

    Outputs:
        Parsed JSON response; exits on missing creds or wrong token kind.

    Example:
        >>> _api("GET", "/projects", pat_only=True)  # doctest: +SKIP
        {'items': []}
    """
    base, token, _, kind = _require_creds()
    if pat_only and kind != "pat":
        sys.exit("this command requires a pat_… or sso_… token")
    return _http(method, f"{base}/eso/v1{path}", token=token, body=body, query=query)


def _proj_api(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    query: dict | None = None,
    project: str | None = None,
) -> object:
    """Call ``/eso/v1/projects/<project>{path}``.

    Description:
        Resolves current project (or *project* override), validates that
        machine tokens carry a UUID, then performs the HTTP call.

    Inputs:
        method, path (suffix after ``/projects/<id>``), body/query, optional
        project override (UUID or — for PAT — unique name).

    Outputs:
        Parsed JSON; exits when project missing or UUID rule violated.

    Example:
        >>> _proj_api("GET", "/secrets", query={"meta": "1"})  # doctest: +SKIP
        {'items': […]}
    """
    base, token, conf_proj, kind = _require_creds()
    project = project or conf_proj
    if not project:
        sys.exit("no project set; corvus project <name>")
    if kind == "machine" and not _UUID_RE.match(project):
        sys.exit("machine tokens require project UUID")
    proj = urllib.parse.quote(project, safe="")
    return _http(method, f"{base}/eso/v1/projects/{proj}{path}", token=token, body=body, query=query)


def _mgmt_api(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    query: dict | None = None,
) -> object:
    """Call ``/api/v1/manage{path}`` (PAT only).

    Description:
        PAT-gated manage surface for teams/projects/members/tokens/groups.

    Inputs:
        method, path, optional body/query.

    Outputs:
        Parsed JSON; exits when token is not PAT/sso.

    Example:
        >>> _mgmt_api("GET", "/teams")  # doctest: +SKIP
        {'items': […]}
    """
    base, token = _require_pat()
    return _http(method, f"{base}/api/v1/manage{path}", token=token, body=body, query=query)


def _mgmt_proj_api(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    query: dict | None = None,
    project: str | None = None,
) -> object:
    """Call ``/api/v1/manage/projects/<project>{path}`` (PAT only).

    Description:
        Project-scoped manage endpoints; PAT is required and machine-token UUID
        rule is enforced. Accepts explicit *project* override (``--project``).

    Inputs:
        method, path suffix, optional body/query/project.

    Outputs:
        Parsed JSON; exits on missing project or wrong token kind.

    Example:
        >>> _mgmt_proj_api("GET", "/tokens")  # doctest: +SKIP
        {'items': […]}
    """
    base, token, conf_proj, kind = _require_creds()
    project = project or conf_proj
    if kind != "pat":
        sys.exit("this command requires a pat_… or sso_… token")
    if kind == "machine" and project and not _UUID_RE.match(project):
        sys.exit("machine tokens require project UUID")
    if not project:
        sys.exit("no project set; corvus project <name>")
    proj = urllib.parse.quote(project, safe="")
    return _http(method, f"{base}/api/v1/manage/projects/{proj}{path}", token=token, body=body, query=query)


def _admin_api(
    method: str,
    path: str,
    *,
    query: dict | None = None,
) -> object:
    """Call a global-admin endpoint; hint when token lacks admin rights.

    Description:
        Delegates to :func:`_mgmt_api` and re-raises 403 with an admin hint.

    Inputs:
        method, path, optional query.

    Outputs:
        Parsed JSON on success; exits with hint on 403.

    Example:
        >>> _admin_api("GET", "/admin/users")  # doctest: +SKIP
        {'items': […]}
    """
    try:
        return _mgmt_api(method, path, query=query)
    except SystemExit as e:
        if "403" in str(e):
            sys.exit(f"{e}\nhint: requires a global admin pat_… or sso_… token")
        raise
