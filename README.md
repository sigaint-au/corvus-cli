# secretserver-cli

kubectl-style CLI for [Sigaint Secret Server](https://git.sigaint.au/Sigaint/secretserver) (`/eso/v1`).

Python 3 stdlib only. RHEL 9+.

```bash
sudo install -m 0755 secretserver /usr/bin/secretserver
# or: make rpm && sudo dnf install -y dist/secretserver-cli-*.noarch.rpm
```

## Login

Credentials: env **or** `~/.config/secretserver/config` (mode `0600`). Env wins.

| Env | Meaning |
|-----|---------|
| `SS_URL` | Server base URL (no trailing slash) |
| `SS_TOKEN` | `ss_…` machine token **or** `pat_…` PAT |
| `SS_PROJECT` | Project UUID (`ss_…`) or UUID/name (`pat_…`) |
| `PID` | Alias for `SS_PROJECT` |

```bash
# Machine token (project UUID required)
secretserver login \
  --url https://secrets.example.com \
  --token ss_… \
  --project 31a70875-7d6a-40a7-a315-751f8a7ee38f

# PAT (project name ok)
secretserver login \
  --url https://secrets.example.com \
  --token pat_… \
  --project ios-app

# Or only env (no config file)
export SS_URL=https://secrets.example.com
export SS_TOKEN=ss_…          # keep this out of scripts committed to git
export SS_PROJECT=<uuid>
```

`configure` is an alias for `login`.

## Everyday usage

```bash
secretserver                          # print usage

# List (metadata only — no secret values)
secretserver get secrets
secretserver get secrets -l api       # filter by key/note substring
secretserver get secrets -o wide      # table

# One secret
secretserver get secret API_KEY                 # JSON (includes value)
secretserver get secret API_KEY -o value        # value only
secretserver get secret API_KEY -o name         # key name only

# Create / update
secretserver apply secret API_KEY --from-file=./api.key
secretserver apply secret API_KEY --from-env=API_KEY_VALUE
secretserver apply secret API_KEY --note 'rotated'          # metadata only
secretserver apply secret API_KEY --kind plain --expires-days 90 --from-env=V

# Delete (soft-delete → trash in UI)
secretserver delete secret API_KEY

# Projects (PAT only)
secretserver get projects
secretserver get projects -l Mobile
```

Aliases: `create` / `set` → `apply`.

### Output (`-o`)

| Value | Meaning |
|-------|---------|
| `json` | Pretty JSON (default) |
| `value` | Plaintext secret only (for scripts) |
| `name` | Resource name only |
| `wide` | Table for `get secrets` |

`apply` success JSON **omits** the secret value so it is not echoed to the terminal.

---

## Shell scripts (do not put secrets in history)

**Rules of thumb**

1. Prefer env vars or a restricted config file for `SS_TOKEN` — never hard-code tokens in git.
2. Pull secrets with `-o value` into a variable or file with tight permissions.
3. Push secrets with `--from-file=-` or `--from-env=…` so the value is never on argv (and not stored in bash history).
4. Avoid `--value 'literal'` in interactive shells.

### Read a secret into an env var

```bash
#!/usr/bin/env bash
set -euo pipefail

# Assumes login already done, or SS_* exported in the job environment.
export DATABASE_URL
DATABASE_URL="$(secretserver get secret DATABASE_URL -o value)"

# use it
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

### Write a secret from a generator (no value on command line)

```bash
#!/usr/bin/env bash
set -euo pipefail

# openssl output never appears as a shell word → not in history
openssl rand -base64 32 | secretserver apply secret APP_SESSION_KEY --from-file=-
```

### Write a secret already in the environment

```bash
#!/usr/bin/env bash
set -euo pipefail

# e.g. CI injects NEW_DB_PASSWORD as a masked env var
secretserver apply secret DATABASE_PASSWORD --from-env=NEW_DB_PASSWORD --note "rotated by CI"
```

### Rotate and export without echoing

```bash
#!/usr/bin/env bash
set -euo pipefail

NEW="$(openssl rand -hex 24)"
export NEW
secretserver apply secret API_KEY --from-env=NEW
unset NEW

# app reads current value
export API_KEY
API_KEY="$(secretserver get secret API_KEY -o value)"
./deploy.sh
```

### CI example (GitLab / GitHub Actions style)

```bash
#!/usr/bin/env bash
set -euo pipefail

# CI secrets store (not shell history):
#   SS_URL, SS_TOKEN (ss_… write or pat_…), SS_PROJECT
: "${SS_URL:?}" "${SS_TOKEN:?}" "${SS_PROJECT:?}"

# optional: write build artifact secret
if [[ -n "${BUILD_API_KEY:-}" ]]; then
  secretserver apply secret BUILD_API_KEY --from-env=BUILD_API_KEY --note "ci ${CI_COMMIT_SHA:-local}"
fi

# inject runtime config for the next step
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

## Quick reference

| Task | Command |
|------|---------|
| Usage | `secretserver` |
| Login | `secretserver login --url … --token … --project …` |
| List keys | `secretserver get secrets` |
| Get value (script) | `secretserver get secret KEY -o value` |
| Set from stdin | `… \| secretserver apply secret KEY --from-file=-` |
| Set from env | `secretserver apply secret KEY --from-env=VAR` |
| Delete | `secretserver delete secret KEY` |
| List projects (PAT) | `secretserver get projects` |

API docs: sibling `secretserver` repo → `docs/api.md`.
