"""SSH host integration (flow 4: `Include` fragment + key dir).

Description:
    Implements ``corvus ssh sync``, ``corvus ssh config`` and
    ``corvus ssh _ensure`` (lazy ``Match exec`` helper). Secrets that are
    SSH private keys are stored in Corvus. Out of the box it discovers
    ``kind=ssh`` secrets, including the default ``corvus-agent`` layout
    ``hosts/<hostname>/users/<account>`` (key_prefix ``hosts/``). This
    module materializes them to a local key directory (0700, files 0600)
    and generates an OpenSSH ``Include`` fragment so native ``ssh <host>``
    works without a wrapper.

Inputs:
    Credentials via :mod:`corvus_cli.api` (``_proj_api``), filesystem
    (``~/.config/corvus/keys``, ``~/.ssh/config`` and ``~/.ssh/config.d/corvus``),
    optional host map ``~/.config/corvus/ssh_hosts`` and argparse namespace.

Outputs:
    Keys and fragment on disk, exit messages, or ``sys.exit`` on 403/approval.

Example:
    >>> # corvus ssh sync --dry-run  -> lists hosts without fetching values
    >>> # corvus ssh config install   -> adds Include to ~/.ssh/config
    >>> # corvus ssh _ensure web01
    >>> # with agent default: hosts/web01/users/deploy -> ssh web01 (User deploy)
"""

from __future__ import annotations

import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

from corvus_cli.api import _proj_api
from corvus_cli.output import emit, print_table

# ponytail: matches corvus-agent default key_prefix="hosts/"
DEFAULT_PREFIX = "hosts/"
DEFAULT_FRAG = Path.home() / ".ssh" / "config.d" / "corvus"
# ponytail: TTL 3600s file cache; lower if keys rotate fast
DEFAULT_TTL = 3600
INCLUDE_LINE = "Include ~/.ssh/config.d/corvus"


def _config_dir() -> Path:
    from corvus_cli.config import CONFIG_DIR

    return CONFIG_DIR


def _ssh_hosts_map() -> Path:
    return _config_dir() / "ssh_hosts"


def _default_key_dir() -> Path:
    return _config_dir() / "keys"


def _resolve_key_dir(custom: str | None) -> Path:
    if custom:
        return Path(custom).expanduser()
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.environ.get("SS_SSH_USE_RUNTIME") == "1":
        return Path(runtime) / "corvus"
    return _default_key_dir()


def _resolve_frag(custom: str | None) -> Path:
    if custom:
        return Path(custom).expanduser()
    return DEFAULT_FRAG


def _sanitize_host(host: str) -> str:
    host = host.strip()
    if not host or "/" in host or "\\" in host or ".." in host:
        sys.exit(f"invalid host {host!r}")
    if len(host) > 255:
        sys.exit(f"invalid host {host!r}: too long")
    if "@" in host:
        host = host.split("@", 1)[-1]
        if "/" in host or ".." in host:
            sys.exit(f"invalid host {host!r}")
    # ponytail: host alias must not contain whitespace
    if re.search(r"\s", host):
        sys.exit(f"invalid host {host!r}")
    return host


def _parse_agent_key(key: str) -> tuple[str, str] | None:
    """Return (hostname, account) if *key* matches ``<prefix><host>/users/<acct>``.

    Matches the default ``corvus-agent`` layout ``hosts/<host>/users/<acct>``
    and any custom ``key_prefix`` that still contains ``/users/``. Host and
    account are single path segments without slashes.
    """
    if "/users/" not in key:
        return None
    # split on the last /users/ to handle prefix containing same string unlikely
    left, _, acct = key.rpartition("/users/")
    if not left or not acct or "/" in acct or "\\" in acct or ".." in acct:
        return None
    # host is last segment of left part
    host = left.rsplit("/", 1)[-1] if "/" in left else left
    if not host or "/" in host or "\\" in host or ".." in host:
        return None
    # must look like <prefix>host/users/acct, not bare users/acct
    if "/" not in left and left == host:
        # key like "web01/users/deploy" without prefix: still valid agent-like
        pass
    return host, acct


