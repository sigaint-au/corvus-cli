"""Table/JSON/value output for the Corvus CLI.

Description:
    Rendering helpers used by every command: JSON pretty-printer, truncated table
    printer with ``--no-trunc`` support, scalar/detail formatters, and the
    central :func:`emit` dispatcher that normalizes ``-o``/``--output``.
    Also emits a pagination notice when the server response includes paging.

Inputs:
    Python objects (``dict`` with ``items``, single objects, scalars) and the
    requested output mode (``table``/``json``/``value``/``name``/``wide``).

Outputs:
    Text to ``stdout``/``stderr``; helpers return ``None``. ``emit`` may
    ``sys.exit`` when ``value`` mode lacks a ``value`` field.

Example:
    >>> from corvus_cli.output import emit
    >>> emit({"key": "API_KEY", "value": "s3cret"}, output="value")  # doctest: +SKIP
    s3cret
"""

from __future__ import annotations

import json
import sys

# Global set by argument parser for truncate opt-out.
_NO_TRUNC = False


def set_no_trunc(v: bool) -> None:
    """Set global no-truncate flag for table rendering.

    Description:
        When ``True``, :func:`print_table` and :func:`trunc` render cells in
        full. Called from :mod:`corvus_cli.parser` after ``--no-trunc`` parse.

    Inputs:
        v: ``True`` to disable truncation.

    Outputs:
        None; mutates module global ``_NO_TRUNC``.

    Example:
        >>> set_no_trunc(True); set_no_trunc(False)
    """
    global _NO_TRUNC
    _NO_TRUNC = v


def print_json(obj: object) -> None:
    """Print *obj* as pretty sorted JSON.

    Description:
        Uses ``indent=2, sort_keys=True`` and ``default=str`` for datetimes.

    Inputs:
        obj: any JSON-serialisable value.

    Outputs:
        JSON text to ``stdout``.

    Example:
        >>> print_json({"b": 2, "a": 1})  # doctest: +SKIP
        {
          "a": 1,
          "b": 2
        }
    """
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    """Print a fixed-width aligned table with truncation.

    Description:
        Computes column widths from headers and rows (capped at 48 unless
        ``--no-trunc``), prints header + separator + rows. ``None`` cells become
        ``""``, over-long cells are truncated with ``…`` when truncating.

    Inputs:
        headers: column titles. rows: 2D list of cell strings.

    Outputs:
        Table text to ``stdout``.

    Example:
        >>> print_table(["NAME", "ID"], [["ios-app", "p1"]])  # doctest: +SKIP
        NAME     ID
        --------  --
        ios-app  p1
    """
    cols = list(headers)
    data = [[("" if c is None else str(c)) for c in row] for row in rows]
    widths = [len(h) for h in cols]
    cap = 10_000 if _NO_TRUNC else 48
    for row in data:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], min(len(cell), cap))
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(cols)))
    print("  ".join("-" * widths[i] for i in range(len(cols))))
    for row in data:
        cells: list[str] = []
        for i in range(len(cols)):
            cell = row[i] if i < len(row) else ""
            if not _NO_TRUNC and len(cell) > 48:
                cell = cell[:47] + "…"
            cells.append(cell.ljust(widths[i]))
        print("  ".join(cells))


def trunc(s: str, n: int = 48) -> str:
    """Ellipsize *s* to *n* (no-op with ``--no-trunc``).

    Description:
        Replaces newlines with ``\\n``. When ``--no-trunc`` is active, returns
        *s* unchanged (newlines still escaped).

    Inputs:
        s: text. n: max length when truncating (default 48).

    Outputs:
        Possibly truncated string.

    Example:
        >>> trunc("a" * 60)
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa…'
        >>> trunc("a\\n b")  # doctest: +SKIP
        'a\\n b'
    """
    s = str(s).replace("\n", "\\n")
    if _NO_TRUNC:
        return s
    return s if len(s) <= n else s[: n - 1] + "…"


def _token_scope_label(scope) -> str:
    """Format token scope list for the SCOPE table column.

    Description:
        Normalizes server scope objects with ``secret_key`` or ``key_pattern``
        into ``"K1,glob/*"``. Returns ``"*"`` when scope is empty/None.

    Inputs:
        scope: list of dicts or ``None`` as returned by ``/tokens``.

    Outputs:
        Comma-joined label, e.g. ``"API_KEY,prod/*"`` or ``"*"``.

    Example:
        >>> _token_scope_label([{"secret_key": "API_KEY"}, {"key_pattern": "prod/*"}])
        'API_KEY,prod/*'
    """
    out: list[str] = []
    for s in scope or []:
        if isinstance(s, dict):
            if s.get("secret_key"):
                out.append(s["secret_key"])
            elif s.get("key_pattern"):
                out.append(s["key_pattern"])
    return ",".join(out) if out else "*"


