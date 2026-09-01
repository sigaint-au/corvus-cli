"""Credential config, validation and helper utilities.

Description:
    Reads ``SS_*`` env vars and ``~/.config/corvus/config`` via :mod:`configparser`
    (percent-safe), resolves project names→UUIDs for PAT tokens, validates
    URLs/tokens/timeouts, and provides atomic config writes plus misc helpers
    (``expect_confirm``, ``_suggest_resource``) used across commands.

Inputs:
    Env (``SS_URL``/``SS_TOKEN``/``SS_PROJECT``/``PID``/``SS_TIMEOUT``), config
    file path ``~/.config/corvus/config``, plus function args (``url``, ``token``,
    ``project``, ``role``, …).

Outputs:
    Dicts of creds, validated strings, timeout ints, booleans from confirm
    prompts, suggestions, timeout/URL diagnostics.

Example:
    >>> import os
    >>> from pathlib import Path
    >>> from corvus_cli import config as cfg
    >>> cfg._timeout()  # doctest: +SKIP
    60
    >>> cfg._validate_url("https://secrets.example") is None
    True
    >>> cfg._suggest_resource("secerts")  # typo
    'secrets'
"""

from __future__ import annotations

import difflib
import os
import sys
import tempfile
import urllib.parse
from configparser import ConfigParser
from pathlib import Path

from corvus_cli.constants import (
    CONFIG_DIR,
    CONFIG_PATH,
    ENV_PROJECT_ALIASES,
    ENV_TIMEOUT,
    ENV_TOKEN,
    ENV_URL,
    TIMEOUT_DEFAULT,
    TIMEOUT_MAX,
    TIMEOUT_MIN,
    KNOWN_RESOURCES,
    _PROJECT_ROLES,
    _SECRET_ROLES,
    _SERVICE_ROLES,
    _TEAM_ROLES,
    _UUID_RE,
)


def _env_first(*names: str) -> str:
    """Return the first non-empty env var among *names*.

    Description:
        Scans env names in order, returns first truthy value or empty string.
        Used for ``SS_PROJECT``/``PID`` alias handling.

    Inputs:
        *names: env var names, e.g. ``"SS_PROJECT"``, ``"PID"``.

    Outputs:
        The env value as ``str``; ``""`` if none set.

    Example:
        >>> import os; os.environ["SS_PROJECT"] = "proj-uuid"
        >>> _env_first("SS_PROJECT", "PID")
        'proj-uuid'
    """
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return ""


def _load_config() -> dict[str, str]:
    """Load credentials with env-over-file precedence.

    Description:
        Reads ``~/.config/corvus/config`` ``[default]`` via :class:`ConfigParser`
        (interpolation disabled so ``%`` in URLs/tokens survives) then lets env
        vars override. Emits ``warning: SS_PROJECT overrides config`` to stderr
        when both sources provide a project.

    Inputs:
        Env (``SS_URL``, ``SS_TOKEN``, ``SS_PROJECT``/``PID``) and
        ``CONFIG_PATH`` on disk.

    Outputs:
        ``{url, token, project}`` dict of strings (``""`` if absent).

    Example:
        >>> _load_config()  # doctest: +SKIP
        {'url': 'https://secrets.example', 'token': 'pat_…', 'project': '…'}
    """
    cfg = ConfigParser(interpolation=None)
    if CONFIG_PATH.is_file():
        cfg.read(CONFIG_PATH)
    sec = cfg["default"] if cfg.has_section("default") else {}
    env_url = _env_first(ENV_URL)
    env_token = _env_first(ENV_TOKEN)
    env_project = _env_first(*ENV_PROJECT_ALIASES)
    file_project = sec.get("project", "") if isinstance(sec.get("project", ""), str) else ""
    if env_project and file_project and env_project != file_project:
        print(
            f"warning: {ENV_PROJECT}/PID env overrides config project",
            file=sys.stderr,
        )
    return {
        "url": env_url or sec.get("url", ""),
        "token": env_token or sec.get("token", ""),
        "project": env_project or file_project,
    }


def _file_config() -> dict[str, str]:
    """Load only the config file (no env).

    Description:
        Used by ``project`` and ``login`` to decide whether to persist to disk
        rather than clobber an env-only session.

    Inputs:
        ``CONFIG_PATH`` if present.

    Outputs:
        ``{url, token, project}`` from file only.

    Example:
        >>> _file_config()  # doctest: +SKIP
        {'url': 'https://secrets.example', 'token': 'pat_…', 'project': '…'}
    """
    cfg = ConfigParser(interpolation=None)
    if CONFIG_PATH.is_file():
        cfg.read(CONFIG_PATH)
    sec = cfg["default"] if cfg.has_section("default") else {}
    return {
        "url": sec.get("url", ""),
        "token": sec.get("token", ""),
        "project": sec.get("project", ""),
    }