def _load_host_map() -> dict[str, str]:
    p = _ssh_hosts_map()
    if not p.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
        else:
            parts = line.split()
            if len(parts) < 2:
                continue
            k, v = parts[0], parts[1]
        out[k.strip()] = v.strip()
    return out


def _host_to_key(host: str, prefix: str, host_map: dict[str, str] | None = None) -> str:
    host_map = host_map or _load_host_map()
    if host in host_map:
        return host_map[host]
    return f"{prefix}{host}" if prefix else host


def _derive_entries(candidates: list[dict], prefix: str, host_map: dict[str, str]) -> list[dict]:
    """Derive sync entries for *candidates* handling agent layout out of the box.

    Returns list of dicts {alias, aliases, file, key, user, hostname, kind}.
    For agent key hosts/<host>/users/<acct> -> alias host (first per host) or host-acct,
    file host-acct, user acct, hostname host.
    For manual keys -> alias derived from suffix/basename, file alias, no user.
    Host map overrides via rev_map.
    """
    rev_map = {v: k for k, v in host_map.items()}
    # First pass: build raw entries
    raw: list[dict] = []
    for it in candidates:
        key = it.get("key") or ""
        kind = it.get("kind") or ""
        if key in rev_map:
            alias = rev_map[key]
            try:
                alias = _sanitize_host(alias)
            except SystemExit:
                continue
            # preserve agent User/HostName if key matches agent layout
            agent = _parse_agent_key(key)
            if agent:
                hostname, account = agent
                try:
                    _sanitize_host(hostname)
                    _sanitize_host(account)
                except SystemExit:
                    agent = None
                if agent:
                    raw.append({"alias": alias, "aliases": [alias], "file": alias, "key": key, "user": account, "hostname": hostname, "kind": kind})
                    continue
            raw.append({"alias": alias, "aliases": [alias], "file": alias, "key": key, "user": None, "hostname": None, "kind": kind})
            continue
        agent = _parse_agent_key(key)
        if agent:
            hostname, account = agent
            try:
                _sanitize_host(hostname)
                _sanitize_host(account)
            except SystemExit:
                continue
            file_name = f"{hostname}-{account}"
            raw.append({"alias": hostname, "aliases": [hostname, file_name], "file": file_name, "key": key, "user": account, "hostname": hostname, "kind": kind, "_agent": True})
            continue
        # non-agent
        if prefix and key.startswith(prefix):
            alias = key[len(prefix):]
        else:
            alias = key.rsplit("/", 1)[-1]
        alias = alias.strip()
        if not alias:
            continue
        alias_norm = alias.replace("/", "-").replace("\\", "-")
        # also handle agent-like but without prefix detection fallback already covered
        try:
            alias_norm = _sanitize_host(alias_norm)
        except SystemExit:
            continue
        raw.append({"alias": alias_norm, "aliases": [alias_norm], "file": alias_norm, "key": key, "user": None, "hostname": None, "kind": kind})

    # Resolve collisions for agent host aliases: first per hostname keeps bare hostname
    seen_hostnames: dict[str, int] = {}
    out: list[dict] = []
    for e in raw:
        # mapped entries (no _agent) keep verbatim but reserve bare hostname
        if e.get("hostname") and "_agent" not in e:
            if e["alias"] not in [x["alias"] for x in out] and e["file"] not in [x["file"] for x in out]:
                out.append(e)
                hn = e["hostname"]
                if hn == e["alias"]:
                    seen_hostnames[hn] = seen_hostnames.get(hn, 0) + 1
                continue
            out.append(e)
            continue
        if e.get("_agent"):
            hn = e["hostname"]
            n = seen_hostnames.get(hn, 0)
            seen_hostnames[hn] = n + 1
            if n == 0:
                e["alias"] = hn
                e["aliases"] = [hn, e["file"]]
            else:
                e["alias"] = e["file"]
                e["aliases"] = [e["file"]]
            e.pop("_agent", None)
            out.append(e)
        else:
            alias = e["alias"]
            if alias not in [x["alias"] for x in out]:
                out.append(e)
            else:
                base = alias
                i = 2
                while f"{base}-{i}" in [x["alias"] for x in out]:
                    i += 1
                e["alias"] = f"{base}-{i}"
                e["aliases"] = [e["alias"]]
                e["file"] = e["alias"]
                out.append(e)
    return out


