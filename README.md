# corvus-cli

[![CI](https://img.shields.io/badge/CI-Forgejo-blue)](#development)
[![Python](https://img.shields.io/badge/python-%3E%3D3.9-blue)](#requirements)
[![License](https://img.shields.io/badge/license-AGPL--3.0--or--later-green)](LICENSE)
[![RPM](https://img.shields.io/badge/RPM-noarch-red)](#installation)

Command-line client for [Corvus](https://git.sigaint.au/Sigaint/corvus). It talks to `/eso/v1` and `/api/v1/manage` to manage secrets, teams, projects, and admin. No runtime dependencies beyond stdlib. Built for RHEL 9+.

```
corvus login --url https://secrets.example.com --token pat_… --project ios-app
corvus get secrets
corvus get secret API_KEY -o value
printf '%s' "$NEW" | corvus apply secret API_KEY --from-file=-
```

`corvus` or `corvus --help` prints usage. `corvus --version` prints the version. `man corvus` has the full reference.

---

## Features

- Secrets: list metadata, get one secret, apply, delete, request reveal, check history, use folders, and export. Bulk listing never returns values.
- SSH hosts: out-of-the-box `kind=ssh` (including `corvus-agent` `hosts/<host>/users/<acct>`) → `~/.config/corvus/keys` (0700/0600, atomic) + `~/.ssh/config.d/corvus` Include for native `ssh <host>` (`ssh web01` sets `User <acct>`, `Host <host>` / `Host <host>-<acct>` aliases, `HostName` when needed, `IdentityFile`/`IdentitiesOnly yes`).
- Access control: per-secret modes, approval requirements, bindings (`grant`/`unbind`), and project settings.
- Org: teams, projects, members, groups, scoped machine tokens, and trash.
- Admin: users, audit by source (`project`, `org`, `secret`, `access`), and access requests.
- Output and UX: tables by default, plus JSON, raw values, and names. Use `--no-trunc` to keep full cells. You get did-you-mean hints, auto-pagination with `--limit`/`--page-size`/`--no-paginate`, and a warning when env vars override config.
- Safety: the CLI writes config atomically with `0600` (dir `0700`), enforces HTTPS, clamps timeouts, gates `export` behind `--yes`, and warns on broad perms.
- Shell: completion for `bash`, `zsh`, and `fish`. Paste `sso_…` login commands directly. Read values from stdin or env vars.

---

## Requirements

- Python >= 3.9
- Linux (RHEL 9+ tested), macOS, or any POSIX with Python 3
- No third-party runtime deps

---

## Installation

### pip / pipx (recommended for dev)

```bash
pipx install .
# or
python3 -m pip install .
corvus --version
```

### Standalone script (no pip)

```bash
sudo install -m 0755 corvus /usr/bin/corvus
sudo install -m 0644 corvus.1 /usr/share/man/man1/corvus.1
```

Uses `#!/usr/bin/env python3` and the `corvus_cli/` package alongside `corvus`.

### RPM (RHEL / Fedora)

```bash
make rpm
sudo dnf install -y dist/corvus-cli-*.noarch.rpm
```

Version comes from `pyproject.toml`.

---

## Quick start

```bash
# Machine token (one project, secrets only)
corvus login --url https://secrets.example.com --token ss_… --project 31a70875-7d6a-40a7-a315-751f8a7ee38f
corvus get secrets
corvus get secret API_KEY -o value

# PAT / sso session (human, all teams/projects, name resolution)
corvus login --url https://secrets.example.com --token pat_…
corvus project ios-app
corvus get secrets -l platform-team -o json

# Web UI "Copy login command" paste
corvus login --url https://secrets.example.com --token sso_…

# CI: env only, no config file
export SS_URL=https://secrets.example.com
export SS_TOKEN=ss_…    # never commit
export SS_PROJECT=31a70875-7d6a-40a7-a315-751f8a7ee38f
corvus get secrets
```

`configure` is an alias for `login`.

---

## Credentials

| Env | Meaning |
|-----|---------|
| `SS_URL` | Base URL (no trailing slash). `https://` required except `http://localhost` |
| `SS_TOKEN` | `ss_…` machine token, `pat_…` PAT, or `sso_…` CLI session token |
| `SS_PROJECT` | Project UUID (`ss_…`) or UUID/name (`pat_…`/`sso_…`) |
| `PID` | Alias for `SS_PROJECT` |
| `SS_TIMEOUT` | HTTP timeout in seconds (default 60, clamped 5 to 300) |
| `SS_PAGE_SIZE` | Default page size hint for list pagination (overridden by `--page-size`) |
| `SS_MAX_PAGES` | Safety cap on pages to fetch (default 100) |
| `SS_MAX_ITEMS` | Safety cap on total items (default 50000) |

| Token | Project | Scope |
|-------|---------|-------|
| `ss_…` | UUID only | One project, secrets only. Bypasses ACL and approval |
| `pat_…` | UUID or unique name | All teams and projects of the user, plus org and admin |
| `sso_…` | UUID or unique name | Like `pat_…`, short-lived, from web UI |

Env vars override `~/.config/corvus/config`. If you set `SS_PROJECT` or `PID` in env and config points elsewhere, the CLI prints `warning: SS_PROJECT/PID env overrides config project`. The CLI writes the config file atomically with `0600` (dir `0700`) and warns if existing perms are broader.

> **Security:** `--token` and `--value` appear in `ps aux`. Use `SS_TOKEN` and `--from-file` or `--from-env` instead. The CLI warns when you pass them on the command line.

---

## CLI usage

```text
corvus <command> [resource] [name] [flags]
```

### Project

```bash
corvus project              # show current (resolves UUID to name for PAT)
corvus project ios-app      # switch (PAT resolves name to UUID; machine needs UUID)
```

```
PROJECT  ID
-------  ------------------------------------
ios-app  31a70875-7d6a-40a7-a315-751f8a7ee38f
```

Unset `SS_PROJECT`/`PID` after switch or env will still override the file.

### Secrets: list and get

```bash
corvus get secrets
corvus get secrets -l api -o json
corvus get secrets --no-trunc          # disable 48-char truncation
corvus get secrets --limit 50 --page-size 20   # pagination controls
corvus get secrets --no-paginate       # first page only (disable auto-pagination)
corvus get secret API_KEY
corvus get secret API_KEY -o value     # for scripts
corvus get secret prod/db/password -o value
```

`-l` is forwarded as `q=` and matches key, note, and custom metadata.

```
KEY         KIND   FOLDER    NOTE         EXPIRES
----------  -----  --------  -----------  -------
APNS_KEY    plain  ops/prod  Apple push
```

Bulk listing returns metadata only, never values. The CLI supports hierarchical keys with `/`.

```json
{
  "key": "API_KEY",
  "kind": "plain",
  "note": "rotated",
  "value": "s3cret…",
  "metadata": { "owner": "platform-team", "env": "prod" }
}
```

#### Errors on get (PAT)

| Server `error` | Meaning | What to do |
|----------------|---------|------------|
| `approval_required` | Reveal needs admin approval | `corvus reveal secret NAME --reason "…"` then wait |
| `forbidden` | Per-secret ACL denies reveal | Ask a project admin to `grant` or change ACL mode |
| other 403 | Token/project rights | Check token, project, team membership |

Machine tokens (`ss_…`) skip ACL and approval checks.

```bash
corvus reveal secret API_KEY --reason "debugging prod auth #1234"
corvus get requests
corvus approve <request-id> --minutes 60   # 15/60/240/1440 only
corvus get secret API_KEY -o value
```

### Create / update

```bash
# History-safe (preferred)
printf '%s' "$NEW" | corvus apply secret API_KEY --from-file=-
corvus apply secret API_KEY --from-env=NEW_API_KEY
corvus apply secret API_KEY --from-file=./api.key --note 'rotated in CI'

# Expiry / kind
corvus apply secret API_KEY --kind plain --expires-days 90 --from-env=V
corvus apply secret API_KEY --clear-expires --note 'no expiry'

# Metadata (PAT only)
corvus apply secret API_KEY --meta owner=platform-team --meta env=prod
corvus apply secret API_KEY --delete-meta env

# Access (PAT, project admin)
corvus apply secret API_KEY --access-mode restricted --requires-approval on

# Avoid in interactive shells (lands in history):
# corvus apply secret API_KEY --value 'literal'
```

Aliases: `create secret` and `set secret` both run `apply secret`. Success tables omit the value. `--expires-days` must be a positive integer.

### Delete

```bash
corvus delete secret API_KEY   # soft-delete to trash
```

### SSH hosts (native `ssh <host>`)

Fetches `kind=ssh` private keys out of the box, including the default `corvus-agent` layout `hosts/<hostname>/users/<account>` (`key_prefix = "hosts/"`). One sync writes keys to `~/.config/corvus/keys` (0700 dir, 0600 files, atomic `mkstemp+rename`) and an Include fragment to `~/.ssh/config.d/corvus` with native `ssh <host>` aliases (no wrapper needed for `scp`/`rsync`/`ansible`).

* `hosts/web01/users/deploy` (agent): `ssh web01` (or `ssh web01-deploy`) connects to `web01` as `User deploy` with its own key file `~/.config/corvus/keys/web01-deploy`. Multiple accounts on one host keep both aliases (`Host web01 web01-deploy` + `User deploy` + `HostName web01` for the suffixed alias). First account per host keeps bare hostname alias.
* Any other `kind=ssh` secrets (e.g. legacy `ssh/web01` or `hosts/web01/users/root` with custom prefix): alias + file derived from suffix/basename; `User` only when the agent pattern applies.
* `~/.config/corvus/ssh_hosts` (`host=secret_key` or `host secret_key`) still overrides the derived mapping.

```bash
# Zero-config: agent already wrote hosts/<host>/users/<account> (kind=ssh, value=generated private key)
# On your workstation (same Corvus project):
corvus ssh config install   # once, idempotent — prepends Include ~/.ssh/config.d/corvus to ~/.ssh/config
corvus ssh sync --dry-run   # no values fetched: lists HOST, SECRET_KEY, KEY_PATH (+ USER/HOSTNAME when agent layout)
corvus ssh sync             # fetch values → keys + fragment
ssh web01                   # agent account deploy → User deploy, Host web01 + web01-deploy, HostName web01, IdentityFile web01-deploy
ssh web01-deploy            # explicit account alias
ssh svc_backup@web01        # override User still works (CLI User is default only)
scp report.tgz web01:/tmp/  # rsync/ansible work unchanged
# If multiple accounts share a host, second gets only web01-root etc.:
#   Host web01-root  HostName web01 / User root / IdentityFile web01-root

# Custom prefix / kind-only discovery (also works for manual keys like ssh/web01)
corvus apply secret ssh/web01 --kind ssh --from-file ~/.ssh/web01.key --note "manual"
corvus ssh sync --prefix ssh/          # if you actually store ssh/web01
corvus ssh sync --prefix ""            # empty prefix: kind=ssh only, host = last segment
SS_SSH_USE_RUNTIME=1 corvus ssh sync   # tmpfs keys at $XDG_RUNTIME_DIR/corvus
corvus ssh sync --clean -o json        # remove stale files, json {ok, failed, key_dir, fragment}

# Host alias map (override derived name, e.g. ssh/prod/bastion-key → bastion)
echo "myhost=ssh/web01" >> ~/.config/corvus/ssh_hosts
echo "bastion=ssh/prod/bastion-key" >> ~/.config/corvus/ssh_hosts
corvus ssh sync   # myhost -> ssh/web01

# Lazy fetch for Match exec (TTL 3600s file cache)
corvus ssh _ensure web01                        # no-op if age < 3600s
corvus ssh _ensure web01-deploy --ttl 0 --force # always refresh; also resolves agent aliases/file names
corvus ssh _ensure web01 --prefix hosts/        # explicit prefix variant

# Uninstall
corvus ssh config uninstall   # removes Include line, keeps fragment
```

Discovery: default `--prefix hosts/`; when empty → `kind=ssh` only; otherwise prefix matches plus all `kind=ssh` (so agent + manual coexist). Agent key `*/users/<acct>` → host/alias mapping above; others → alias = suffix after prefix else last segment. `--dry-run` never fetches values. Cache TTL via `SS_SSH_TTL` / `--ttl 0` to always refresh; use `Match exec "corvus ssh _ensure %h"` for on-demand fetch (works for both `web01` and `web01-deploy`).

### Folders

```bash
corvus get folders
corvus create folder ops/prod
corvus delete folder <folder-id>
corvus get secrets             # shows FOLDER column when any secret has folder_path
```

### Teams, members, groups

```bash
corvus get teams
corvus create team NewTeam
corvus delete team NewTeam --yes

corvus get team Platform
corvus get members --team Platform
corvus create member alice@example.com --team Platform --role team-admin
corvus delete member alice@example.com --team Platform

corvus get groups --team Platform
corvus create group admins --team Platform
corvus create group-member alice@example.com --team Platform --group admins
corvus delete group-member alice@example.com --team Platform --group admins

corvus get projects
corvus get project ios-app
corvus create project demo --team Platform
corvus delete project demo --yes

corvus get members                        # current project
corvus get members --project other-app    # override without switching
corvus create member bob@example.com --role project-write
```

Roles: `team-viewer` / `team-member` / `team-admin` / `team-owner`; `project-read` / `project-write` / `project-reveal` / `project-admin`; `service-read` / `service-reveal` / `service-write`.

### Machine tokens (PAT, current project)

```bash
corvus get tokens                              # shows SCOPE
corvus create token ci --role service-write
corvus create token ci --role service-reveal --scope 'API_KEY,prod/*' --expires-days 90
# token (copy now): ss_… scope=API_KEY,prod/*   (printed to stderr once)
corvus delete token <token-uuid>
```

If you omit `--expires-days`, the CLI warns that the token will not expire.

### Secret bindings and project settings (PAT, project admin)

```bash
corvus grant secret API_KEY --to alice@example.com --role secret-reveal
corvus grant secret API_KEY --group <group-id> --role secret-read
corvus unbind secret API_KEY <binding-id>
corvus settings --require-reveal-approval on --default-access-mode restricted
```

### Export and trash (PAT)

```bash
corvus export -o env --yes > .env          # --yes required (audited), auto-paginated
corvus export -o json --yes --limit 100
corvus get trash
corvus restore trash <secret-uuid>
corvus delete trash <secret-uuid> --yes    # permanent purge
corvus restore trash --all
corvus delete trash --all --yes
```

`export` warns with the count. All list endpoints auto-paginate across cursor/`page_token`/`Link: rel="next"`/`has_more`+`page`/`offset`/`total` styles. Caps `SS_MAX_PAGES` (100) and `SS_MAX_ITEMS` (50000) and loop detection stop runaway loops.

### Audit and users (global admin PAT)

```bash
corvus get users -l alice
corvus get audit                            # project audit
corvus get audit --source org               # org / secret / access (admin)
corvus get history API_KEY
corvus transfer team Platform --email new-owner@example.com
```

### Output

| Flag | Use |
|------|-----|
| `table` | Human tables (**default**) |
| `json` | Pretty-printed JSON |
| `value` | Plaintext value only, for `$(…)` |
| `name` | Key/name/id only |
| `wide` | Alias for `table` |

Global flags: `--no-trunc` disables 48-char cell truncation (for example `corvus get secrets --no-trunc`); `--no-paginate`, `--limit N`, `--page-size N` control auto-pagination on all `get` and `export` list endpoints (with `SS_PAGE_SIZE`/`SS_MAX_PAGES`/`SS_MAX_ITEMS` env overrides). Truncation still escapes newlines.

Typo helper: `unknown resource 'secrets': did you mean 'secrets'?` for close matches.

---

## Org access model

Secrets inherit team and project membership. Optional per-secret ACL and reveal approval apply only to PAT and browser users.

| Who | Plaintext get | Bulk list values |
|-----|---------------|------------------|
| Machine `ss_…` | Project token scope | All live keys in project |
| PAT with write/reveal | Subject to ACL and approval | Only secrets caller may reveal |
| PAT meta list (`get secrets`) | No values | Keys, note, and metadata |

Groups can hold team roles, project roles, and secret ACL grants.

---

## Shell scripting cookbook

1. Store `SS_TOKEN` in env/CI secrets or a `0600` config file, never in git.
2. Read with `-o value`.
3. Write with `--from-file=-` or `--from-env=…` (not `--value`).
4. Prefer `set -euo pipefail`.

### Read one secret

```bash
#!/usr/bin/env bash
set -euo pipefail
DB_URL="$(corvus get secret DATABASE_URL -o value)"
psql "$DB_URL" -c 'SELECT 1'
```

### Find by metadata then load

```bash
corvus get secrets -l platform-team
API_KEY="$(corvus get secret API_KEY -o value)"
```

### Read several secrets

```bash
for key in DATABASE_URL API_KEY REDIS_URL; do
  export "$key=$(corvus get secret "$key" -o value)"
done
exec ./my-app
```

### Write from a generator (stdin)

```bash
openssl rand -base64 32 | corvus apply secret APP_SESSION_KEY --from-file=-
```

### Write from env (CI-masked)

```bash
corvus apply secret DATABASE_PASSWORD --from-env=NEW_DB_PASSWORD --note "rotated by CI"
```

### Rotate then deploy

```bash
NEW="$(openssl rand -hex 24)"; export NEW
corvus apply secret API_KEY --from-env=NEW; unset NEW
export API_KEY; API_KEY="$(corvus get secret API_KEY -o value)"
./deploy.sh
```

### Switch project then build (PAT)

```bash
corvus project ios-app
APNS_KEY="$(corvus get secret APNS_KEY -o value)"
./build-ios.sh
```

### CI job

```bash
#!/usr/bin/env bash
set -euo pipefail
: "${SS_URL:?}" "${SS_TOKEN:?}" "${SS_PROJECT:?}"
[[ -n "${BUILD_API_KEY:-}" ]] && corvus apply secret BUILD_API_KEY --from-env=BUILD_API_KEY --note "ci ${CI_COMMIT_SHA:-local}"
DATABASE_URL="$(corvus get secret DATABASE_URL -o value)" ./run-migrations.sh
```

### Fail closed if missing

```bash
if ! val="$(corvus get secret REQUIRED_KEY -o value 2>/dev/null)" || [[ -z "$val" ]]; then
  echo "REQUIRED_KEY missing or unreadable" >&2; exit 1
fi
export REQUIRED_KEY="$val"
```

---

## Shell completion

```bash
# bash
corvus completion bash | sudo tee /etc/bash_completion.d/corvus >/dev/null
# or: eval "$(corvus completion bash)"

# zsh: add to $fpath as _corvus
corvus completion zsh > ~/.zfunc/_corvus && fpath=(~/.zfunc $fpath)

# fish
corvus completion fish > ~/.config/fish/completions/corvus.fish
```

---

## Security notes

- The CLI writes config atomically (temp file, `chmod 0600`, then rename). It creates the directory as `0700`. It warns if existing perms are broader.
- `SS_URL` must use `https://` except `http://localhost`, `127.0.0.1`, or `::1`. The CLI exits on `login` and warns elsewhere.
- The CLI clamps `SS_TIMEOUT` to 5 to 300 seconds.
- `corvus export` dumps all plaintext values and requires `--yes`. It warns with the count on `stderr`.
- If the server paginates (`next`/`has_more`/`total`), the CLI prints `warning: ... truncated` so you notice missing items.
- `_mgmt_proj_api` checks for PAT on the client. Machine tokens cannot hit manage endpoints.
- Machine-token projects must be UUIDs.

---

## Project structure

```
corvus              # thin shim (env python3, re-exports for tests)
corvus_cli/
  __init__.py       # single version source (importlib.metadata)
  constants.py      # env names, paths, regexes, role tuples, USAGE, completions
  config.py         # creds, atomic 0600 writes, URL/timeout/role validation, helpers
  api.py            # _http / _api / _proj_api / _mgmt_api / _mgmt_proj_api / _admin_api
  output.py         # print_table / print_json / trunc / emit plus pagination warnings
  parser.py         # argparse wiring, --no-trunc, --expires-days, completion
  cli.py            # main() dispatcher
  commands/
    auth.py         # login / project
    secrets.py      # apply (secret)
    management.py   # get / create / delete / restore / transfer
    access.py       # reveal / approve / deny / grant / unbind / export / settings
    ssh.py          # ssh sync / config / _ensure (Include fragment + key dir, 0700/0600)
corvus.1            # man page
tests/              # pytest suite (no network)
rpm/corvus-cli.spec
pyproject.toml      # single version source
```

Every public function has a docstring with Description, Inputs, Outputs, and Example.

---

## Quick reference

| Task | Command |
|------|---------|
| Usage | `corvus` |
| Login | `corvus login --url … --token … [--project …]` |
| Switch project | `corvus project [name\|uuid]` |
| List / get secrets | `get secrets` / `get secret KEY` |
| Script value | `get secret KEY -o value` |
| Apply secret | `apply secret KEY --from-env=V` |
| Teams / projects | `get teams` / `create project N --team T` |
| Members | `create member email --team T --role team-member` |
| Tokens | `create token NAME --role service-write [--scope 'K1,prod/*'] [--expires-days 90]` |
| Bindings | `grant secret K --to EMAIL` / `unbind secret K <binding>` |
| Settings | `settings --require-reveal-approval on` |
| Export | `export -o env --yes` |
| Groups | `get groups --team T` / `create group NAME --team T` |
| Trash | `get trash` / `restore trash ID` |
| SSH hosts | `ssh config install` / `ssh sync [--dry-run] [--clean]` / `ssh _ensure HOST` |
| Admin | `get users` / `get audit --source access` |
| Completion | `completion bash\|zsh\|fish` |

---

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[dev]"
pytest -q
ruff check corvus_cli corvus tests/   # or: make check
```

`pyproject.toml` sets up pytest and ruff (mirrors the `corvus` server). Tests run offline. The suite mocks HTTP.

Server API: `corvus` repo, `docs/dev/api.md` (secret CRUD on `/eso/v1`, org and admin on `/api/v1/manage`, PAT only).

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `missing url, token` | No env or config | `corvus login …` or export `SS_URL`/`SS_TOKEN` |
| `SS_PROJECT/PID env overrides config` | Both set | `unset SS_PROJECT` or align them |
| `refusing http://` | Non-localhost HTTP | Use `https://` |
| `token must start with ss_…` | Bad prefix | Check copy, no trailing newline |
| `machine tokens require project UUID` | Name given to `ss_…` | Use UUID |
| `export … --yes` | Gate | `corvus export -o env --yes` |
| `warning: … truncated` | Server paginated | Narrow with `-l` / `q=` |

---

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