def _save_config(url: str, token: str, project: str) -> None:
    """Persist credentials atomically with 0600 perms.

    Description:
        Writes ``[default]`` to a temp file with mode ``0o600`` then
        atomically renames over ``CONFIG_PATH``. Parent dir is created
        ``0o700``. No window where the file has broader perms.

    Inputs:
        url: base URL (trailing slash stripped). token: ``ss_``/``pat_``/``sso_``
        prefixed. project: UUID or name (possibly empty for PAT login).

    Outputs:
        None; creates/overwrites ``CONFIG_PATH`` on disk.

    Example:
        >>> _save_config("https://secrets.example", "pat_x", "ios-app")  # doctest: +SKIP
    """
    CONFIG_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        CONFIG_DIR.chmod(0o700)
    except OSError:
        pass
    cfg = ConfigParser(interpolation=None)
    cfg["default"] = {"url": url.rstrip("/"), "token": token, "project": project}

    # Atomic write: create tmp in same dir with 0600 then rename.
    fd, tmp = tempfile.mkstemp(dir=str(CONFIG_DIR), prefix=".config.")
    try:
        with os.fdopen(fd, "w") as f:
            cfg.write(f)
        Path(tmp).chmod(0o600)
        Path(tmp).replace(CONFIG_PATH)
    finally:
        try:
            Path(tmp).unlink()
        except FileNotFoundError:
            pass


def _check_config_perms() -> None:
    """Warn if config file is broader than 0600 or dir broader than 0700.

    Description:
        Inspects ``CONFIG_PATH`` and ``CONFIG_DIR`` mode bits; warns to stderr
        if group/other bits are set. Never fails the command; user fixes perms
        at leisure.

    Inputs:
        Filesystem state of ``CONFIG_PATH``/``CONFIG_DIR``.

    Outputs:
        None; may print warnings to ``sys.stderr``.

    Example:
        >>> _check_config_perms()  # doctest: +SKIP
    """
    import stat as _stat

    for p, want in ((CONFIG_PATH, 0o600), (CONFIG_DIR, 0o700)):
        try:
            mode = p.stat().st_mode
        except FileNotFoundError:
            continue
        # Any group/other read/write/exec when not expected is too broad.
        if want == 0o600 and (mode & 0o077):
            print(f"warning: {p} has broad perms ({oct(_stat.S_IMODE(mode))}); expected 0600", file=sys.stderr)
        if want == 0o700 and (mode & 0o077):
            print(f"warning: {p} has broad perms ({oct(_stat.S_IMODE(mode))}); expected 0700", file=sys.stderr)


def _token_kind(token: str) -> str:
    """Classify a token by prefix.

    Description:
        Returns ``"machine"`` for ``ss_``, ``"pat"`` for ``pat_``/``sso_``,
        else ``"unknown"``.

    Inputs:
        token: raw token string (possibly empty).

    Outputs:
        One of ``"machine"``, ``"pat"``, ``"unknown"``.

    Example:
        >>> _token_kind("ss_abc")
        'machine'
        >>> _token_kind("sso_xyz")
        'pat'
    """
    if token.startswith("ss_"):
        return "machine"
    if token.startswith("pat_") or token.startswith("sso_"):
        return "pat"
    return "unknown"


def _require_creds() -> tuple[str, str, str, str]:
    """Load and validate credentials for any API call.

    Description:
        Ensures URL + token present and token has a known prefix, warns on
        insecure URL scheme.

    Inputs:
        Env/config as loaded by :func:`_load_config`.

    Outputs:
        ``(base_url, token, project, kind)`` where ``kind`` is
        ``machine``/``pat``. Exits 1 on missing/invalid creds.

    Example:
        >>> _require_creds()  # doctest: +SKIP
        ('https://secrets.example', 'pat_…', 'ios-app', 'pat')
    """
    c = _load_config()
    missing = [k for k in ("url", "token") if not c.get(k)]
    if missing:
        sys.exit(f"missing {', '.join(missing)}; set env or run: corvus login")
    kind = _token_kind(c["token"])
    if kind == "unknown":
        sys.exit("token must start with ss_…, pat_…, or sso_…")
    _warn_insecure_url(c["url"])
    _check_config_perms()
    return c["url"].rstrip("/"), c["token"], c.get("project") or "", kind


def _require_project() -> str:
    """Return validated current project or exit.

    Description:
        Ensures project is set and that machine tokens carry a UUID.

    Inputs:
        Creds via :func:`_require_creds`.

    Outputs:
        Project string (UUID or name). Exits 1 when missing/invalid.

    Example:
        >>> _require_project()  # doctest: +SKIP
        '31a70875-…'
    """
    _, _, project, kind = _require_creds()
    if not project:
        sys.exit("no project set; run: corvus project <name> or login --project …")
    if kind == "machine" and not _UUID_RE.match(project):
        sys.exit("machine tokens require project UUID")
    return project


