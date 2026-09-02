"""SSH host integration (flow 4: `Include` fragment + ssh-agent).

Description:
    Implements ``corvus ssh setup``, ``list``, ``status``, ``sync``,
    ``uninstall`` and the internal ``_ensure`` ``Match exec`` helper. Secrets that are
    SSH private keys are stored in Corvus. Out of the box it discovers
    ``kind=ssh`` secrets, including the ``corvus-agent`` layout
    ``hosts/<hostname>/users/<account>/ssh`` (``_ssh_account_key`` with
    ``key_prefix`` ``hosts/``) and legacy ``hosts/<hostname>/users/<account>``.
    The companion ``hosts/<hostname>/users/<account>/password`` (``_account_key``)
    is ignored for SSH. ``_ensure`` loads the private key into ``ssh-agent``
    (``ssh-add -t TTL``) and writes only the ``.pub`` to the key directory so
    native ``ssh <host>`` works with ``IdentitiesOnly yes``.

Inputs:
    Credentials via :mod:`corvus_cli.api` (``_proj_api``), filesystem
    (``~/.config/corvus/keys/*.pub``, ``~/.ssh/config`` and ``~/.ssh/config.d/corvus``),
    ``SSH_AUTH_SOCK``, optional host map ``~/.config/corvus/ssh_hosts`` and argparse namespace.

Outputs:
    Public keys and fragment on disk, identities in ssh-agent, exit messages,
    or ``sys.exit`` on 403/approval / missing agent.

Example:
    >>> # corvus ssh sync --dry-run  -> lists hosts without fetching values
    >>> # corvus ssh config install   -> adds Include to ~/.ssh/config
    >>> # corvus ssh _ensure web01
    >>> # with agent default: hosts/web01/users/deploy/ssh -> ssh web01 (User deploy)
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

from corvus_cli.api import _proj_api
from corvus_cli.output import emit, print_table

# ponytail: matches corvus-agent default key_prefix="hosts/"
DEFAULT_PREFIX = "hosts/"
DEFAULT_FRAG = Path.home() / ".ssh" / "config.d" / "corvus"
# ponytail: TTL 300s for lazy JIT keys (was 3600); short enough to pick up rotations
DEFAULT_TTL = 300
INCLUDE_LINE = "Include ~/.ssh/config.d/corvus"
AGENT_HINT = "eval $(ssh-agent)   # Fedora/RHEL: systemctl --user enable --now ssh-agent.socket"

SSH_HELP = """\
corvus ssh — native ssh <host> with keys from Corvus

  corvus ssh setup              once: wire ~/.ssh/config and discover hosts
  ssh <host>                    first connect loads that key into ssh-agent

  corvus ssh list               hosts you can ssh to (no secrets fetched)
  corvus ssh status             agent, Include, which keys are loaded
  corvus ssh sync               refresh host list / fragment
  corvus ssh sync --eager       prefetch all keys into ssh-agent now
  corvus ssh uninstall [--purge]

