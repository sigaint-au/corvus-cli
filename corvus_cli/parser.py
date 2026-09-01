"""Argparse wiring for the Corvus CLI.

Description:
    Builds the top-level :class:`argparse.ArgumentParser` with all subcommands,
    global ``--no-trunc`` handling, and shell completion plumbing. Thin parser
    only — handlers live in :mod:`corvus_cli.commands.*`.

Inputs:
    None direct; uses :mod:`corvus_cli.constants` for help text and
    :mod:`corvus_cli.output` to wire ``--no-trunc``.

Outputs:
    :class:`argparse.ArgumentParser` via :func:`build_parser`; side effect of
    setting :data:`corvus_cli.output._NO_TRUNC` after parse via wrapper.

Example:
    >>> from corvus_cli.parser import build_parser
    >>> p = build_parser()
    >>> p.parse_args(["get", "secrets", "-o", "json"]).resource
    'secrets'
"""

from __future__ import annotations

import argparse
import sys

from corvus_cli.constants import _COMPLETION_SH


def _add_output(p: argparse.ArgumentParser) -> None:
    """Attach ``-o/--output`` to parser *p*.

    Description:
        Adds ``-o/--output`` with choices ``table|json|value|name|wide`` and
        default ``table``.

    Inputs:
        p: parser or sub-parser to extend.

    Outputs:
        None; mutates *p*.

    Example:
        >>> import argparse; p = argparse.ArgumentParser()
        >>> _add_output(p)
        >>> p.parse_args(["-o", "json"]).output
        'json'
    """
    p.add_argument(
        "-o",
        "--output",
        default="table",
        choices=("table", "json", "value", "name", "wide"),
    )


def _add_no_trunc(p: argparse.ArgumentParser) -> None:
    """Attach ``--no-trunc`` to parser *p*.

    Description:
        Global opt-out for table truncation pass-through to :mod:`corvus_cli.output`.

    Inputs:
        p: parser.

    Outputs:
        None.

    Example:
        >>> import argparse; p = argparse.ArgumentParser()
        >>> _add_no_trunc(p)
        >>> p.parse_args(["--no-trunc"]).no_trunc
        True
    """
    p.add_argument(
        "--no-trunc",
        action="store_true",
        help="do not truncate table cells",
    )