def _require_pat() -> tuple[str, str]:
    """Require a PAT/sso token; exit otherwise.

    Description:
        Used to gate manage API access; exits with guidance when token is
        ``ss_``.

    Inputs:
        Creds via :func:`_require_creds`.

    Outputs:
        ``(base_url, token)``. Exits 1 when not ``pat``.

    Example:
        >>> _require_pat()  # doctest: +SKIP
        ('https://secrets.example', 'pat_…')
    """
    base, token, _, kind = _require_creds()
    if kind != "pat":
        sys.exit("this command requires a pat_… or sso_… token")
    return base, token


def _timeout() -> int:
    """Return clamped HTTP timeout seconds.

    Description:
        Reads ``SS_TIMEOUT`` (default 60), clamps to ``[TIMEOUT_MIN, TIMEOUT_MAX]``
        (``5..300``). Returns ``TIMEOUT_DEFAULT`` on parse error.

    Inputs:
        ``SS_TIMEOUT`` env var.

    Outputs:
        Timeout int in ``5..300``.

    Example:
        >>> import os; os.environ["SS_TIMEOUT"] = "999"; _timeout()
        300
    """
    try:
        return max(TIMEOUT_MIN, min(TIMEOUT_MAX, int(os.environ.get(ENV_TIMEOUT, str(TIMEOUT_DEFAULT)))))
    except (TypeError, ValueError):
        return TIMEOUT_DEFAULT


def _validate_url(url: str) -> str | None:
    """Validate base URL scheme/host; allow only http on localhost.

    Description:
        Returns ``None`` when URL is acceptable, or an error string to surface
        to the user. ``http://`` is accepted only for ``localhost``/``127.0.0.1``/``::1``.

    Inputs:
        url: candidate base URL, e.g. ``https://secrets.example``.

    Outputs:
        ``None`` when valid, else error string.

    Example:
        >>> _validate_url("https://secrets.example") is None
        True
        >>> _validate_url("http://localhost:8080") is None
        True
        >>> bool(_validate_url("http://evil.example"))
        True
    """
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https"):
        return "URL must start with https:// (or http:// for localhost)"
    if p.scheme == "http" and p.hostname not in ("localhost", "127.0.0.1", "::1"):
        return "refusing http:// for non-localhost; use https:// or http://localhost"
    if not p.netloc:
        return "invalid URL: missing host"
    return None


def _warn_insecure_url(url: str) -> None:
    """Emit a warning if URL would be rejected by :func:`_validate_url`.

    Description:
        Non-fatal check used on every API path so a pre-existing config with
        ``http://`` still warns rather than silently sending secrets in cleartext.

    Inputs:
        url: base URL from config/env.

    Outputs:
        None; may print to ``sys.stderr``.

    Example:
        >>> _warn_insecure_url("https://secrets.example")  # doctest: +SKIP
    """
    msg = _validate_url(url)
    if msg:
        print(f"warning: {msg} (url={url})", file=sys.stderr)


def _valid_role(role: str | None, default: str, allowed: tuple[str, ...]) -> str:
    """Validate a role name or return the default.

    Description:
        Strips whitespace, falls back to *default* when ``role`` is falsy,
        exits 1 listing valid roles when invalid.

    Inputs:
        role: requested role or ``None``. default: fallback. allowed: tuple of
        valid role strings.

    Outputs:
        Validated role string; never returns invalid value (exits instead).

    Example:
        >>> _valid_role(None, "team-member", _TEAM_ROLES)
        'team-member'
    """
    role = (role or default).strip()
    if role not in allowed:
        sys.exit(f"role must be one of: {', '.join(allowed)} (got {role!r})")
    return role


def _require_confirm(args, action: str) -> None:
    """Exit unless ``--yes`` was supplied; else return.

    Description:
        Guards destructive/purge/bulk-export paths. Prints actionable message.

    Inputs:
        args: namespace with ``.yes`` bool. action: human label, e.g.
        ``"delete team"``.

    Outputs:
        None; exits 1 when not confirmed.

    Example:
        >>> import argparse; ns = argparse.Namespace(yes=True)
        >>> _require_confirm(ns, "delete team")  # succeeds
    """
    if getattr(args, "yes", False):
        return
    sys.exit(f"refusing: {action} is destructive; re-run with --yes")