ssh-agent must be running:
  """ + AGENT_HINT + "\n"


def _config_dir() -> Path:
    from corvus_cli.config import CONFIG_DIR

    return CONFIG_DIR


def _ssh_hosts_map() -> Path:
    return _config_dir() / "ssh_hosts"


def _default_key_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.environ.get("SS_SSH_USE_RUNTIME") != "0":
        return Path(runtime) / "corvus"
    return _config_dir() / "keys"


def _resolve_key_dir(custom: str | None) -> Path:
    if custom:
        return Path(custom).expanduser()
    return _default_key_dir()


def _resolve_frag(custom: str | None) -> Path:
    if custom:
        return Path(custom).expanduser()
    return DEFAULT_FRAG


def _ssh_config_path(custom: str | None) -> Path:
    return Path(custom or (Path.home() / ".ssh" / "config")).expanduser()


def _include_line_for(frag: Path) -> str:
    try:
        if frag.resolve() == DEFAULT_FRAG.expanduser().resolve():
            return INCLUDE_LINE
    except OSError:
        pass
    if frag == DEFAULT_FRAG:
        return INCLUDE_LINE
    return f"Include {frag}"


def _include_present(ssh_config: Path, frag: Path) -> bool:
    if not ssh_config.is_file():
        return False
    text = ssh_config.read_text()
    return _include_line_for(frag) in text or INCLUDE_LINE in text or str(frag) in text


def _should_wire_include(args) -> bool:
    """Do not touch ~/.ssh/config when tests/users only override the fragment path."""
    if getattr(args, "no_fragment", False):
        return False
    custom_frag = getattr(args, "config_fragment", None)
    custom_ssh = getattr(args, "ssh_config", None)
    if custom_frag and not custom_ssh:
        return False
    return True


def _install_include(ssh_config: Path, frag: Path) -> bool:
    """Idempotently add Include and an empty fragment. Returns True if anything changed."""
    changed = False
    line = _include_line_for(frag)
    _ensure_dir(frag.parent, 0o700)
    if not frag.exists():
        frag.write_text("# corvus ssh - generated, do not edit\n")
        try:
            frag.chmod(0o600)
        except OSError:
            pass
        changed = True
    _ensure_dir(ssh_config.parent, 0o700)
    if not ssh_config.exists():
        ssh_config.write_text(f"{line}\n")
        try:
            ssh_config.chmod(0o600)
        except OSError:
            pass
        print(f"installed {line} in {ssh_config}", file=sys.stderr)
        return True
    text = ssh_config.read_text()
    if line not in text and INCLUDE_LINE not in text and str(frag) not in text:
        ssh_config.write_text(f"{line}\n" + text)
        print(f"installed {line} in {ssh_config}", file=sys.stderr)
        changed = True
    return changed


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
    """Return (hostname, account) if *key* matches agent SSH layout.

    Supports ``hosts/<host>/users/<acct>/authorized_keys`` (``_ssh_account_key``)
    and legacy ``hosts/<host>/users/<acct>`` / ``.../ssh`` (backward compat).
    The companion ``.../password`` (``_account_key``) is not SSH.
    """
    # ponytail: agent splits account into /authorized_keys (ssh) and /password (plain); only /authorized_keys is SSH
    if key.endswith("/password"):
        return None
    if key.endswith("/authorized_keys"):
        base = key[:-16]
    elif key.endswith("/ssh"):
        base = key[:-4]
    else:
        base = key
    if "/users/" not in base:
        return None
    # split on the last /users/ to handle prefix containing same string unlikely
    left, _, acct = base.rpartition("/users/")
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
    For agent key hosts/<host>/users/<acct>/ssh (and legacy bare .../<acct>
    for backward compat) -> alias host (first per host) or host-acct,
    file host-acct, user acct, hostname host. Password .../password skipped.
    For manual keys -> alias derived from suffix/basename, file alias, no user.
    Host map overrides via rev_map.
    """
    rev_map = {v: k for k, v in host_map.items()}
    # First pass: build raw entries
    raw: list[dict] = []
    for it in candidates:
        key = it.get("key") or ""
        kind = it.get("kind") or ""
        # ponytail: new agent splits account into /ssh (ssh) and /password (plain); skip password for ssh sync
        if key.endswith("/password"):
            continue
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

    # ponytail: dedup legacy hosts/<h>/users/<a> vs new hosts/<h>/users/<a>/authorized_keys -> prefer /authorized_keys
    seen_agent_keys: dict[tuple[str, str], dict] = {}
    deduped: list[dict] = []
    for e in raw:
        if e.get("_agent") and e.get("hostname") and e.get("user"):
            k = (e["hostname"], e["user"])  # type: ignore[assignment]
            prev = seen_agent_keys.get(k)
            if prev is None:
                seen_agent_keys[k] = e
                deduped.append(e)
            else:
                cur_is_ak = e["key"].endswith("/authorized_keys")
                prev_is_ak = prev["key"].endswith("/authorized_keys")
                cur_is_ssh = e["key"].endswith("/ssh") or cur_is_ak
                prev_is_ssh = prev["key"].endswith("/ssh") or prev_is_ak
                if cur_is_ak and not prev_is_ak:
                    idx = deduped.index(prev)
                    deduped[idx] = e
                    seen_agent_keys[k] = e
                elif cur_is_ssh and not prev_is_ssh:
                    idx = deduped.index(prev)
                    deduped[idx] = e
                    seen_agent_keys[k] = e
                # else keep prev, drop cur
        else:
            deduped.append(e)
    raw = deduped

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