def _ensure_dir(p: Path, mode: int = 0o700) -> None:
    p.mkdir(parents=True, exist_ok=True)
    try:
        p.chmod(mode)
    except OSError:
        pass


def _write_key_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    import tempfile

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(value)
            if not value.endswith("\n"):
                f.write("\n")
        Path(tmp).chmod(0o600)
        Path(tmp).replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        try:
            Path(tmp).unlink()
        except FileNotFoundError:
            pass


def _list_candidates(project: str | None, prefix: str) -> list[dict]:
    data = _proj_api("GET", "/secrets", query={"meta": "1"}, project=project)
    items = (data or {}).get("items") or []  # type: ignore[union-attr]
    if not prefix:
        return [it for it in items if (it.get("kind") or "") == "ssh"]
    # prefix set: include prefix matches + any stray ssh kind (fallback)
    out = [it for it in items if (it.get("key") or "").startswith(prefix)]
    if not out:
        # fallback to ssh kind if no prefix hits
        ssh_only = [it for it in items if (it.get("kind") or "") == "ssh"]
        if ssh_only:
            return ssh_only
    else:
        # also include ssh kind outside prefix (covers agent + manual)
        for it in items:
            if (it.get("kind") or "") == "ssh" and it not in out:
                out.append(it)
    return out


def _fetch_value(key: str, project: str | None) -> str:
    qkey = urllib.parse.quote(key, safe="/")
    try:
        data = _proj_api("GET", f"/secrets/{qkey}", project=project)
    except SystemExit as e:
        msg = str(e)
        low = msg.lower()
        if "approval_required" in low or ("403" in msg and "approval" in low):
            sys.exit(f"{msg}\nhint: request approval with:\n  corvus reveal secret {key} --reason \"...\"")
        if "reveal access" in low:
            sys.exit(f"{msg}\nhint: this token cannot reveal secrets (service-read). Use a service-reveal token or a pat_…/sso_… token.")
        if "forbidden" in low or "403" in msg:
            sys.exit(f"{msg}\nhint: you lack reveal permission on this secret (ACL / project role). Ask a project admin.")
        raise
    if not isinstance(data, dict) or "value" not in data:
        sys.exit(f"secret {key!r} has no value")
    return str(data["value"])


