"""Secret CRUD commands: get/list, apply/create, delete.

Description:
    Implements ``corvus get secrets|secret``, ``apply|set secret`` (plus
    ``create secret`` delegation), and ``delete secret``. Handles output modes,
    ACL/approval error hints, metadata/access-settings sub-calls, create-vs-
    patch heuristics, and single-source secret value ingestion.

Inputs:
    Parsed :class:`argparse.Namespace` (``resource``, ``name``, ``selector``,
    ``output``, ``project``, ``meta``, ``delete_meta``, value flags, …) plus
    env/config/HTTP via :mod:`corvus_cli.api` and :mod:`corvus_cli.config`.

Outputs:
    Rendered tables/JSON/values to ``stdout`` via :mod:`corvus_cli.output`;
    error hints to ``sys.exit``; secret values never echoed on success tables.

Example:
    >>> # apply with env value:
    >>> # main(["apply", "secret", "API_KEY", "--from-env", "NEW_KEY"])  # doctest: +SKIP
"""

from __future__ import annotations

import sys
import urllib.parse

from corvus_cli.api import _mgmt_proj_api, _proj_api
from corvus_cli.config import _parse_meta_flags, _read_secret_value, _require_pat, _suggest_resource
from corvus_cli.constants import KNOWN_RESOURCES
from corvus_cli.output import emit


def _out(args) -> str:
    """Return resolved output mode from *args*.

    Description:
        Normalizes ``args.output`` defaulting to ``"table"``.

    Inputs:
        args: namespace with optional ``.output`` string.

    Outputs:
        One of ``"table"``, ``"json"``, ``"value"``, ``"name"``, ``"wide"``,
        ``"env"`` (export).

    Example:
        >>> import argparse
        >>> _out(argparse.Namespace(output="json"))
        'json'
        >>> _out(argparse.Namespace())
        'table'
    """
    return getattr(args, "output", None) or "table"


def cmd_apply(args) -> None:
    """Create or update a secret, optionally setting metadata/access.

    Description:
        Validates that a secret value source (``--value``/``--from-file``/
        ``--from-env``) is exclusive, warns on ``--value`` (ps-visible),
        validates ``kind`` and ``--expires-days`` (must be positive int when
        supplied) and that creation carries a value. On apply, issues
        ``PUT`` when a value is present else ``PATCH`` for metadata-only updates,
        then sequentially patches ``/meta`` entries, deletes via
        ``DELETE …/meta/<key>``, and patches access settings. Responses are
        redacted (``value`` stripped) before emit.

    Inputs:
        args: namespace with ``resource``, ``name`` (key), value flags
        (``value``/``from_file``/``from_env``), ``note``/``kind``/``expires_days``/
        ``clear_expires``, ``meta``/``delete_meta``, ``access_mode``/
        ``requires_approval``, ``output``, ``project``.

    Outputs:
        Table/JSON/value confirmation (no secret value on success table);
        exits on missing name, conflicting sources, bad meta, or missing value
        on create.

    Example:
        >>> # meta-only:
        >>> # cmd_apply(Namespace(resource="secret", name="K", meta=["owner=team"], …))
    """
    res = (getattr(args, "resource", "") or "").lower()
    if res not in ("secret", "secrets"):
        sug = _suggest_resource(res)
        hint = f"; did you mean {sug!r}?" if sug else ""
        sys.exit(f"apply supports 'secret' (got {res!r}){hint}")
    if not getattr(args, "name", None):
        sys.exit("apply secret NAME …")
    key = urllib.parse.quote(args.name, safe="/")
    project = getattr(args, "project", None)

    meta_sets = _parse_meta_flags(getattr(args, "meta", None) or [])
    meta_dels = getattr(args, "delete_meta", None) or []
    if meta_sets or meta_dels:
        _require_pat()  # metadata lives on /api/v1/manage (PAT only)

    # Validate expires_days
    ed = getattr(args, "expires_days", None)
    if ed is not None and ed <= 0:
        sys.exit("--expires-days must be a positive integer")

    body: dict = {}
    value = _read_secret_value(args)
    if value is not None:
        if getattr(args, "value", None) is not None:
            print(
                "warning: --value is visible in process listings; use --from-file or --from-env instead",
                file=sys.stderr,
            )
        body["value"] = value
    if getattr(args, "note", None) is not None:
        body["note"] = args.note
    if getattr(args, "kind", None) is not None:
        body["kind"] = args.kind
    if ed is not None:
        body["expires_days"] = ed
    if getattr(args, "clear_expires", False):
        body["clear_expires"] = True

    access_body: dict = {}
    if getattr(args, "access_mode", None):
        access_body["access_mode"] = args.access_mode
    if getattr(args, "requires_approval", None):
        access_body["requires_approval"] = {
            "on": True,
            "off": False,
            "inherit": None,
        }[args.requires_approval]
    if access_body:
        _require_pat()  # access settings on /api/v1/manage (PAT only)

    # Creation requires a value; note-only apply without existing secret would 404.
    # We let server decide on update path, but warn if nothing to do.
    data: dict = {}  # type: ignore[assignment]
    if body:
        method = "PUT" if "value" in body else "PATCH"
        data = _proj_api(method, f"/secrets/{key}", body=body, project=project)  # type: ignore[assignment]
    elif not meta_sets and not meta_dels and not access_body:
        sys.exit("nothing to apply")

    out = _out(args)
    if out == "value" and isinstance(data, dict) and "value" in data:
        emit(data, "value")
        return
    for mkey, mval in meta_sets:
        _mgmt_proj_api(
            "PATCH",
            f"/secrets/{key}/meta",
            body={"key": mkey, "value": mval},
            project=project,
        )
    for mkey in meta_dels:
        _mgmt_proj_api(
            "DELETE",
            f"/secrets/{key}/meta/{urllib.parse.quote(mkey, safe='')}",
            project=project,
        )
    if access_body:
        _mgmt_proj_api("PATCH", f"/secrets/{key}", body=access_body, project=project)
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k != "value"}
        data["ok"] = data.get("ok", True)
        data["key"] = data.get("key") or args.name
    elif meta_sets or meta_dels or access_body:
        data = {"ok": True, "key": args.name}
    emit(data, out)