def _emit_scalar(obj: dict) -> None:
    """Print a small field→value table for a flat object.

    Description:
        Falls back to JSON when nothing scalar is present.

    Inputs:
        obj: flat dict to render.

    Outputs:
        Table or JSON to ``stdout``.

    Example:
        >>> _emit_scalar({"name": "demo", "id": "p1"})  # doctest: +SKIP
        FIELD  VALUE
        -----  -----
        name   demo
        id     p1
    """
    rows = [[k, str(v)] for k, v in obj.items() if v is not None and not isinstance(v, (list, dict))]
    if rows:
        print_table(["FIELD", "VALUE"], rows)
    else:
        print_json(obj)


def _print_members(members: list[dict]) -> None:
    """Print a members sub-table.

    Description:
        Renders ``EMAIL/ROLE/USER_ID`` or ``(empty)`` when the list is empty.

    Inputs:
        members: list of member objects from the server.

    Outputs:
        Table text to ``stdout``.

    Example:
        >>> _print_members([{"email": "a@x", "role": "project-admin", "user_id": "u1"}])  # doctest: +SKIP
        EMAIL  ROLE           USER_ID
        -----  -------------  -------
        a@x    project-admin  u1
    """
    if not members:
        print("(empty)")
        return
    print_table(
        ["EMAIL", "ROLE", "USER_ID"],
        [[m.get("email", ""), m.get("role", ""), m.get("user_id", "")] for m in members],
    )


def _print_tokens(tokens: list[dict]) -> None:
    """Print a tokens sub-table.

    Description:
        Renders ``NAME/PREFIX/ROLE/EXPIRES`` for the detail sections of
        ``get project`` / ``get team``.

    Inputs:
        tokens: list of token objects as returned alongside project detail.

    Outputs:
        Table text to ``stdout``.
    """
    if not tokens:
        print("(empty)")
        return
    print_table(
        ["NAME", "PREFIX", "ROLE", "EXPIRES"],
        [
            [
                t.get("name", ""),
                t.get("token_prefix", ""),
                t.get("role", ""),
                t.get("expires_at") or "",
            ]
            for t in tokens
        ],
    )


def _maybe_warn_pagination(obj: object) -> None:
    """Warn on possible server-side pagination truncation.

    Description:
        If *obj* is a dict with a paginated ``items`` list and contains a
        ``next``/``next_page``/``has_more`` indicator (or ``total`` > ``len(items)``),
        prints a notice to ``stderr`` that the output may be partial.

    Inputs:
        obj: parsed API response.

    Outputs:
        None; may print a warning to ``stderr``.

    Example:
        >>> _maybe_warn_pagination({"items": [], "total": 50})  # doctest: +SKIP
        # warning on stderr
    """
    if not isinstance(obj, dict) or "items" not in obj:
        return
    items = obj.get("items")
    if not isinstance(items, list):
        return
    # Server signals that may indicate truncation
    if obj.get("next") or obj.get("next_page") or obj.get("has_more") or obj.get("page_token"):
        print("warning: server paginated results; output may be truncated (use -l/--selector to narrow)", file=sys.stderr)
        return
    total = obj.get("total")
    if isinstance(total, int) and total > len(items):
        print(f"warning: {total} total items but {len(items)} returned; output truncated", file=sys.stderr)