def _write_fragment(entries: list[dict], key_dir: Path, frag: Path, prefix: str) -> None:
    lines: list[str] = ["# corvus ssh - generated, do not edit", f"# prefix={prefix!r} key_dir={key_dir}", ""]
    if not entries:
        lines.append("# (no ssh hosts found)")
    for e in sorted(entries, key=lambda x: x["alias"]):
        aliases = e.get("aliases") or [e["alias"]]
        host_line = " ".join(aliases)
        lines.append(f"Host {host_line}")
        # For agent entries, HostName disambiguates file-variant aliases
        hostname = e.get("hostname")
        if hostname:
            # if any alias != hostname, HostName is needed for those aliases
            if any(a != hostname for a in aliases):
                lines.append(f"  HostName {hostname}")
            # also if single alias equals hostname, HostName is redundant but harmless; skip to keep minimal
        user = e.get("user")
        if user:
            lines.append(f"  User {user}")
        lines.append(f"  IdentityFile {key_dir / e['file']}")
        lines.append("  IdentitiesOnly yes")
        lines.append("")
    content = "\n".join(lines)
    frag.parent.mkdir(parents=True, exist_ok=True)
    try:
        frag.parent.chmod(0o700)
    except OSError:
        pass
    import tempfile

    fd, tmp = tempfile.mkstemp(dir=str(frag.parent), prefix=".corvus.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        Path(tmp).chmod(0o600)
        Path(tmp).replace(frag)
    finally:
        try:
            Path(tmp).unlink()
        except FileNotFoundError:
            pass


def _find_entry_for_host(host: str, entries: list[dict]) -> dict | None:
    for e in entries:
        if host in (e.get("aliases") or []) or host == e.get("file") or host == e.get("alias"):
            return e
    return None


def _cmd_sync(args) -> None:
    project = getattr(args, "project", None)
    prefix = getattr(args, "prefix", None)
    if prefix is None:
        prefix = DEFAULT_PREFIX
    key_dir = _resolve_key_dir(getattr(args, "key_dir", None))
    frag = _resolve_frag(getattr(args, "config_fragment", None))
    dry_run = bool(getattr(args, "dry_run", False))
    do_clean = bool(getattr(args, "clean", False))
    do_fragment = not bool(getattr(args, "no_fragment", False))
    out_mode = getattr(args, "output", None) or "table"

    candidates = _list_candidates(project, prefix)
    host_map = _load_host_map()
    entries = _derive_entries(candidates, prefix, host_map)

    if out_mode == "json" and dry_run:
        emit({"prefix": prefix, "key_dir": str(key_dir), "fragment": str(frag), "hosts": [e["alias"] for e in entries], "keys": {e["alias"]: e["key"] for e in entries}, "entries": entries}, "json")
        return

    if dry_run:
        if not entries:
            print("(no ssh hosts found)")
            return
        rows = []
        for e in sorted(entries, key=lambda x: x["alias"]):
            rows.append([e["alias"], e["key"], str(key_dir / e["file"]), e.get("user") or "", e.get("hostname") or ""])
        # hide empty USER/HOSTNAME columns if none
        has_user = any(r[3] for r in rows)
        has_host = any(r[4] for r in rows)
        headers = ["HOST", "SECRET_KEY", "KEY_PATH"]
        if has_user:
            headers.append("USER")
        else:
            rows = [r[:3] for r in rows]
        if has_host and has_user:
            headers.append("HOSTNAME")
            # rows already have 5 cols
        elif has_host and not has_user:
            headers.append("HOSTNAME")
            rows = [r[:3] + [r[4]] for r in rows]
        print_table(headers, rows)
        return

    _ensure_dir(key_dir, 0o700)
    ok_entries: list[dict] = []
    failed: list[tuple[str, str]] = []
    for e in sorted(entries, key=lambda x: x["alias"]):
        key = e["key"]
        try:
            val = _fetch_value(key, project)
            _write_key_atomic(key_dir / e["file"], val)
            ok_entries.append(e)
        except SystemExit as exc:
            failed.append((e["alias"], str(exc).splitlines()[0][:120]))

    if do_fragment:
        _write_fragment(ok_entries, key_dir, frag, prefix)

    if do_clean and key_dir.is_dir():
        expected = {e["file"] for e in ok_entries}
        for p in key_dir.iterdir():
            if p.is_file() and p.name not in expected and not p.name.startswith("."):
                try:
                    p.unlink()
                except OSError:
                    pass

    if out_mode == "json":
        emit({"ok": [e["alias"] for e in ok_entries], "failed": failed, "key_dir": str(key_dir), "fragment": str(frag)}, "json")
        return
    if ok_entries:
        rows = [[e["alias"], e["key"], str(key_dir / e["file"])] for e in ok_entries]
        print_table(["HOST", "SECRET_KEY", "KEY_PATH"], rows)
    if failed:
        print("\nfailed:", file=sys.stderr)
        for h, msg in failed:
            print(f"  {h}: {msg}", file=sys.stderr)
    print(f"synced {len(ok_entries)} key(s) to {key_dir}", file=sys.stderr)
    if do_fragment:
        print(f"fragment {frag}", file=sys.stderr)
    if failed:
        sys.exit(1)


def _cmd_config(args) -> None:
    frag = _resolve_frag(getattr(args, "config_fragment", None))
    ssh_config = Path(getattr(args, "ssh_config", None) or Path.home() / ".ssh" / "config").expanduser()
    action = getattr(args, "config_action", None) or getattr(args, "action", None) or "install"
    if action not in ("install", "uninstall"):
        sys.exit("ssh config: use 'install' or 'uninstall'")

    if action == "install":
        _ensure_dir(frag.parent, 0o700)
        if not frag.exists():
            frag.write_text("# corvus ssh - generated, do not edit\n")
            try:
                frag.chmod(0o600)
            except OSError:
                pass
        _ensure_dir(ssh_config.parent, 0o700)
        if not ssh_config.exists():
            ssh_config.write_text(f"{INCLUDE_LINE}\n")
            try:
                ssh_config.chmod(0o600)
            except OSError:
                pass
            print(f"created {ssh_config} with {INCLUDE_LINE}", file=sys.stderr)
        else:
            text = ssh_config.read_text()
            if INCLUDE_LINE not in text and str(frag) not in text:
                new = f"{INCLUDE_LINE}\n" + text
                ssh_config.write_text(new)
                print(f"added {INCLUDE_LINE!r} to {ssh_config}", file=sys.stderr)
            else:
                print(f"{ssh_config} already includes {INCLUDE_LINE}", file=sys.stderr)
        print(f"fragment: {frag}", file=sys.stderr)
        print(f"key_dir: {_resolve_key_dir(getattr(args, 'key_dir', None))}", file=sys.stderr)
        print("next: corvus ssh sync", file=sys.stderr)
        return
    else:
        if ssh_config.is_file():
            text = ssh_config.read_text()
            if INCLUDE_LINE in text:
                text = text.replace(INCLUDE_LINE + "\n", "").replace(INCLUDE_LINE, "")
                ssh_config.write_text(text)
                print(f"removed {INCLUDE_LINE!r} from {ssh_config}", file=sys.stderr)
            else:
                print(f"{ssh_config} does not contain {INCLUDE_LINE}", file=sys.stderr)
        else:
            print(f"{ssh_config} not found", file=sys.stderr)
        if frag.is_file():
            print(f"kept fragment {frag} (remove manually if desired)", file=sys.stderr)


def _cmd_ensure(args) -> None:
    host = getattr(args, "host", None) or getattr(args, "name", None)
    if not host:
        sys.exit("usage: corvus ssh _ensure HOST")
    host = _sanitize_host(host)
    project = getattr(args, "project", None)
    prefix = getattr(args, "prefix", None)
    if prefix is None:
        prefix = DEFAULT_PREFIX
    key_dir = _resolve_key_dir(getattr(args, "key_dir", None))
    ttl = getattr(args, "ttl", None)
    if ttl is None:
        try:
            ttl = int(os.environ.get("SS_SSH_TTL", str(DEFAULT_TTL)))
        except Exception:
            ttl = DEFAULT_TTL
    else:
        ttl = int(ttl)
    force = bool(getattr(args, "force", False))

    # Resolve file/key for host (host_map + agent alias lookup)
    host_map = _load_host_map()
    key: str | None = None
    file_name: str = host
    if host in host_map:
        key = host_map[host]
        file_name = host
    else:
        # try to find via candidate list (covers agent alias like web01 or web01-deploy)
        try:
            candidates = _list_candidates(project, prefix)
            entries = _derive_entries(candidates, prefix, host_map)
            ent = _find_entry_for_host(host, entries)
            if ent:
                key = ent["key"]
                file_name = ent["file"]
        except Exception:
            pass
        if key is None:
            key = _host_to_key(host, prefix, host_map)
            file_name = host
            # if key is agent-like but host was bare hostname, file should be host-<account>?
            # For ambiguous bare hostname with multiple accounts, the derived entry already handled above.
            # Fallback stays as host.

    dest = key_dir / file_name
    if dest.is_file() and not force and ttl > 0:
        try:
            age = time.time() - dest.stat().st_mtime
            if age < ttl:
                sys.exit(0)
        except OSError:
            pass

    assert key is not None
    val = _fetch_value(key, project)
    _write_key_atomic(dest, val)
    sys.exit(0)


def cmd_ssh(args) -> None:
    """Dispatch ``corvus ssh`` subcommands.

    Description:
        Routes to ``sync``, ``config`` or ``_ensure``. With no subcommand
        prints a short hint.

    Inputs:
        args: namespace with ``ssh_cmd`` and delegated flags.

    Outputs:
        Delegates to sub-handlers; exits on unknown subcommand.
    """
    sub = getattr(args, "ssh_cmd", None)
    if sub == "sync":
        _cmd_sync(args)
    elif sub == "config":
        _cmd_config(args)
    elif sub == "_ensure":
        _cmd_ensure(args)
    else:
        sys.exit("usage: corvus ssh sync|config|_ensure ... (try --help)")
