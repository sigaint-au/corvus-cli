"""HTTP and API helpers for Corvus.

Description:
    Thin wrappers around :mod:`urllib.request` for the Corvus server:
    ``/eso/v1`` (machine + PAT) and ``/api/v1/manage`` (PAT only). Provides
    credential-aware routing, project scoping, timeout clamping, JSON
    error extraction and transparent server pagination for list endpoints.
    No third-party deps.

Inputs:
    Credentials via :mod:`corvus_cli.config` (env/config file), plus method,
    path, optional ``body``/``query``/``project`` args.

Outputs:
    Parsed JSON payloads (``dict``/``list``/``{}``); calls ``sys.exit`` with
    ``HTTP {code}: {error}`` or ``request failed: …`` on transport failure.
    For GET list endpoints that return ``{"items": [...]}`` pagination is
    handled automatically (cursor, Link header, page/offset styles).

Example:
    >>> from corvus_cli.api import _api  # doctest: +SKIP
    >>> _api("GET", "/projects", pat_only=True)  # doctest: +SKIP
    {'items': […]}
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from corvus_cli.config import _require_creds, _require_pat, _timeout
from corvus_cli.constants import _UUID_RE

# ── pagination globals (set by CLI from --no-paginate / --limit / --page-size) ──
_NO_PAGINATE = False
_PAGINATE_LIMIT: int | None = None
_PAGINATE_PAGE_SIZE: int | None = None
_MAX_PAGES = int(os.environ.get("SS_MAX_PAGES", "100"))
_MAX_ITEMS = int(os.environ.get("SS_MAX_ITEMS", "50000"))


def set_pagination_config(*, no_paginate: bool = False, limit: int | None = None, page_size: int | None = None) -> None:
    """Configure pagination behaviour for the current invocation.

    Description:
        Called from :mod:`corvus_cli.cli` after argument parsing. Controls
        whether auto-pagination is disabled and caps total items / page size.

    Inputs:
        no_paginate: when True, fetch only the first page.
        limit: max total items to return (client-side cap, ``None`` = no cap).
        page_size: page size hint passed to the server as ``limit``/``per_page``.

    Outputs:
        None; mutates module globals.

    Example:
        >>> set_pagination_config(no_paginate=True)
        >>> set_pagination_config(limit=100, page_size=50)
    """
    global _NO_PAGINATE, _PAGINATE_LIMIT, _PAGINATE_PAGE_SIZE
    _NO_PAGINATE = bool(no_paginate)
    _PAGINATE_LIMIT = limit
    _PAGINATE_PAGE_SIZE = page_size


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
    data, _ = _http_with_headers(method, url, token=token, body=body, query=query)
    return data


def _http_with_headers(
    method: str,
    url: str,
    *,
    token: str,
    body: dict | None = None,
    query: dict | None = None,
) -> tuple[object, dict[str, str]]:
    """Like :func:`_http` but also returns response headers.

    Description:
        Same request flow as :func:`_http` but captures headers for pagination
        (``Link: <url>; rel="next"``). Normalises errors identically.

    Inputs:
        Same as :func:`_http`.

    Outputs:
        ``(data, headers)`` where ``data`` is parsed JSON and ``headers`` is a
        case-preserving dict of response headers (lower-cased lookup is valid via
        iteration).

    Example:
        >>> _http_with_headers("GET", "https://h/eso/v1/projects", token="pat_…")  # doctest: +SKIP
        ({'items': []}, {'Link': '…'})
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
            headers = {k: v for k, v in resp.headers.items()}  # type: ignore[attr-defined]
            parsed = json.loads(raw) if raw else {}
            return parsed, headers
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace")[:500]
        try:
            msg = json.loads(err_body).get("error", err_body)
        except json.JSONDecodeError:
            msg = err_body or e.reason
        sys.exit(f"HTTP {e.code}: {msg}")
    except urllib.error.URLError as e:
        sys.exit(f"request failed: {e.reason}")


