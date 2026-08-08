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
secretserver get secrets -l api      # filter key/note
secretserver get secrets -o json
```

Example:

```text
KEY         KIND   NOTE               EXPIRES
----------  -----  -----------------  -------
APNS_KEY    plain  Apple push
SENTRY_DSN  plain  Sentry
```

Values are **not** listed (metadata only).

### Get one secret

```bash
secretserver get secret API_KEY              # table (default)
secretserver get secret API_KEY -o value     # scripts — value only
secretserver get secret API_KEY -o json
secretserver get secret API_KEY -o name
```

Example (table):

```text
FIELD        VALUE
-----------  ------------------------------------
key          API_KEY
kind         plain
note         rotated
id           a1b2c3d4-…
value        s3cret…          # truncated in table
```

### Create / update

```bash
# History-safe (preferred)
printf '%s' "$NEW" | secretserver apply secret API_KEY --from-file=-
secretserver apply secret API_KEY --from-env=NEW_API_KEY
secretserver apply secret API_KEY --from-file=./api.key

# Metadata only
secretserver apply secret API_KEY --note 'rotated in CI'
secretserver apply secret API_KEY --kind plain --expires-days 90 --from-env=V

# Avoid in interactive shells (lands in history):
# secretserver apply secret API_KEY --value 'literal'
```

Aliases: `create`, `set` → `apply`. Success table **omits** the secret value.

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
secretserver create member bob@example.com --team Platform --role member
secretserver delete member bob@example.com --team Platform
secretserver transfer team Platform --email alice@example.com

# Projects
secretserver get projects
secretserver get project ios-app
secretserver create project my-app --team Platform
secretserver delete project my-app
secretserver get members              # current project
secretserver create member dave@example.com --role write

# Machine tokens (current project)
secretserver get tokens
secretserver create token ci --role write          # prints ss_… once on stderr
secretserver delete token <token-uuid>

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
| Members | `create member email --team T --role member` |
| Tokens | `create token NAME --role write` |
| Trash | `get trash` / `restore trash ID` |
| Admin users / audit | `get users` / `get audit --source access` |

Server API: `secretserver` repo → `docs/api.md` + `/eso/v1` management routes.
