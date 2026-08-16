# secretserver-cli

kubectl-style CLI for [Sigaint Secret Server](https://git.sigaint.au/Sigaint/secretserver) (`/eso/v1`).

Python 3 stdlib only · RHEL 9+

```bash
sudo install -m 0755 secretserver /usr/bin/secretserver
# or: make rpm && sudo dnf install -y dist/secretserver-cli-*.noarch.rpm
```

---

## Credentials

Env **or** `~/.config/secretserver/config` (`0600`). **Env wins.**

| Env | Meaning |
|-----|---------|
| `SS_URL` | Base URL (no trailing slash) |
| `SS_TOKEN` | `ss_…` machine token **or** `pat_…` PAT |
| `SS_PROJECT` | Project UUID (`ss_…`) or UUID/name (`pat_…`) |
| `PID` | Alias for `SS_PROJECT` |
| `SS_TIMEOUT` | HTTP timeout in seconds (default 60, min 5) |

| Token | Project |
|-------|---------|
| `ss_…` | UUID only |
| `pat_…` | UUID or unique **name** |

```bash
# Machine token
secretserver login \
  --url https://secrets.example.com \
  --token ss_… \
  --project 31a70875-7d6a-40a7-a315-751f8a7ee38f

# PAT (name ok)
secretserver login \
  --url https://secrets.example.com \
  --token pat_… \
  --project ios-app

# Env-only (CI / no config file)
export SS_URL=https://secrets.example.com
export SS_TOKEN=ss_…   # do not commit
export SS_PROJECT=<uuid>
```

`configure` = `login`.

> **Security:** `--token` and `--value` are visible in process listings (`ps aux`).
> Prefer `SS_TOKEN` env var and `--from-file` / `--from-env` for secret values.
> The CLI prints a warning when `--token` or `--value` is used.

---

## CLI usage

```text
secretserver <command> [resource] [name] [flags]
```

No args → usage summary.

### Project

```bash
secretserver project              # show current
secretserver project ios-app      # switch (PAT: name; machine: UUID)
secretserver project 31a70875-7d6a-40a7-a315-751f8a7ee38f
```

Example:

```text
PROJECT   ID
--------  ------------------------------------
ios-app   31a70875-7d6a-40a7-a315-751f8a7ee38f
```

Unset `SS_PROJECT`/`PID` after switch, or env still overrides the config file.

### List secrets (table by default)

```bash
secretserver get secrets
secretserver get secrets -l api      # filter key, note, or custom metadata
secretserver get secrets -o json
```

`-l` / selector is passed as `q=` to the API and matches **key**, **note**, and
**custom metadata** keys/values (same as the UI search).

Example:

```text
KEY         KIND   NOTE               EXPIRES
----------  -----  -----------------  -------
APNS_KEY    plain  Apple push
SENTRY_DSN  plain  Sentry
```

Values are **not** listed (metadata-only list). Hierarchical keys with `/`
(e.g. `prod/db/password`) are supported on get/reveal/delete/apply.

### Get one secret

```bash
secretserver get secret API_KEY              # table (default)
secretserver get secret API_KEY -o value     # scripts — value only
secretserver get secret API_KEY -o json      # full object incl. metadata
secretserver get secret API_KEY -o name
secretserver get secret prod/db/password -o value   # hierarchical key
```

Example (`-o json` fields of interest):

```json
{
  "key": "API_KEY",
  "kind": "plain",
  "note": "rotated",
  "value": "s3cret…",
  "created_at": "2026-01-15T12:00:00+00:00",
  "updated_at": "2026-03-01T09:30:00+00:00",
  "last_accessed_at": "2026-08-09T10:00:00+00:00",
  "last_accessed_by": "alice@example.com",
  "metadata": {
    "owner": "platform-team",
    "env": "prod"
  }
}
```

`metadata` is the map of custom fields from the UI **Metadata** tab (labels
only — not secret values). Successful PAT get updates last-accessed timestamps.

#### Errors on get (PAT)

| Server `error` | Meaning | What to do |
|----------------|---------|------------|
| `approval_required` | Reveal needs admin approval | `secretserver reveal secret NAME --reason "…"` then wait for approve |
| `forbidden` | Per-secret ACL denies reveal | Ask a project admin to grant reveal/write or change ACL mode |
| other 403 | Token/project rights | Check token, project, team membership |

Machine tokens (`ss_…`) are **not** gated by human ACL or reveal-approval.

```bash
# Request access (PAT)
secretserver reveal secret API_KEY --reason "debugging prod auth #1234"

# Approver (project admin / team owner, PAT)
secretserver get requests
secretserver approve <request-id> --minutes 15   # 15, 60, 240, or 1440 only
# secretserver deny <request-id>

# Then fetch value
secretserver get secret API_KEY -o value
```

### Create / update

```bash
# History-safe (preferred)
printf '%s' "$NEW" | secretserver apply secret API_KEY --from-file=-
secretserver apply secret API_KEY --from-env=NEW_API_KEY
secretserver apply secret API_KEY --from-file=./api.key

# Note / kind / expiry (not custom metadata map)
secretserver apply secret API_KEY --note 'rotated in CI'
secretserver apply secret API_KEY --kind plain --expires-days 90 --from-env=V

# Avoid in interactive shells (lands in history):
# secretserver apply secret API_KEY --value 'literal'
```

Aliases: `create`, `set` → `apply`. Success table **omits** the secret value.

Custom metadata fields (`owner=platform-team`, etc.) are set with `apply
--meta`/`--delete-meta` (PAT only) and searched/read via `get secrets -l` /
`get secret -o json`.

```bash
secretserver apply secret API_KEY --meta owner=platform-team --meta env=prod --note 'metadata set from CLI'
secretserver apply secret API_KEY --delete-meta env
```

### Delete

```bash
secretserver delete secret API_KEY
```

Soft-delete (restorable in UI trash).

### Projects (PAT only)

```bash
secretserver get projects
secretserver get projects -l Mobile
```

Example:

```text
NAME         TEAM      ID
-----------  --------  ------------------------------------
ios-app      Mobile    31a70875-…
android-app  Mobile    c29f6ab5-…
```

### Output (`-o`)

| Flag | Use |
|------|-----|
| `table` | Human tables (**default**) |
| `json` | Pretty JSON |
| `value` | Plaintext only → scripts / `$(…)` |
| `name` | Resource name only |
| `wide` | Same as `table` |

---

## Org access model (app + CLI)

Secrets inherit **team / project** membership. Optional **per-secret ACL**
(Permissions tab) and **reveal approval** apply to PAT/browser users.
**Groups** (Team → Groups) can hold team roles, project roles, and secret ACL
grants. Full guide: app repo `docs/admin/rbac.md`.

| Who | Plaintext get | Bulk list values |
|-----|---------------|------------------|
| Machine `ss_…` | Project token scope | All live keys in project |
| PAT with write/reveal | Subject to ACL + approval | Only secrets caller may reveal |
| PAT meta list (`get secrets`) | No values | Keys + note + metadata fields |

---

## Shell scripts (keep secrets out of history)

1. Store `SS_TOKEN` in env/CI secrets or `0600` config — never in git.
2. Read with `-o value`.
3. Write with `--from-file=-` or `--from-env=…` (not `--value`).
4. Prefer `set -euo pipefail`.

### Read one secret into env

```bash
#!/usr/bin/env bash
set -euo pipefail

export DATABASE_URL
DATABASE_URL="$(secretserver get secret DATABASE_URL -o value)"
psql "$DATABASE_URL" -c 'SELECT 1'
```

### Find secrets by custom metadata then load one

```bash
#!/usr/bin/env bash
set -euo pipefail

# Lists keys whose key/note/metadata match "platform-team"
secretserver get secrets -l platform-team
export API_KEY
API_KEY="$(secretserver get secret API_KEY -o value)"
```

### Read several secrets

```bash
#!/usr/bin/env bash
set -euo pipefail

for key in DATABASE_URL API_KEY REDIS_URL; do
  export "$key=$(secretserver get secret "$key" -o value)"
done
exec ./my-app
```

### Write from a generator (stdin)

```bash
#!/usr/bin/env bash
set -euo pipefail

openssl rand -base64 32 | secretserver apply secret APP_SESSION_KEY --from-file=-
```

### Write from env (CI-masked vars)

```bash
#!/usr/bin/env bash
set -euo pipefail

secretserver apply secret DATABASE_PASSWORD \
  --from-env=NEW_DB_PASSWORD \
  --note "rotated by CI"
```

### Rotate then load for deploy

```bash
#!/usr/bin/env bash
set -euo pipefail

NEW="$(openssl rand -hex 24)"
export NEW
secretserver apply secret API_KEY --from-env=NEW
unset NEW

export API_KEY
API_KEY="$(secretserver get secret API_KEY -o value)"
./deploy.sh
```

### Switch project then run (PAT)

```bash
#!/usr/bin/env bash
set -euo pipefail

secretserver project ios-app
export APNS_KEY
APNS_KEY="$(secretserver get secret APNS_KEY -o value)"
./build-ios.sh
```

### CI job

```bash
#!/usr/bin/env bash
set -euo pipefail

# Injected by CI (not shell history / not in repo):
#   SS_URL, SS_TOKEN, SS_PROJECT
: "${SS_URL:?}" "${SS_TOKEN:?}" "${SS_PROJECT:?}"

if [[ -n "${BUILD_API_KEY:-}" ]]; then
  secretserver apply secret BUILD_API_KEY \
    --from-env=BUILD_API_KEY \
    --note "ci ${CI_COMMIT_SHA:-local}"
fi

export DATABASE_URL
DATABASE_URL="$(secretserver get secret DATABASE_URL -o value)"
./run-migrations.sh
```

### Fail closed if missing

```bash
#!/usr/bin/env bash
set -euo pipefail

if ! val="$(secretserver get secret REQUIRED_KEY -o value 2>/dev/null)" || [[ -z "$val" ]]; then
  echo "REQUIRED_KEY missing or unreadable" >&2
  exit 1
fi
export REQUIRED_KEY="$val"
```

---

## Org & admin (PAT required)

Teams, projects, members, tokens, trash, users, and audit need a **`pat_…`** token.
Machine tokens (`ss_…`) only manage secrets in one project.

```bash
# Teams
secretserver get teams
secretserver get team Platform
secretserver create team NewTeam
secretserver delete team NewTeam
secretserver get members --team Platform
secretserver create member bob@example.com --team Platform --role team-member
secretserver delete member bob@example.com --team Platform
secretserver transfer team Platform --email alice@example.com

# Projects
secretserver get projects
secretserver get project ios-app
secretserver create project my-app --team Platform
secretserver delete project my-app
secretserver get members              # current project
secretserver create member dave@example.com --role project-write

# Machine tokens (current project)
secretserver get tokens                         # shows key allow-list scope
secretserver create token ci --role service-write               # prints ss_… once
secretserver create token ci --role service-reveal --scope 'API_KEY,prod/*'
secretserver delete token <token-uuid>

# Secret access & metadata (PAT, project admin for access settings)
secretserver apply secret API_KEY --access-mode restricted --requires-approval on
secretserver grant secret API_KEY --to bob@example.com --role secret-reveal
secretserver unbind secret API_KEY <binding-id>
secretserver apply secret API_KEY --meta owner=platform-team --delete-meta env

# Project settings (PAT, project admin)
secretserver settings --require-reveal-approval on --default-access-mode restricted

# Export / bulk trash (PAT)
secretserver export -o env          # secrets as KEY=VALUE (json|csv|table|value)
secretserver restore trash --all    # bulk restore
secretserver delete trash --all     # bulk purge

# Groups (PAT)
secretserver get groups --team Platform
secretserver create group admins --team Platform
secretserver create group-member bob@example.com --team Platform --group admins
secretserver delete group-member bob@example.com --team Platform --group admins
secretserver delete group admins --team Platform

# Trash
secretserver get trash
secretserver restore trash <secret-uuid>
secretserver delete trash <secret-uuid>            # permanent purge

# Audit / users (global admin PAT)
secretserver get users
secretserver get users -l alice
secretserver get audit                            # project secret audit
secretserver get audit --source org               # org audit (admin)
secretserver get audit --source secret            # global secret audit (admin)
secretserver get audit --source access            # access review (admin)

# Secret history
secretserver get history API_KEY
```

Server settings (SMTP, LDAP, OIDC, banners, etc.) are **not** exposed in the CLI.

## Quick reference

| Task | Command |
|------|---------|
| Usage | `secretserver` |
| Login | `secretserver login --url … --token … --project …` |
| Switch project | `secretserver project [name\|uuid]` |
| List / get secrets | `get secrets` / `get secret KEY` |
| Script value | `get secret KEY -o value` |
| Apply secret | `apply secret KEY --from-env=V` |
| Teams / projects | `get teams` / `create project N --team T` |
| Members | `create member email --team T --role team-member` |
| Tokens | `create token NAME --role service-write [--scope 'K1,prod/*']` |
| Secret access / bindings | `apply secret K --access-mode …` / `grant secret K --to EMAIL` |
| Project settings | `settings --require-reveal-approval on` |
| Export | `export -o env\|json\|csv` |
| Groups | `get groups --team T` / `create group NAME --team T` |
| Trash | `get trash` / `restore trash ID` |
| Admin users / audit | `get users` / `get audit --source access` |

Team roles: `team-viewer`, `team-member`, `team-admin`, `team-owner`. Project
roles: `project-read`, `project-write`, `project-admin`. Machine roles:
`service-read`, `service-reveal`, `service-write`.

Server API: `secretserver` repo → `docs/dev/api.md` (secret CRUD uses `/eso/v1`,
org/admin uses `/api/v1/manage`, PAT only).
