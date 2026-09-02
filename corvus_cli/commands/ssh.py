"""SSH host integration (flow 4: `Include` fragment + key dir).

Description:
    Implements ``corvus ssh sync``, ``corvus ssh config`` and
    ``corvus ssh _ensure`` (lazy ``Match exec`` helper). Secrets that are
    SSH private keys are stored in Corvus (convention ``ssh/<host>`` or
    ``kind=ssh``). This module materializes them to a local key directory
    (0700, files 0600) and generates an OpenSSH ``Include`` fragment so
    native ``ssh <host>`` works without a wrapper.

Inputs:
    Credentials via :mod:`corvus_cli.api` (``_proj_api``), filesystem
    (``~/.config/corvus/keys``, ``~/.ssh/config`` and ``~/.ssh/config.d/corvus``),
    optional host map ``~/.config/corvus/ssh_hosts`` and argparse namespace.

Outputs:
    Keys and fragment on disk, exit messages, or ``sys.exit`` on 403/approval.

Example:
    >>> # corvus ssh sync --dry-run  -> lists hosts without fetching values
    >>> # corvus ssh config --install
    >>> # corvus ssh _ensure web01
"""

from __future__ import annotations

import os
import sys
import time
import urllib.parse
from pathlib import Path

from corvus_cli.api import _proj_api
from corvus_cli.output import emit, print_table

DEFAULT_PREFIX = "ssh/"
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
    return host


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
        # also include ssh kind outside prefix
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


def _write_fragment(hosts: list[str], key_dir: Path, frag: Path, prefix: str) -> None:
    lines: list[str] = ["# corvus ssh - generated, do not edit", f"# prefix={prefix!r} key_dir={key_dir}", ""]
    if not hosts:
        lines.append("# (no ssh hosts found)")
    for host in sorted(hosts):
        key_path = key_dir / host
        lines.append(f"Host {host}")
        lines.append(f"  IdentityFile {key_path}")
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
    hosts: list[str] = []
    key_for_host: dict[str, str] = {}
    host_map = _load_host_map()
    rev_map = {v: k for k, v in host_map.items()}
    for it in candidates:
        key = it.get("key") or ""
        if key in rev_map:
            host = rev_map[key]
        elif prefix and key.startswith(prefix):
            host = key[len(prefix) :]
        else:
            host = key.rsplit("/", 1)[-1]
        host = _sanitize_host(host)
        if not host:
            continue
        hosts.append(host)
        key_for_host[host] = key

    if out_mode == "json" and dry_run:
        emit({"prefix": prefix, "key_dir": str(key_dir), "fragment": str(frag), "hosts": hosts, "keys": key_for_host}, "json")
        return

    if dry_run:
        if not hosts:
            print("(no ssh hosts found)")
            return
        print_table(["HOST", "SECRET_KEY", "KEY_PATH"], [[h, key_for_host[h], str(key_dir / h)] for h in sorted(hosts)])
        return

    _ensure_dir(key_dir, 0o700)
    ok: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    for host in sorted(hosts):
        key = key_for_host[host]
        try:
            val = _fetch_value(key, project)
            _write_key_atomic(key_dir / host, val)
            ok.append((host, key))
        except SystemExit as e:
            failed.append((host, str(e).splitlines()[0][:120]))

    if do_fragment:
        _write_fragment([h for h, _ in ok], key_dir, frag, prefix)

    if do_clean and key_dir.is_dir():
        expected = {h for h, _ in ok}
        for p in key_dir.iterdir():
            if p.is_file() and p.name not in expected and not p.name.startswith("."):
                try:
                    p.unlink()
                except OSError:
                    pass

    if out_mode == "json":
        emit({"ok": [h for h, _ in ok], "failed": failed, "key_dir": str(key_dir), "fragment": str(frag)}, "json")
        return
    if ok:
        print_table(["HOST", "SECRET_KEY", "KEY_PATH"], [[h, key_for_host[h], str(key_dir / h)] for h, _ in ok])
    if failed:
        print("\nfailed:", file=sys.stderr)
        for h, msg in failed:
            print(f"  {h}: {msg}", file=sys.stderr)
    print(f"synced {len(ok)} key(s) to {key_dir}", file=sys.stderr)
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

    dest = key_dir / host
    if dest.is_file() and not force and ttl > 0:
        try:
            age = time.time() - dest.stat().st_mtime
            if age < ttl:
                sys.exit(0)
        except OSError:
            pass

    key = _host_to_key(host, prefix)
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