def _extract_next_token(
    data: dict,
    headers: dict[str, str] | None,
    current_query: dict[str, str],
    aggregated_len: int,
) -> dict[str, str] | None:
    """Derive pagination cursor/page for the next request.

    Description:
        Inspects ``Link`` header and common body fields (``next``,
        ``next_page_token``, ``page_token``, ``cursor`` as URL or token,
        ``next_page`` int, ``has_more`` with ``page``/``offset`` fallbacks,
        ``total``/``total_pages``). Returns ``{"__url": absolute_url}`` for
        Link/URL style or a query-update dict (``{"page_token": "…"}``,
        ``{"page": "2"}``, ``{"offset": "20"}``, …). ``None`` means no more
        pages.

    Inputs:
        data: parsed JSON body of the current page.
        headers: response headers (may be empty).
        current_query: query dict used for the current request.
        aggregated_len: total items fetched so far (for offset fallback).

    Outputs:
        Query update dict, ``{"__url": url}`` for Link/URL pagination, or
        ``None`` when pagination is finished.

    Example:
        >>> _extract_next_token({"next": "abc"}, {}, {}, 10)
        {'page_token': 'abc'}
        >>> _extract_next_token({"has_more": True, "page": 1}, {}, {"page": "1"}, 20)
        {'page': '2'}
    """
    # 1. Link header: <url>; rel="next"
    if headers:
        link_val = None
        for k, v in headers.items():
            if k.lower() == "link":
                link_val = v
                break
        if link_val:
            for part in link_val.split(","):
                if 'rel="next"' in part or "rel='next'" in part or 'rel=next' in part:
                    m = re.search(r"<([^>]+)>", part)
                    if m:
                        return {"__url": m.group(1).strip()}

    # 2. Explicit body tokens (URL or opaque)
    for key in ("next", "next_page_token", "page_token", "cursor", "next_cursor", "next_cursor_token"):
        val = data.get(key)
        if isinstance(val, str) and val:
            if val.startswith("http://") or val.startswith("https://") or val.startswith("/eso/") or val.startswith("/api/"):
                return {"__url": val}
            if "cursor" in key:
                return {"cursor": val}
            if "page" in key:
                return {"page_token": val}
            # generic next -> try page_token first, fallback to cursor handled by caller updating both?
            # Use page_token as primary; server will ignore unknown param but we also support cursor param via duplicate key?
            # For safety, return page_token; offset fallback below will handle cursor-only servers if they expect cursor param?
            # Check if current_query already uses cursor param -> keep using cursor
            if "cursor" in current_query:
                return {"cursor": val}
            return {"page_token": val}
        if isinstance(val, int) and key == "next_page":
            return {"page": str(val)}

    # 3. has_more / total_pages / total based
    has_more = data.get("has_more")
    if has_more is None:
        has_more = data.get("hasMore")
    total = data.get("total")
    total_pages = data.get("total_pages") or data.get("totalPages")
    cur_page_val = data.get("page")
    if cur_page_val is None:
        cur_page_val = current_query.get("page") or current_query.get("page_number")

    # If has_more true or total indicates more, try page increment
    should_paginate = False
    if has_more is True:
        should_paginate = True
    elif isinstance(total, int) and aggregated_len < total:
        should_paginate = True
    elif isinstance(total_pages, int) and isinstance(cur_page_val, (int, str)):
        try:
            if int(cur_page_val) < int(total_pages):
                should_paginate = True
        except Exception:
            pass

    if should_paginate:
        # Prefer page increment if page present
        if cur_page_val is not None:
            try:
                return {"page": str(int(cur_page_val) + 1)}
            except Exception:
                pass
        # Fallback to offset style if query uses offset/limit/per_page
        if any(k in current_query for k in ("offset", "limit", "per_page", "page_size")):
            # offset pagination
            try:
                cur_off = int(current_query.get("offset", "0") or 0)
                # step is per_page / limit / page_size or len of last page items
                per = current_query.get("limit") or current_query.get("per_page") or current_query.get("page_size")
                step = int(per) if per is not None else len(data.get("items") or []) or 20
                return {"offset": str(cur_off + step)}
            except Exception:
                pass
        # Generic page increment starting at 1 or 2
        try:
            cur = int(current_query.get("page", cur_page_val or "1") or 1)
            return {"page": str(cur + 1)}
        except Exception:
            return {"page": "2"}

    # 4. total fallback without has_more flag but total > aggregated
    if isinstance(total, int) and total > aggregated_len and total > len(data.get("items") or []):
        # try offset
        if any(k in current_query for k in ("offset", "limit", "per_page")) or True:
            # we still try page increment as generic cursor fallback
            # Prefer offset if server likely uses it: when query has limit/per_page, use offset
            if "offset" in current_query or "limit" in current_query:
                try:
                    cur_off = int(current_query.get("offset", "0") or 0)
                    step = len(data.get("items") or []) or 20
                    return {"offset": str(cur_off + step)}
                except Exception:
                    pass
            # otherwise page increment
            try:
                cur = int(current_query.get("page", cur_page_val or "1") or 1)
                return {"page": str(cur + 1)}
            except Exception:
                return {"page": "2"}

    return None