def _identity_path(key_dir: Path, file_name: str) -> Path:
    return key_dir / f"{file_name}.pub"


def _cfg_path(path: Path) -> str:
    s = str(path)
    if re.search(r"\s", s):
        return f'"{s}"'
    return s


def _ttl_from_args(args) -> int:
    ttl = getattr(args, "ttl", None)
    if ttl is None:
        try:
            return int(os.environ.get("SS_SSH_TTL", str(DEFAULT_TTL)))
        except (TypeError, ValueError):
            return DEFAULT_TTL
    return int(ttl)


def _ssh_add_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("SSH_ASKPASS_REQUIRE", "never")
    return env


def _agent_status() -> tuple[str, int | None]:
    """Return (ok|empty|missing, identity count)."""
    if not os.environ.get("SSH_AUTH_SOCK"):
        return "missing", None
    r = subprocess.run(["ssh-add", "-l"], capture_output=True, text=True)
    if r.returncode == 2:
        return "missing", None
    if r.returncode == 1:
        return "empty", 0
    n = len([ln for ln in r.stdout.splitlines() if ln.strip()])
    return "ok", n


def _require_ssh_agent() -> None:
    st, _ = _agent_status()
    if st == "missing":
        sys.exit(f"ssh-agent not available\nhint: {AGENT_HINT}")


def _warn_if_no_agent() -> str:
    st, n = _agent_status()
    if st == "missing":
        print(
            f"warning: ssh-agent is not running — `ssh <host>` will fail until you start one\n  {AGENT_HINT}",
            file=sys.stderr,
        )
    return st


_FP_RE = re.compile(r"SHA256:[A-Za-z0-9+/]+")