def _add_value_flags(p: argparse.ArgumentParser) -> None:
    """Attach secret-value/metadata/access flags to parser *p*.

    Description:
        Adds ``--value`` (suppressed), ``--from-file``, ``--from-env``,
        ``--note``, ``--kind``, ``--expires-days``, ``--clear-expires``,
        ``--meta`` / ``--delete-meta``, and ``--access-mode`` /
        ``--requires-approval``.

    Inputs:
        p: parser for ``apply`` / ``create secret`` style commands.

    Outputs:
        None.

    Example:
        >>> import argparse; p = argparse.ArgumentParser()
        >>> _add_value_flags(p)
        >>> p.parse_args(["--note", "hi"]).note
        'hi'
    """
    p.add_argument("--value", help=argparse.SUPPRESS)
    p.add_argument("--from-file", metavar="PATH", help="file or - for stdin")
    p.add_argument("--from-env", metavar="VAR")
    p.add_argument("--note")
    p.add_argument("--kind", choices=("plain", "database", "certificate", "ssh", "kv"))
    p.add_argument("--expires-days", type=int, dest="expires_days")
    p.add_argument("--clear-expires", action="store_true", dest="clear_expires")
    p.add_argument(
        "--meta",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help="set one custom metadata field (repeatable; requires PAT)",
    )
    p.add_argument(
        "--delete-meta",
        action="append",
        default=None,
        dest="delete_meta",
        metavar="KEY",
        help="remove one custom metadata field (repeatable; requires PAT)",
    )
    p.add_argument(
        "--access-mode",
        choices=("inherit", "restricted"),
        help="per-secret access mode (project admin, PAT)",
    )
    p.add_argument(
        "--requires-approval",
        choices=("on", "off", "inherit"),
        help="override reveal-approval for this secret (project admin, PAT)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build and return the Corvus CLI argument parser.

    Description:
        Registers subcommands ``login``, ``project``, ``get``, ``reveal``,
        ``approve``, ``deny``, ``delete``, ``create``, ``apply``/``set``,
        ``restore``, ``transfer``, ``grant``, ``unbind``, ``export``,
        ``settings``, ``completion``, and ``help`` with their per-command flags.
        Global ``--no-trunc`` is added to each sub-parser so placement is free.

    Inputs:
        None.

    Outputs:
        Configured :class:`argparse.ArgumentParser` whose parsed ``.func`` points
        to the command handler and ``.no_trunc`` indicates truncation opt-out.

    Example:
        >>> p = build_parser()
        >>> args = p.parse_args(["get", "secrets", "--no-trunc"])
        >>> args.no_trunc
        True
        >>> args.func  # doctest: +SKIP
        <function cmd_get …>
    """
    # local imports to avoid circular init order
    from corvus_cli.commands.access import (
        cmd_approve,
        cmd_deny,
        cmd_export,
        cmd_grant,
        cmd_reveal,
        cmd_settings,
        cmd_unbind,
    )
    from corvus_cli.commands.auth import cmd_login, cmd_project
    from corvus_cli.commands.management import cmd_create, cmd_delete, cmd_get, cmd_restore, cmd_transfer
    from corvus_cli.commands.secrets import cmd_apply

    p = argparse.ArgumentParser(
        prog="corvus",
        description="Command-line client for Corvus",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # global truncation flag (also per-subparser for `corvus get secrets --no-trunc` order)
    _add_no_trunc(p)

    sub = p.add_subparsers(dest="cmd")

    pl = sub.add_parser(
        "login",
        aliases=["configure"],
        help="save credentials (--project optional for pat_…/sso_… tokens)",
    )
    pl.add_argument("--url")
    pl.add_argument("--token")
    pl.add_argument("--project", help="project UUID (machine) or name/UUID (pat_…/sso_…)")
    _add_no_trunc(pl)
    pl.set_defaults(func=cmd_login)

    pp = sub.add_parser("project")
    pp.add_argument("name", nargs="?")
    _add_no_trunc(pp)
    pp.set_defaults(func=cmd_project)

    pg = sub.add_parser("get")
    pg.add_argument("resource")
    pg.add_argument("name", nargs="?")
    pg.add_argument("-l", "--selector", dest="selector")
    pg.add_argument("--team")
    pg.add_argument("--source", choices=("org", "secret", "access", "project"))
    pg.add_argument("--status", choices=("pending", "approved", "denied", "all"))
    pg.add_argument("--actor")
    pg.add_argument("--action")
    pg.add_argument("--since")
    pg.add_argument("--until")
    pg.add_argument("--admin", action="store_true")
    pg.add_argument("--project", help="target project (UUID, or unique name for a pat_…/sso_…)")
    _add_no_trunc(pg)
    _add_output(pg)
    pg.set_defaults(func=cmd_get)

    prv = sub.add_parser("reveal")
    prv.add_argument("resource", nargs="?", default="secret")
    prv.add_argument("name", nargs="?")
    prv.add_argument("--reason", default="")
    prv.add_argument("--project", help="target project (UUID, or unique name for a pat_…/sso_…)")
    _add_no_trunc(prv)
    _add_output(prv)
    prv.set_defaults(func=cmd_reveal)

    pap = sub.add_parser("approve")
    pap.add_argument("request_id", nargs="?")
    pap.add_argument("name", nargs="?")
    pap.add_argument("--minutes", type=int, default=15, help="grant duration: 15, 60, 240, or 1440")
    pap.add_argument("--project", help="target project (UUID, or unique name for a pat_…/sso_…)")
    _add_no_trunc(pap)
    _add_output(pap)
    pap.set_defaults(func=cmd_approve)

    pdy = sub.add_parser("deny")
    pdy.add_argument("request_id", nargs="?")
    pdy.add_argument("name", nargs="?")
    pdy.add_argument("--project", help="target project (UUID, or unique name for a pat_…/sso_…)")
    _add_no_trunc(pdy)
    _add_output(pdy)
    pdy.set_defaults(func=cmd_deny)

    pd = sub.add_parser("delete")
    pd.add_argument("resource")
    pd.add_argument("name", nargs="?")
    pd.add_argument("--team")
    pd.add_argument("--group")
    pd.add_argument("--all", action="store_true")
    pd.add_argument("--project", help="target project (UUID, or unique name for a pat_…/sso_…)")
    pd.add_argument("--yes", action="store_true", help="confirm destructive delete (team/project/trash purge)")
    _add_no_trunc(pd)
    _add_output(pd)
    pd.set_defaults(func=cmd_delete)

    pc = sub.add_parser("create")
    pc.add_argument("resource")
    pc.add_argument("name", nargs="?")
    pc.add_argument("--team")
    pc.add_argument("--role", help="member: team-…/project-…; token: service-…")
    pc.add_argument("--email")
    pc.add_argument("--group")
    pc.add_argument("--source", choices=("manual", "ldap", "oidc"))
    pc.add_argument("--external-key", dest="external_key")
    pc.add_argument(
        "--scope",
        help="token key allow-list: comma/space-separated exact keys and/or globs (default: all keys)",
    )
    pc.add_argument("--project", help="target project (UUID, or unique name for a pat_…/sso_…)")
    _add_value_flags(pc)
    _add_no_trunc(pc)
    _add_output(pc)
    pc.set_defaults(func=cmd_create)

    for alias in ("apply", "set"):
        pa = sub.add_parser(alias)
        pa.add_argument("resource")
        pa.add_argument("name", nargs="?")
        pa.add_argument("--project", help="target project (UUID, or unique name for a pat_…/sso_…)")
        _add_value_flags(pa)
        _add_no_trunc(pa)
        _add_output(pa)
        pa.set_defaults(func=cmd_apply)

    pr = sub.add_parser("restore")
    pr.add_argument("resource")
    pr.add_argument("name", nargs="?")
    pr.add_argument("--all", action="store_true")
    pr.add_argument("--project", help="target project (UUID, or unique name for a pat_…/sso_…)")
    _add_no_trunc(pr)
    _add_output(pr)
    pr.set_defaults(func=cmd_restore)

    pt = sub.add_parser("transfer")
    pt.add_argument("resource")
    pt.add_argument("name", nargs="?")
    pt.add_argument("--email", required=True)
    _add_no_trunc(pt)
    _add_output(pt)
    pt.set_defaults(func=cmd_transfer)

    pgr = sub.add_parser("grant")
    pgr.add_argument("resource", nargs="?", default="secret")
    pgr.add_argument("name", nargs="?")
    pgr.add_argument("--to")
    pgr.add_argument("--group")
    pgr.add_argument("--sa")
    pgr.add_argument("--role", help="secret-read | secret-write | secret-reveal")
    pgr.add_argument("--project", help="target project (UUID, or unique name for a pat_…/sso_…)")
    _add_no_trunc(pgr)
    _add_output(pgr)
    pgr.set_defaults(func=cmd_grant)

    pub = sub.add_parser("unbind")
    pub.add_argument("resource", nargs="?", default="secret")
    pub.add_argument("name", nargs="?")
    pub.add_argument("binding", nargs="?")
    pub.add_argument("--project", help="target project (UUID, or unique name for a pat_…/sso_…)")
    _add_no_trunc(pub)
    _add_output(pub)
    pub.set_defaults(func=cmd_unbind)

    pe = sub.add_parser("export")
    pe.add_argument("name", nargs="?", help="project name/UUID; defaults to current project")
    pe.add_argument("-o", "--output", default="env", choices=("env", "value", "json", "csv", "table"))
    pe.add_argument("--yes", action="store_true", help="confirm bulk plaintext export")
    pe.add_argument("--project", help="alias for positional project")
    _add_no_trunc(pe)
    pe.set_defaults(func=cmd_export)

    pset = sub.add_parser("settings")
    pset.add_argument(
        "--require-reveal-approval",
        dest="require_reveal_approval",
        choices=("on", "off", "true", "false", "yes", "1"),
        default=None,
    )
    pset.add_argument("--default-access-mode", dest="default_access_mode", choices=("inherit", "restricted"))
    pset.add_argument("--description")
    pset.add_argument("--project", help="target project (UUID, or unique name for a pat_…/sso_…)")
    _add_no_trunc(pset)
    _add_output(pset)
    pset.set_defaults(func=cmd_settings)

    pcomp = sub.add_parser("completion", help="print shell completion script (bash|zsh|fish)")
    pcomp.add_argument("shell", nargs="?", choices=("bash", "zsh", "fish"), default="bash")
    _add_no_trunc(pcomp)
    pcomp.set_defaults(func=_cmd_completion)

    ph = sub.add_parser("help")
    _add_no_trunc(ph)
    ph.set_defaults(func=lambda _a: print(_usage(), end=""))

    return p


def _usage() -> str:
    """Return the canonical usage string.

    Description:
        Isolated for reuse by the ``help`` subcommand handler without
        importing the package at module import time.

    Inputs:
        None.

    Outputs:
        Usage text as ``str``.

    Example:
        >>> isinstance(_usage(), str)
        True
    """
    from corvus_cli.constants import USAGE

    return USAGE


def _cmd_completion(args) -> None:
    """Print a shell completion script for ``corvus completion <shell>``.

    Description:
        Emits the appropriate snippet from :data:`_COMPLETION_SH` and exits.

    Inputs:
        args: namespace with ``.shell`` in ``bash``/``zsh``/``fish``.

    Outputs:
        Completion script to ``stdout``.

    Example:
        >>> import argparse; _cmd_completion(argparse.Namespace(shell="bash", no_trunc=False))  # doctest: +SKIP
        # corvus bash completion …
    """
    shell = getattr(args, "shell", "bash") or "bash"
    sys.stdout.write(_COMPLETION_SH.get(shell, _COMPLETION_SH["bash"]))