def _expect_confirm_export(args) -> None:
    """Require ``--yes`` for bulk plaintext export; handling TTY hint.

    Description:
        Export prints every secret value; this gate prevents accidental
        exfiltration from history or pasted commands. Prints a hint about
        redirecting when not a TTY.

    Inputs:
        args: namespace with ``.yes`` bool.

    Outputs:
        None; exits 1 when not confirmed.

    Example:
        >>> import argparse; ns = argparse.Namespace(yes=True)
        >>> _expect_confirm_export(ns)  # ok
    """
    if getattr(args, "yes", False):
        return
    sys.exit(
        "refusing: export dumps all plaintext secrets; re-run with --yes "
        "(e.g. corvus export -o env --yes > .env)"
    )


def _suggest_resource(typed: str) -> str | None:
    """Return closest known resource name or None.

    Description:
        Uses :func:`difflib.get_close_matches` with cutoff 0.6.

    Inputs:
        typed: user-supplied resource token.

    Outputs:
        Best match or ``None``.

    Example:
        >>> _suggest_resource("secerts")
        'secrets'
    """
    hits = difflib.get_close_matches(typed, list(KNOWN_RESOURCES), n=1, cutoff=0.6)
    return hits[0] if hits else None


def _resolve_project_if_needed(url: str, token: str, project: str) -> str:
    """Resolve a PAT project name→UUID via the API; return unchanged if already UUID.

    Description:
        For ``pat_``/``sso_`` tokens a human-friendly project name can be
        resolved via ``GET /eso/v1/projects?name=…``. UUIDs and ``ss_`` bypass
        the lookup.

    Inputs:
        url: base URL. token: bearer token. project: UUID or name.

    Outputs:
        Project UUID when resolved, otherwise the original input. Exits on
        ambiguous or missing project.

    Example:
        >>> _resolve_project_if_needed("https://h", "pat_x", "ios-app")  # doctest: +SKIP
        '31a70875-…'
    """
    # local import to avoid cycle with api
    from corvus_cli.api import _http

    if _UUID_RE.match(project) or _token_kind(token) != "pat":
        return project
    data = _http(
        "GET",
        f"{url.rstrip('/')}/eso/v1/projects",
        token=token,
        query={"name": project},
    )
    items = (data or {}).get("items") or []  # type: ignore[union-attr]
    exact = [i for i in items if i.get("name") == project]
    if len(exact) == 1:
        return exact[0]["id"]
    if len(items) == 1:
        return items[0]["id"]
    if not items:
        sys.exit(f"no project named {project!r}")
    sys.exit(
        "ambiguous project; candidates: "
        + ", ".join(f"{i.get('name')}={i.get('id')}" for i in items[:8])
    )


def _parse_meta_flags(flags: list[str]) -> list[tuple[str, str]]:
    """Parse ``--meta KEY=VALUE`` entries.

    Description:
        Validates each flag contains ``=``; strips key whitespace; exits on
        malformed input.

    Inputs:
        flags: list of ``KEY=VALUE`` strings from argparse.

    Outputs:
        List of ``(key, value)`` tuples.

    Example:
        >>> _parse_meta_flags(["owner=platform", "env=prod"])
        [('owner', 'platform'), ('env', 'prod')]
    """
    out: list[tuple[str, str]] = []
    for f in flags:
        if "=" not in f:
            sys.exit(f"--meta needs KEY=VALUE (got {f!r})")
        k, _, v = f.partition("=")
        out.append((k.strip(), v))
    return out


def _read_secret_value(args) -> str | None:
    """Read the secret value from exactly one of --value/--from-file/--from-env.

    Description:
        Enforces single-source, handles ``-`` as stdin, missing env, and unreadable
        files with clean exit messages.

    Inputs:
        args: namespace with ``from_file``, ``from_env``, ``value``.

    Outputs:
        Secret value string or ``None`` when no source supplied; exits on
        conflicting sources or read errors.

    Example:
        >>> import argparse, os
        >>> ns = argparse.Namespace(from_file=None, from_env="MY_VAL", value=None)
        >>> os.environ["MY_VAL"] = "s3cret"  # doctest: +SKIP
        >>> _read_secret_value(ns)  # doctest: +SKIP
        's3cret'
    """
    sources = [
        getattr(args, "from_file", None) is not None,
        getattr(args, "from_env", None) is not None,
        getattr(args, "value", None) is not None,
    ]
    if sum(1 for s in sources if s) > 1:
        sys.exit("use only one of: --value, --from-file, --from-env")
    file_arg = getattr(args, "from_file", None)
    if file_arg is not None:
        if file_arg == "-":
            return sys.stdin.read()
        try:
            return Path(file_arg).read_text()
        except OSError as e:
            sys.exit(f"cannot read {file_arg!r}: {e}")
    env_name = getattr(args, "from_env", None)
    if env_name is not None:
        if env_name not in os.environ:
            sys.exit(f"env {env_name!r} not set")
        return os.environ[env_name]
    v = getattr(args, "value", None)
    if v is not None:
        return v
    return None