def _paginated_http(
    method: str,
    url: str,
    token: str,
    query: dict[str, str] | None,
    body: dict | None = None,
) -> object:
    """GET helper that transparently follows pagination.

    Description:
        For non-GET or when :data:`_NO_PAGINATE` is set, delegates to
        :func:`_http`. For GET list responses (``{"items": [...]}``) it loops
        following ``Link`` header, body tokens, ``has_more``/``total`` etc.,
        accumulating ``items``. Respects ``_PAGINATE_LIMIT``,
        ``_PAGINATE_PAGE_SIZE``, ``_MAX_PAGES`` and ``_MAX_ITEMS`` safety
        caps, and loop detection via seen tokens/URLs.

    Inputs:
        method, url (without query string), token, query, body (same as
        :func:`_http`).

    Outputs:
        Aggregated JSON (``items`` merged) or single-page JSON when not
        paginated. Exits on HTTP errors via :func:`_http_with_headers`.

    Example:
        >>> _paginated_http("GET", "https://h/eso/v1/projects", "pat_…", {"q": ""})  # doctest: +SKIP
        {'items': […]}
    """
    if method != "GET" or body is not None or _NO_PAGINATE:
        return _http(method, url, token=token, body=body, query=query)

    # Apply page_size hint if set and not already in query
    query = dict(query or {})
    if _PAGINATE_PAGE_SIZE is not None:
        # Server param name varies: try limit/per_page/page_size; set limit as primary
        if "limit" not in query and "per_page" not in query and "page_size" not in query:
            query["limit"] = str(_PAGINATE_PAGE_SIZE)
    elif "limit" not in query and os.environ.get("SS_PAGE_SIZE"):
        try:
            ps = int(os.environ["SS_PAGE_SIZE"])
            query["limit"] = str(ps)
        except Exception:
            pass

    # Determine effective limit for client-side cap
    effective_limit = _PAGINATE_LIMIT
    if effective_limit is None and query.get("limit"):
        # do not treat server page limit as client cap
        pass

    all_items: list = []
    first_data: dict | None = None
    current_query = dict(query)
    current_url = url
    seen: set[tuple] = set()
    pages = 0

    # Use headers-aware fetch for pagination detection
    data, headers = _http_with_headers(method, current_url, token=token, query=current_query, body=body)

    if not isinstance(data, dict) or "items" not in data:
        return data

    # Fast path: no pagination signal
    # We still need to check if we need to paginate; if no signal, return single page
    # But we already have first page items
    items = data.get("items") or []
    all_items.extend(items)
    first_data = data
    pages = 1

    # Check limit cap after first page
    if effective_limit is not None and len(all_items) >= effective_limit:
        truncated = all_items[:effective_limit]
        result = dict(first_data)
        result["items"] = truncated
        for k in ("next", "next_page", "next_page_token", "page_token", "cursor", "next_cursor", "has_more", "hasMore", "total", "total_pages"):
            result.pop(k, None)
        return result

    while True:
        # Determine next pagination
        nxt = _extract_next_token(data, headers, current_query, len(all_items))
        if not nxt:
            break
        # Loop detection
        key = tuple(sorted(nxt.items()))
        if key in seen:
            print("warning: pagination loop detected; stopping", file=sys.stderr)
            break
        seen.add(key)
        if pages >= _MAX_PAGES:
            print(f"warning: reached max pages ({_MAX_PAGES}); results may be truncated (use --page-size / --limit)", file=sys.stderr)
            break
        if len(all_items) >= _MAX_ITEMS:
            print(f"warning: reached max items ({_MAX_ITEMS}); stopping pagination", file=sys.stderr)
            break
        # Fetch next page
        if "__url" in nxt:
            next_url = nxt["__url"]
            # Resolve relative URLs
            if next_url.startswith("/"):
                # prepend scheme+host from current_url
                parsed = urllib.parse.urlparse(current_url)
                next_url = f"{parsed.scheme}://{parsed.netloc}{next_url}"
            # When URL already contains query, pass query=None
            data, headers = _http_with_headers("GET", next_url, token=token, query=None, body=None)
            # Extract query for next iteration's loop detection (parse from URL)
            try:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(next_url).query)
                current_query = {k: v[0] for k, v in qs.items()}
            except Exception:
                current_query = {}
            current_url = next_url  # for further Link resolution, keep absolute
        else:
            # Update query with pagination token/page
            current_query = dict(current_query)
            current_query.update(nxt)
            data, headers = _http_with_headers(method, url, token=token, query=current_query, body=body)

        if not isinstance(data, dict) or "items" not in data:
            # Unexpected non-list, break
            break
        items = data.get("items") or []
        if not items:
            # Empty page -> done
            break

        # Apply limit slicing if needed
        if effective_limit is not None:
            remaining = effective_limit - len(all_items)
            if remaining <= 0:
                break
            if len(items) > remaining:
                items = items[:remaining]
                all_items.extend(items)
                break

        all_items.extend(items)
        pages += 1

        # If we hit limit, stop
        if effective_limit is not None and len(all_items) >= effective_limit:
            break

    # Merge into first_data
    if first_data is None:
        return {}
    if len(all_items) == len(first_data.get("items") or []):
        # No extra pages fetched; return original to preserve pagination fields? But we still want consistent output without pagination fields?
        # Keep original data as is (avoid mutating)
        return first_data
    result = dict(first_data)
    result["items"] = all_items[:effective_limit] if effective_limit is not None else all_items
    # Remove pagination markers
    for k in ("next", "next_page", "next_page_token", "page_token", "cursor", "next_cursor", "next_cursor_token", "has_more", "hasMore", "Link"):
        result.pop(k, None)
    # Keep total if present but ensure it matches? Optionally update total to len
    return result


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
        is set, exits if the token is ``ss_``. For GET list calls pagination
        is handled transparently.

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
    url = f"{base}/eso/v1{path}"
    if method == "GET":
        return _paginated_http(method, url, token=token, query=query, body=body)
    return _http(method, url, token=token, body=body, query=query)


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
        machine tokens carry a UUID, then performs the HTTP call. GET list
        results are auto-paginated.

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
    url = f"{base}/eso/v1/projects/{proj}{path}"
    if method == "GET":
        return _paginated_http(method, url, token=token, query=query, body=body)
    return _http(method, url, token=token, body=body, query=query)


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
        GET lists are auto-paginated.

    Inputs:
        method, path, optional body/query.

    Outputs:
        Parsed JSON; exits when token is not PAT/sso.

    Example:
        >>> _mgmt_api("GET", "/teams")  # doctest: +SKIP
        {'items': […]}
    """
    base, token = _require_pat()
    url = f"{base}/api/v1/manage{path}"
    if method == "GET":
        return _paginated_http(method, url, token=token, query=query, body=body)
    return _http(method, url, token=token, body=body, query=query)


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
        GET lists are auto-paginated.

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
    url = f"{base}/api/v1/manage/projects/{proj}{path}"
    if method == "GET":
        return _paginated_http(method, url, token=token, query=query, body=body)
    return _http(method, url, token=token, body=body, query=query)


def _admin_api(
    method: str,
    path: str,
    *,
    query: dict | None = None,
) -> object:
    """Call a global-admin endpoint; hint when token lacks admin rights.

    Description:
        Delegates to :func:`_mgmt_api` and re-raises 403 with an admin hint.
        GET lists are auto-paginated via :func:`_mgmt_api`.

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
