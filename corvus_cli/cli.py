"""CLI entrypoint for Corvus.

Description:
    Thin ``main()`` dispatcher: prints version/usage for no-arg/``-h``/
    ``--version``, builds the :mod:`corvus_cli.parser` parser, wires
    ``--no-trunc`` into :mod:`corvus_cli.output`, pagination flags into
    :mod:`corvus_cli.api`, and invokes the selected subcommand handler.
    Safe to import (no side-effects beyond re-exporting public symbols for
    legacy tests importing ``corvus`` as a module).

Inputs:
    Command-line ``argv`` list (defaults to ``sys.argv[1:]``).

Outputs:
    Dispatches to sub-command handlers which write to stdout/stderr and may
    ``sys.exit``. Returns ``None`` when a handler returns.

Example:
    >>> from corvus_cli.cli import main  # doctest: +SKIP
    >>> main(["--version"])  # prints corvus 1.0.0 then exits 0
"""

from __future__ import annotations

import sys

from corvus_cli import VERSION
from corvus_cli.constants import USAGE
from corvus_cli.parser import build_parser

# Re-exports for tests importing ``corvus`` as a legacy single-file module.
# The top-level ``corvus`` shim does ``from corvus_cli.cli import *`` equivalent
# so old test paths keep working. Additional symbols are pulled from submodules
# in that shim directly; we expose the ones that live here.
__all__ = ["VERSION", "USAGE", "build_parser", "main"]

# Noun-first → verb-first argv translation. New syntax is thin sugar:
# only leading tokens move, all flags pass through verbatim, so each noun
# verb reuses its legacy parser (flags, -o, pagination) with zero duplication.
_NOUN_MAP: dict[tuple[str, ...], list[str]] = {
    ("secret", "list"): ["get", "secrets"],
    ("secret", "get"): ["get", "secret"],
    ("secret", "set"): ["apply", "secret"],
    ("secret", "delete"): ["delete", "secret"],
    ("secret", "reveal"): ["reveal", "secret"],
    ("secret", "history"): ["get", "history"],
    ("secret", "export"): ["export"],
    ("folder", "list"): ["get", "folders"],
    ("folder", "create"): ["create", "folder"],
    ("folder", "delete"): ["delete", "folder"],
    ("request", "list"): ["get", "requests"],
    ("request", "approve"): ["approve"],
    ("request", "deny"): ["deny"],
    ("binding", "grant"): ["grant", "secret"],
    ("binding", "revoke"): ["unbind", "secret"],
    ("team", "list"): ["get", "teams"],
    ("team", "get"): ["get", "team"],
    ("team", "create"): ["create", "team"],
    ("team", "delete"): ["delete", "team"],
    ("team", "transfer"): ["transfer", "team"],
    ("member", "list"): ["get", "members"],
    ("member", "add"): ["create", "member"],
    ("member", "remove"): ["delete", "member"],
    ("group", "list"): ["get", "groups"],
    ("group", "create"): ["create", "group"],
    ("group", "delete"): ["delete", "group"],
    ("token", "list"): ["get", "tokens"],
    ("token", "create"): ["create", "token"],
    ("token", "delete"): ["delete", "token"],
    ("trash", "list"): ["get", "trash"],
    ("trash", "restore"): ["restore", "trash"],
    ("trash", "purge"): ["delete", "trash"],
    ("user", "list"): ["get", "users"],
    ("user", "get"): ["get", "user"],
    ("audit", "list"): ["get", "audit"],
    ("project", "list"): ["get", "projects"],
    ("project", "get"): ["get", "project"],
    ("project", "create"): ["create", "project"],
    ("project", "delete"): ["delete", "project"],
    ("project", "use"): ["project"],
}
_NOUNS = frozenset(n for (n, *_) in _NOUN_MAP) | {"group", "project"}


def _rewrite_noun_first(argv: list[str]) -> list[str]:
    """Translate noun-first argv to legacy verb-first form."""
    if not argv or argv[0] not in _NOUNS:
        return argv
    # Three-token form: `group member add|remove EMAIL …`
    if argv[0] == "group" and len(argv) > 2 and argv[1] == "member" and argv[2] in ("add", "remove"):
        legacy = ["create" if argv[2] == "add" else "delete", "group-member"]
        return legacy + argv[3:]
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        verbs = sorted(v for (n, v) in _NOUN_MAP if n == argv[0])
        if argv[0] == "group":
            verbs.append("member add|remove")
        print(f"usage: corvus {argv[0]} ({'|'.join(verbs)}) …")
        sys.exit(0)
    mapped = _NOUN_MAP.get((argv[0], argv[1]))
    if mapped is None:
        # `corvus project NAME` is the legacy switcher, not a verb.
        if argv[0] == "project":
            return argv
        verbs = sorted(v for (n, v) in _NOUN_MAP if n == argv[0])
        if argv[0] == "group":
            verbs.append("member add|remove")
        sys.exit(f"unknown verb {argv[1]!r} for {argv[0]!r} (valid: {'|'.join(verbs)})")
    return mapped + argv[2:]


def main(argv: list[str] | None = None) -> None:
    """Dispatch the Corvus CLI.

    Description:
        Normalizes ``argv``, handles ``-h``/``--help``/``--version``/empty early
        exits, builds the parser, propagates ``--no-trunc`` to the output
        module and ``--no-paginate``/``--limit``/``--page-size`` to the API
        pagination layer, and invokes the chosen command. When no subcommand
        matches (e.g. ``corvus`` with empty argv is already handled), prints
        usage.

    Inputs:
        argv: list of string args without the program name; defaults to
        ``sys.argv[1:]`` when ``None``.

    Outputs:
        None; handlers write to ``stdout``/``stderr`` and may exit. Raises
        :exc:`SystemExit` for ``--version``/``--help``/errors.

    Example:
        >>> main(["help"])  # doctest: +SKIP
        corvus 1.0.0 - command-line client …
    """
    argv = _rewrite_noun_first(list(sys.argv[1:] if argv is None else argv))
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE, end="")
        sys.exit(0)
    if argv[0] in ("-V", "--version"):
        print(f"corvus {VERSION}")
        sys.exit(0)
    p = build_parser()
    args = p.parse_args(argv)
    # Wire --no-trunc (present on every subparser + top-level) into output.
    if getattr(args, "no_trunc", False):
        from corvus_cli.output import set_no_trunc

        set_no_trunc(True)
    # Wire pagination flags into api layer.
    try:
        from corvus_cli.api import set_pagination_config

        set_pagination_config(
            no_paginate=bool(getattr(args, "no_paginate", False)),
            limit=getattr(args, "limit", None),
            page_size=getattr(args, "page_size", None),
        )
    except Exception:
        pass
    if not getattr(args, "func", None):
        print(USAGE, end="")
        sys.exit(0)
    args.func(args)
