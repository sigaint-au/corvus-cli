# corvus-cli

[![CI](https://img.shields.io/badge/CI-Forgejo-blue)](#development)
[![Python](https://img.shields.io/badge/python-%3E%3D3.9-blue)](#requirements)
[![License](https://img.shields.io/badge/license-AGPL--3.0--or--later-green)](LICENSE)
[![RPM](https://img.shields.io/badge/RPM-noarch-red)](#installation)

Command-line client for [Corvus](https://git.sigaint.au/Sigaint/corvus) — secrets, teams, projects, and admin over `/eso/v1` and `/api/v1/manage`. Zero runtime dependencies, stdlib only. Built for RHEL 9+.

```
corvus login --url https://secrets.example.com --token pat_… --project ios-app
corvus get secrets
corvus get secret API_KEY -o value
printf '%s' "$NEW" | corvus apply secret API_KEY --from-file=-
```

`corvus` / `corvus --help` → usage. `corvus --version` → version. `man corvus` → full reference.

---

## Features

- **Secrets** — list (metadata-only), get, apply, delete, reveal flow, history, folders, export
- **Access control** — per-secret ACL mode, reveal approval, bindings (`grant`/`unbind`), project settings
- **Org** — teams, projects, members, groups, machine tokens (scoped), trash
- **Admin** — users, audit (`project`/`org`/`secret`/`access`), access requests
- **UX** — tables/JSON/value/name, `--no-trunc`, did-you-mean hints, pagination warnings, env-override warning
- **Safety** — atomic `0600` config writes, HTTPS enforcement, clamped timeout, `export --yes` gate, perm warnings
- **Shell** — completion for `bash`/`zsh`/`fish`, paste-safe `sso_…` login, stdin/env value ingestion

---

## Requirements

- Python ≥ 3.9
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

Version is single-sourced from `pyproject.toml`.

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

# CI — env only, no config file
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
| `SS_TIMEOUT` | HTTP timeout seconds (default 60, clamped 5–300) |

| Token | Project | Scope |
|-------|---------|-------|
| `ss_…` | UUID only | One project, secrets only. Bypasses ACL + approval |
| `pat_…` | UUID or unique name | All teams/projects of the user + org/admin |
| `sso_…` | UUID or unique name | Like `pat_…`, short-lived, from web UI |

Precedence: **env wins over `~/.config/corvus/config`**. When both set differently the CLI warns `SS_PROJECT/PID env overrides config project`. The config file is written atomically with `0600` (dir `0700`); broad perms emit a warning.

> **Security:** `--token` and `--value` are visible in `ps aux`. Prefer `SS_TOKEN` and `--from-file`/`--from-env`. The CLI warns when they are used.

---

## CLI usage

```text
corvus <command> [resource] [name] [flags]
```

### Project

```bash
corvus project              # show current (resolves UUID→name for PAT)
corvus project ios-app      # switch (PAT resolves name→UUID; machine needs UUID)
```

```
PROJECT  ID
-------  ------------------------------------
ios-app  31a70875-7d6a-40a7-a315-751f8a7ee38f
```

Unset `SS_PROJECT`/`PID` after switch or env still overrides the file.

### Secrets — list and get

```bash
corvus get secrets
corvus get secrets -l api -o json
corvus get secrets --no-trunc          # disable 48-char truncation
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

Values are never listed in bulk — metadata only. Hierarchical keys with `/` are supported.

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

Machine tokens (`ss_…`) are not gated by ACL or approval.

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

Aliases: `create secret` / `set secret` → `apply secret`. Success tables omit the value. `--expires-days` must be a positive integer.

### Delete

```bash
corvus delete secret API_KEY   # soft-delete → trash
```

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

Omitting `--expires-days` warns that the token will not expire.

### Secret bindings & project settings (PAT, project admin)

```bash
corvus grant secret API_KEY --to alice@example.com --role secret-reveal
corvus grant secret API_KEY --group <group-id> --role secret-read
corvus unbind secret API_KEY <binding-id>
corvus settings --require-reveal-approval on --default-access-mode restricted
```

### Export & trash (PAT)

```bash
corvus export -o env --yes > .env          # --yes required (audited)
corvus export -o json --yes
corvus get trash
corvus restore trash <secret-uuid>
corvus delete trash <secret-uuid> --yes    # permanent purge
corvus restore trash --all
corvus delete trash --all --yes
```

`export` warns with the count and when the server paginates.

### Audit & users (global admin PAT)

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
| `value` | Plaintext value only → `$(…)` |
| `name` | Key/name/id only |
| `wide` | Alias for `table` |

Global flag: `--no-trunc` disables 48-char cell truncation (e.g. `corvus get secrets --no-trunc`). Truncation still escapes newlines.

Typo helper: `unknown resource 'secrets' — did you mean 'secrets'?` for close matches.

---

## Org access model

Secrets inherit **team / project** membership. Optional **per-secret ACL** and **reveal approval** apply only to PAT/browser users.

| Who | Plaintext get | Bulk list values |
|-----|---------------|------------------|
| Machine `ss_…` | Project token scope | All live keys in project |
| PAT with write/reveal | Subject to ACL + approval | Only secrets caller may reveal |
| PAT meta list (`get secrets`) | No values | Keys + note + metadata |

**Groups** can hold team roles, project roles, and secret ACL grants.

---

## Shell scripting cookbook

1. Store `SS_TOKEN` in env/CI secrets or `0600` config, never in git.
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

# zsh — add to $fpath as _corvus
corvus completion zsh > ~/.zfunc/_corvus && fpath=(~/.zfunc $fpath)

# fish
corvus completion fish > ~/.config/fish/completions/corvus.fish
```

---

## Security notes

- Config is written atomically via temp-file + `chmod 0600` + rename; dir is `0700`. Existing broad perms warn.
- `SS_URL` must be `https://` except `http://localhost`/`127.0.0.1`/`::1`; otherwise the CLI exits (login) or warns.
- `SS_TIMEOUT` is clamped to 5–300s.
- `corvus export` dumps all plaintext values and requires `--yes`; the count is warned to `stderr`.
- Server pagination (`next`/`has_more`/`total`) triggers `warning: … truncated` so missing items are not silent.
- `_mgmt_proj_api` is PAT-gated client-side; machine tokens cannot hit manage endpoints.
- Machine-token project values must be UUIDs.

---

## Project structure

```
corvus              # thin shim (env python3, re-exports for tests)
corvus_cli/
  __init__.py       # single version source (importlib.metadata)
  constants.py      # env names, paths, regexes, role tuples, USAGE, completions
  config.py         # creds, atomic 0600 writes, URL/timeout/role validation, helpers
  api.py            # _http / _api / _proj_api / _mgmt_api / _mgmt_proj_api / _admin_api
  output.py         # print_table / print_json / trunc / emit + pagination warnings
  parser.py         # argparse wiring, --no-trunc, --expires-days, completion
  cli.py            # main() dispatcher
  commands/
    auth.py         # login / project
    secrets.py      # apply (secret)
    management.py   # get / create / delete / restore / transfer
    access.py       # reveal / approve / deny / grant / unbind / export / settings
corvus.1            # man page
tests/              # pytest suite (no network)
rpm/corvus-cli.spec
pyproject.toml      # single version source
```

Every public function has a docstring with **Description**, **Inputs**, **Outputs**, and **Example**.

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

`pyproject.toml` configures **pytest** and **ruff** (mirroring the `corvus` server). Tests are offline; HTTP is mocked.

Server API: `corvus` repo → `docs/dev/api.md` (secret CRUD on `/eso/v1`, org/admin on `/api/v1/manage`, PAT only).

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
