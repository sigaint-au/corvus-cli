"""CLI entrypoint for Corvus.

Description:
    Thin ``main()`` dispatcher: prints version/usage for no-arg/``-h``/
    ``--version``, builds the :mod:`corvus_cli.parser` parser, wires
    ``--no-trunc`` into :mod:`corvus_cli.output`, and invokes the selected
    subcommand handler. Safe to import (no side-effects beyond re-exporting
    public symbols for legacy tests importing ``corvus`` as a module).

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


def main(argv: list[str] | None = None) -> None:
    """Dispatch the Corvus CLI.

    Description:
        Normalizes ``argv``, handles ``-h``/``--help``/``--version``/empty early
        exits, builds the parser, propagates ``--no-trunc`` to the output
        module, and invokes the chosen command. When no subcommand matches
        (e.g. ``corvus`` with empty argv is already handled), prints usage.

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
    argv = list(sys.argv[1:] if argv is None else argv)
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
    if not getattr(args, "func", None):
        print(USAGE, end="")
        sys.exit(0)
    args.func(args)