def _pub_fingerprint(pub: Path) -> str | None:
    r = subprocess.run(
        ["ssh-keygen", "-lf", str(pub), "-E", "sha256"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return None
    m = _FP_RE.search(r.stdout)
    return m.group(0) if m else None


def _pub_in_agent(pub: Path) -> bool:
    fp = _pub_fingerprint(pub)
    if not fp:
        return False
    r = subprocess.run(
        ["ssh-add", "-l", "-E", "sha256"],
        capture_output=True,
        text=True,
        env=_ssh_add_env(),
    )
    if r.returncode != 0:
        return False
    return fp in r.stdout


def _agent_delete(pub: Path) -> None:
    if not os.environ.get("SSH_AUTH_SOCK") or not pub.is_file():
        return
    subprocess.run(["ssh-add", "-d", str(pub)], capture_output=True, check=False, env=_ssh_add_env())


def _ssh_keygen_pub(priv: Path) -> str:
    r = subprocess.run(
        ["ssh-keygen", "-y", "-f", str(priv), "-P", ""],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        err = (r.stderr or "").strip() or str(r.returncode)
        sys.exit(f"not an ssh private key: {err}")
    return r.stdout


def _ssh_add(priv: Path, ttl: int) -> None:
    cmd = ["ssh-add"]
    if ttl > 0:
        cmd += ["-t", str(ttl)]
    cmd += ["-q", str(priv)]
    r = subprocess.run(cmd, capture_output=True, text=True, env=_ssh_add_env())
    if r.returncode != 0:
        err = (r.stderr or "").strip() or str(r.returncode)
        sys.exit(
            f"ssh-add failed: {err}\n"
            f"hint: {AGENT_HINT}"
        )


def _load_private_into_agent(value: str, pub_path: Path, ttl: int) -> None:
    """Write .pub, ssh-add -t, unlink the private temp file. Never leaves a private key path."""
    _ensure_dir(pub_path.parent, 0o700)
    if pub_path.is_file():
        _agent_delete(pub_path)
    fd, tmp = tempfile.mkstemp(dir=str(pub_path.parent), prefix=".#priv.")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(value)
            if not value.endswith("\n"):
                f.write("\n")
        tmp_path.chmod(0o600)
        pub = _ssh_keygen_pub(tmp_path)
        _write_key_atomic(pub_path, pub)
        _ssh_add(tmp_path, ttl)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def _sweep_key_dir(key_dir: Path, expected_pubs: set[str] | None = None) -> int:
    """Drop leftover private files; if *expected_pubs* is set, also drop orphan .pub files."""
    if not key_dir.is_dir():
        return 0
    n = 0
    for p in key_dir.iterdir():
        if not p.is_file():
            continue
        if p.name.startswith(".#priv."):
            drop = True
        elif p.name.startswith("."):
            continue
        else:
            drop = p.suffix != ".pub" or (expected_pubs is not None and p.name not in expected_pubs)
        if not drop:
            continue
        if p.suffix == ".pub":
            _agent_delete(p)
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def _purge_key_dir(key_dir: Path) -> None:
    if not key_dir.is_dir():
        return
    for p in key_dir.iterdir():
        if p.is_file() and p.suffix == ".pub":
            _agent_delete(p)
    shutil.rmtree(key_dir, ignore_errors=True)


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


def _corvus_executable() -> str:
    """Absolute path of *this* CLI so Match exec cannot pick a different install from PATH."""
    argv0 = sys.argv[0]
    try:
        p = Path(argv0).expanduser()
        if p.name in ("corvus", "corvus.exe"):
            if p.is_file():
                return str(p.resolve())
            w = shutil.which(argv0) if not p.is_absolute() else None
            if w:
                return str(Path(w).resolve())
    except OSError:
        pass
    return shutil.which("corvus") or "corvus"


def _ensure_exec_cmd(alias: str, key_dir: Path, prefix: str) -> str:
    parts = [_corvus_executable(), "ssh", "_ensure", alias, "--key-dir", str(key_dir)]
    if prefix != DEFAULT_PREFIX:
        parts += ["--prefix", prefix]
    return " ".join(shlex.quote(p) for p in parts)


def _write_fragment(entries: list[dict], key_dir: Path, frag: Path, prefix: str, lazy: bool = False) -> None:
    lines: list[str] = ["# corvus ssh - generated, do not edit", f"# prefix={prefix!r} key_dir={key_dir} lazy={lazy} agent=1", ""]
    if not entries:
        lines.append("# (no ssh hosts found)")
    for e in sorted(entries, key=lambda x: x["alias"]):
        aliases = e.get("aliases") or [e["alias"]]
        ident = _cfg_path(_identity_path(key_dir, e["file"]))
        if lazy:
            # JIT: ssh triggers corvus ssh _ensure on first use; private key lives in ssh-agent until TTL
            # originalhost: Match host re-parses after HostName, so ssh host-acct also matched the
            # bare-hostname block and stacked that account's IdentityFile.
            # Comma-separated pattern list (Host uses spaces). A space after the first alias is
            # parsed as the next Match attribute.
            host_pat = ",".join(aliases)
            ensure = _ensure_exec_cmd(e["alias"], key_dir, prefix)
            lines.append(f'Match originalhost {host_pat} exec "{ensure}"')
            hostname = e.get("hostname")
            if hostname and any(a != hostname for a in aliases):
                lines.append(f"  HostName {hostname}")
            user = e.get("user")
            if user:
                lines.append(f"  User {user}")
            lines.append(f"  IdentityFile {ident}")
            lines.append("  IdentitiesOnly yes")
            lines.append("")
            continue
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
        lines.append(f"  IdentityFile {ident}")
        lines.append("  IdentitiesOnly yes")
        lines.append("")
    content = "\n".join(lines)
    frag.parent.mkdir(parents=True, exist_ok=True)
    try:
        frag.parent.chmod(0o700)
    except OSError:
        pass

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


def _sorted_entries(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda x: x["alias"])


def _host_table(entries: list[dict]) -> tuple[list[str], list[list[str]]]:
    rows = []
    for e in _sorted_entries(entries):
        aliases = e.get("aliases") or [e["alias"]]
        extra = " ".join(a for a in aliases if a != e["alias"])
        rows.append([e["alias"], e.get("user") or "", extra, f"ssh {e['alias']}"])
    has_user = any(r[1] for r in rows)
    has_alias = any(r[2] for r in rows)
    headers = ["HOST"]
    out_rows: list[list[str]] = []
    for r in rows:
        row = [r[0]]
        if has_user:
            row.append(r[1])
        if has_alias:
            row.append(r[2])
        row.append(r[3])
        out_rows.append(row)
    if has_user:
        headers.append("USER")
    if has_alias:
        headers.append("ALIASES")
    headers.append("CONNECT")
    return headers, out_rows


def _print_hosts(entries: list[dict], title: str = "") -> None:
    if not entries:
        print("(no ssh hosts found)")
        return
    headers, rows = _host_table(entries)
    try:
        from rich.console import Console  # type: ignore
        from rich.table import Table  # type: ignore

        t = Table(title=title or None, show_lines=False)
        for h in headers:
            t.add_column(h, overflow="fold")
        for r in rows:
            t.add_row(*r)
        Console().print(t)
    except Exception:
        print_table(headers, rows)


def _next_ssh(entries: list[dict], agent_st: str) -> None:
    host = _sorted_entries(entries)[0]["alias"] if entries else "<host>"
    if agent_st == "missing":
        print(f"next: start ssh-agent, then: ssh {host}\n  {AGENT_HINT}", file=sys.stderr)
        return
    if entries:
        print(f"next: ssh {host}", file=sys.stderr)


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
    eager = bool(getattr(args, "eager", False))

    candidates = _list_candidates(project, prefix)
    host_map = _load_host_map()
    entries = _derive_entries(candidates, prefix, host_map)

    if out_mode == "json" and dry_run:
        emit({"prefix": prefix, "key_dir": str(key_dir), "fragment": str(frag), "hosts": [e["alias"] for e in entries], "keys": {e["alias"]: e["key"] for e in entries}, "entries": entries}, "json")
        return

    if dry_run:
        _print_hosts(entries, title=f"{len(entries)} host(s)")
        if entries:
            print("nothing fetched; `ssh <host>` loads that key into ssh-agent", file=sys.stderr)
        return

    agent_st = _warn_if_no_agent()
    if do_fragment and _should_wire_include(args):
        _install_include(_ssh_config_path(getattr(args, "ssh_config", None)), frag)

    # lazy (default): only write fragment; private keys loaded into ssh-agent on first ssh via _ensure
    if not eager:
        _ensure_dir(key_dir, 0o700)
        if do_fragment:
            _write_fragment(entries, key_dir, frag, prefix, lazy=True)
        removed = _sweep_key_dir(key_dir, {f"{e['file']}.pub" for e in entries} if do_clean else None)
        if do_clean and removed:
            print(f"cleaned {removed} stale file(s) from {key_dir}", file=sys.stderr)
        elif removed:
            print(f"removed {removed} leftover private key(s) from {key_dir}", file=sys.stderr)
        if out_mode == "json":
            emit({"mode": "lazy", "agent": True, "hosts": [e["alias"] for e in entries], "key_dir": str(key_dir), "fragment": str(frag) if do_fragment else None, "ttl": DEFAULT_TTL, "prefix": prefix}, "json")
            return
        _print_hosts(entries, title=f"{len(entries)} host(s) · keys load on first ssh")
        if not entries:
            print(f"no ssh hosts (prefix={prefix!r})", file=sys.stderr)
        _next_ssh(entries, agent_st)
        return

    _require_ssh_agent()
    _ensure_dir(key_dir, 0o700)
    ok_entries: list[dict] = []
    failed: list[tuple[str, str]] = []
    ttl = _ttl_from_args(args)
    # rich progress for eager bulk fetch
    try:
        from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn  # type: ignore
        from rich.console import Console  # type: ignore

        use_progress = sys.stderr.isatty() and len(entries) > 1 and out_mode != "json"
    except Exception:
        use_progress = False

    def _fetch_all_eager():
        nonlocal ok_entries, failed
        work = list(sorted(entries, key=lambda x: x["alias"]))
        if use_progress:
            console = Console(stderr=True)
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(), TaskProgressColumn(), console=console) as prog:
                task = prog.add_task(f"loading {len(work)} key(s) into ssh-agent", total=len(work))
                for e in work:
                    prog.update(task, description=f"fetch {e['alias']}")
                    try:
                        val = _fetch_value(e["key"], project)
                        _load_private_into_agent(val, _identity_path(key_dir, e["file"]), ttl)
                        ok_entries.append(e)
                    except SystemExit as exc:
                        failed.append((e["alias"], str(exc).splitlines()[0][:120]))
                    prog.advance(task)
            return
        for e in work:
            try:
                val = _fetch_value(e["key"], project)
                _load_private_into_agent(val, _identity_path(key_dir, e["file"]), ttl)
                ok_entries.append(e)
            except SystemExit as exc:
                failed.append((e["alias"], str(exc).splitlines()[0][:120]))

    _fetch_all_eager()

    if do_fragment:
        _write_fragment(ok_entries, key_dir, frag, prefix, lazy=True)

    expected = {f"{e['file']}.pub" for e in ok_entries} if do_clean else None
    _sweep_key_dir(key_dir, expected)

    if out_mode == "json":
        emit({"ok": [e["alias"] for e in ok_entries], "failed": failed, "key_dir": str(key_dir), "fragment": str(frag), "agent": True}, "json")
        return
    _print_hosts(ok_entries, title=f"{len(ok_entries)} host(s) · loaded into ssh-agent")
    if failed:
        print("\nfailed:", file=sys.stderr)
        for h, msg in failed:
            print(f"  {h}: {msg}", file=sys.stderr)
    print(f"loaded {len(ok_entries)} key(s) into ssh-agent", file=sys.stderr)
    _next_ssh(ok_entries, "ok")
    if failed:
        sys.exit(1)


def _cmd_setup(args) -> None:
    frag = _resolve_frag(getattr(args, "config_fragment", None))
    ssh_config = _ssh_config_path(getattr(args, "ssh_config", None))
    _install_include(ssh_config, frag)
    _cmd_sync(args)


def _cmd_uninstall(args) -> None:
    frag = _resolve_frag(getattr(args, "config_fragment", None))
    ssh_config = _ssh_config_path(getattr(args, "ssh_config", None))
    line = _include_line_for(frag)
    if ssh_config.is_file():
        text = ssh_config.read_text()
        new = text
        for needle in (line, INCLUDE_LINE):
            new = new.replace(needle + "\n", "").replace(needle, "")
        if new != text:
            ssh_config.write_text(new)
            print(f"removed Include from {ssh_config}", file=sys.stderr)
        else:
            print(f"{ssh_config} has no Corvus Include", file=sys.stderr)
    else:
        print(f"{ssh_config} not found", file=sys.stderr)
    purge = bool(getattr(args, "purge", False))
    key_dir = _resolve_key_dir(getattr(args, "key_dir", None))
    if purge:
        _purge_key_dir(key_dir)
        if frag.is_file():
            try:
                frag.unlink()
                print(f"removed fragment {frag}", file=sys.stderr)
            except OSError as e:
                print(f"could not remove fragment {frag}: {e}", file=sys.stderr)
        print(f"purged key dir {key_dir}", file=sys.stderr)
    elif frag.is_file():
        print(f"kept fragment {frag} (use --purge to delete keys too)", file=sys.stderr)


def _cmd_config(args) -> None:
    action = getattr(args, "config_action", None) or getattr(args, "action", None) or "install"
    if action == "install":
        frag = _resolve_frag(getattr(args, "config_fragment", None))
        ssh_config = _ssh_config_path(getattr(args, "ssh_config", None))
        if not _install_include(ssh_config, frag):
            print(f"{ssh_config} already includes {_include_line_for(frag)}", file=sys.stderr)
        print("next: corvus ssh sync   (or: corvus ssh setup)", file=sys.stderr)
        return
    if action == "uninstall":
        _cmd_uninstall(args)
        return
    sys.exit("ssh config: use 'install' or 'uninstall'")


def _cmd_status(args) -> None:
    key_dir = _resolve_key_dir(getattr(args, "key_dir", None))
    frag = _resolve_frag(getattr(args, "config_fragment", None))
    ssh_config = _ssh_config_path(getattr(args, "ssh_config", None))
    st, n = _agent_status()
    agent_label = {
        "ok": f"running ({n} identit{'y' if n == 1 else 'ies'})",
        "empty": "running (no identities yet)",
        "missing": "not running",
    }[st]
    include = "yes" if _include_present(ssh_config, frag) else "missing"
    frag_label = str(frag) + ("  (ok)" if frag.is_file() else "  (missing)")
    rows = [
        ["ssh-agent", agent_label],
        ["Include", f"{include}  ({ssh_config})"],
        ["fragment", frag_label],
        ["key dir", str(key_dir)],
    ]
    out_mode = getattr(args, "output", None) or "table"
    pubs: list[Path] = []
    if key_dir.is_dir():
        pubs = sorted(p for p in key_dir.iterdir() if p.is_file() and p.suffix == ".pub")
    loaded = []
    for p in pubs:
        loaded.append([p.stem, "yes" if st != "missing" and _pub_in_agent(p) else "no"])

    catalog: list[dict] = []
    try:
        prefix = getattr(args, "prefix", None)
        if prefix is None:
            prefix = DEFAULT_PREFIX
        catalog = _derive_entries(
            _list_candidates(getattr(args, "project", None), prefix),
            prefix,
            _load_host_map(),
        )
    except SystemExit:
        catalog = []

    if out_mode == "json":
        emit(
            {
                "agent": st,
                "identities": n,
                "include": include == "yes",
                "ssh_config": str(ssh_config),
                "fragment": str(frag),
                "key_dir": str(key_dir),
                "pubs": [{"file": a, "in_agent": b == "yes"} for a, b in loaded],
                "hosts": [e["alias"] for e in catalog],
            },
            "json",
        )
        return
    print_table(["CHECK", "VALUE"], rows)
    if catalog:
        print()
        _print_hosts(catalog)
    elif loaded:
        print()
        print_table(["KEY", "IN_AGENT"], loaded)
    in_ag = [name for name, yes in loaded if yes == "yes"]
    if in_ag:
        print(f"in ssh-agent: {', '.join(in_ag)}", file=sys.stderr)
    if include != "yes":
        print("next: corvus ssh setup", file=sys.stderr)
    elif st == "missing":
        print(f"next: {AGENT_HINT}", file=sys.stderr)
    elif catalog:
        print(f"next: ssh {_sorted_entries(catalog)[0]['alias']}", file=sys.stderr)


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
    ttl = _ttl_from_args(args)
    force = bool(getattr(args, "force", False))

    _require_ssh_agent()
    _ensure_dir(key_dir, 0o700)
    _sweep_key_dir(key_dir)

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

    pub = _identity_path(key_dir, file_name)
    if pub.is_file() and not force and ttl > 0 and _pub_in_agent(pub):
        sys.exit(0)

    assert key is not None
    val = _fetch_value(key, project)
    _load_private_into_agent(val, pub, ttl)
    sys.exit(0)


def cmd_ssh(args) -> None:
    """Dispatch ``corvus ssh`` subcommands.

    Description:
        Routes to setup/sync/list/status/uninstall/config/_ensure.
        With no subcommand prints a short how-to.

    Inputs:
        args: namespace with ``ssh_cmd`` and delegated flags.

    Outputs:
        Delegates to sub-handlers; exits on unknown subcommand.
    """
    sub = getattr(args, "ssh_cmd", None)
    if sub in (None, "help"):
        print(SSH_HELP, end="")
        sys.exit(0)
    if sub == "setup":
        _cmd_setup(args)
    elif sub == "sync":
        _cmd_sync(args)
    elif sub == "list":
        args.dry_run = True
        _cmd_sync(args)
    elif sub == "status":
        _cmd_status(args)
    elif sub == "uninstall":
        _cmd_uninstall(args)
    elif sub == "config":
        _cmd_config(args)
    elif sub == "_ensure":
        _cmd_ensure(args)
    else:
        print(SSH_HELP, end="")
        sys.exit(2)