def emit(obj: object, output: str) -> None:
    """Dispatch rendering based on *output* mode.

    Description:
        Handles ``json``, ``value``, ``name``, and table modes. For ``table``:
        picks a column set based on the first item's keys (members, tokens,
        secrets, audit, users, groups, …), emits pagination warnings, and falls
        back to a generic header row.

    Inputs:
        obj: parsed API response (dict with ``items`` or single object).
        output: one of ``table``/``json``/``value``/``name``/``wide`` (``wide``
        behaves like ``table``).

    Outputs:
        Rendered text to ``stdout``/``stderr``; exits 1 on bad ``value`` mode.

    Example:
        >>> emit({"key": "API_KEY", "value": "s3cret"}, "value")  # doctest: +SKIP
        s3cret
    """
    if output == "json":
        print_json(obj)
        return
    if not isinstance(obj, dict):
        print_json(obj)
        return
    if output == "value":
        if "value" not in obj:
            sys.exit("no value field")
        sys.stdout.write(str(obj["value"]))
        if not str(obj["value"]).endswith("\n"):
            sys.stdout.write("\n")
        return
    if output == "name":
        print(obj.get("key") or obj.get("name") or obj.get("id") or "")
        return
    # table
    if "items" in obj:
        _maybe_warn_pagination(obj)
        items = obj.get("items") or []
        if not items:
            print("(empty)")
            return
        sample = items[0] or {}
        if "email" in sample and "scope" in sample:
            print_table(
                ["EMAIL", "TEAM", "TEAM_ROLE", "PROJECT", "PROJECT_ROLE"],
                [
                    [
                        it.get("email", ""),
                        it.get("team", ""),
                        it.get("team_role", ""),
                        it.get("project", ""),
                        it.get("project_role", ""),
                    ]
                    for it in items
                ],
            )
        elif "email" in sample and "is_global_admin" in sample:
            print_table(
                ["EMAIL", "NAME", "ADMIN", "DISABLED"],
                [
                    [
                        it.get("email", ""),
                        it.get("name", ""),
                        str(bool(it.get("is_global_admin"))),
                        "yes" if it.get("disabled_at") else "",
                    ]
                    for it in items
                ],
            )
        elif "team_name" in sample and "name" in sample:
            print_table(
                ["NAME", "TEAM", "ID"],
                [[it.get("name", ""), it.get("team_name", ""), it.get("id", "")] for it in items],
            )
        elif "token_prefix" in sample:
            print_table(
                ["NAME", "PREFIX", "ROLE", "SCOPE", "EXPIRES", "ID"],
                [
                    [
                        it.get("name", ""),
                        it.get("token_prefix", ""),
                        it.get("role", ""),
                        _token_scope_label(it.get("scope")),
                        it.get("expires_at") or "",
                        it.get("id", ""),
                    ]
                    for it in items
                ],
            )
        elif "project_count" in sample or ("role" in sample and "name" in sample and "email" not in sample):
            print_table(
                ["NAME", "ROLE", "PROJECTS", "ID"],
                [
                    [it.get("name", ""), it.get("role", ""), str(it.get("project_count", "")), it.get("id", "")]
                    for it in items
                ],
            )
        elif "secret_key" in sample or "action" in sample:
            print_table(
                ["WHEN", "ACTION", "ACTOR", "DETAIL"],
                [
                    [
                        it.get("created_at") or it.get("when_display") or "",
                        it.get("action", ""),
                        it.get("actor_email", ""),
                        it.get("secret_key") or it.get("detail") or it.get("summary") or "",
                    ]
                    for it in items
                ],
            )
        elif "key" in sample:
            has_folder = any(it.get("folder_path") for it in items)
            headers = ["KEY"]
            if has_folder:
                headers.append("FOLDER")
            headers.extend(["KIND", "NOTE", "EXPIRES"])
            rows: list[list[str]] = []
            for it in items:
                row = [it.get("key", "")]
                if has_folder:
                    row.append(it.get("folder_path") or "")
                row.extend(
                    [
                        it.get("kind", ""),
                        it.get("note", ""),
                        it.get("expires_at") or it.get("deleted_at") or "",
                    ]
                )
                rows.append(row)
            print_table(headers, rows)
        elif "email" in sample:
            print_table(
                ["EMAIL", "NAME", "ROLE", "USER_ID"],
                [[it.get("email", ""), it.get("name", ""), it.get("role", ""), it.get("user_id", "")] for it in items],
            )
        else:
            keys = list(sample.keys())[:6]
            print_table([k.upper() for k in keys], [[str(it.get(k, "")) for k in keys] for it in items])
        return
    # composite detail objects (get project / get team)
    if "project" in obj and "members" in obj:
        _emit_scalar(obj.get("project") or {})
        print("\nMembers:")
        _print_members(obj.get("members") or [])
        print("\nTokens:")
        _print_tokens(obj.get("tokens") or [])
        return
    if "team" in obj and "members" in obj:
        _emit_scalar(obj.get("team") or {})
        print("\nMembers:")
        _print_members(obj.get("members") or [])
        print("\nProjects:")
        projs = obj.get("projects") or []
        if projs:
            print_table(["NAME", "ID"], [[p.get("name", ""), p.get("id", "")] for p in projs])
        else:
            print("(empty)")
        return
    # single object → field table
    rows: list[list[str]] = []
    for k, v in obj.items():
        if k == "value" and output == "table":
            v = trunc(str(v), 64)
        if k == "token":
            rows.append([k, str(v)])
        elif v is not None and not isinstance(v, (list, dict)):
            rows.append([k, str(v)])
    if rows:
        print_table(["FIELD", "VALUE"], rows)
    else:
        print_json(obj)
