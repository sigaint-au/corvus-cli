# corvus-cli

[![CI](https://img.shields.io/badge/CI-Forgejo-blue)](#development)
[![Python](https://img.shields.io/badge/python-%3E%3D3.9-blue)](#requirements)
[![License](https://img.shields.io/badge/license-AGPL--3.0--or--later-green)](LICENSE)
[![RPM](https://img.shields.io/badge/RPM-noarch-red)](#installation)

Command-line client for [Corvus](https://git.sigaint.au/Sigaint/corvus). It talks to `/eso/v1` and `/api/v1/manage` to manage secrets, teams, projects, and admin. No runtime dependencies beyond stdlib. Built for RHEL 9+.

```
corvus login --url https://secrets.example.com --token pat_… --project ios-app
corvus secret list
corvus secret get API_KEY -o value
printf '%s' "$NEW" | corvus secret set API_KEY --from-file=-
```

`corvus` or `corvus --help` prints usage. `corvus --version` prints the version. `man corvus` has the full reference. Commands are noun-first (`corvus secret list`); verb-first aliases (`corvus get secrets`, `corvus apply secret`, …) still work.

---

## Features

- Secrets: list metadata, get one secret, apply, delete, request reveal, check history, use folders, and export. Bulk listing never returns values.
- SSH hosts: `corvus ssh setup` then native `ssh <host>`. Private keys load into `ssh-agent` on first connect (`ssh-add -t TTL`); only `*.pub` is written locally.
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
- `corvus ssh`: OpenSSH client + `ssh-agent` (`openssh-clients` on Fedora / RHEL)

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
corvus secret list
corvus secret get API_KEY -o value

# PAT / sso session (human, all teams/projects, name resolution)
corvus login --url https://secrets.example.com --token pat_…
corvus project use ios-app
corvus secret list -l platform-team -o json

# Web UI "Copy login command" paste
corvus login --url https://secrets.example.com --token sso_…

# CI: env only, no config file
export SS_URL=https://secrets.example.com
export SS_TOKEN=ss_…    # never commit
export SS_PROJECT=31a70875-7d6a-40a7-a315-751f8a7ee38f
corvus secret list
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
corvus <noun> <verb> [name] [flags]
```

Verb-first forms (`get secrets`, `apply secret`, `create team`, …) still work as aliases.

### Project

```bash
corvus project              # show current (resolves UUID to name for PAT)
corvus project use ios-app  # switch (PAT resolves name to UUID; machine needs UUID)
```

```
PROJECT  ID
-------  ------------------------------------
ios-app  31a70875-7d6a-40a7-a315-751f8a7ee38f
```

Unset `SS_PROJECT`/`PID` after switch or env will still override the file.

### Secrets: list and get

```bash
corvus secret list
corvus secret list -l api -o json
corvus secret list --no-trunc          # disable 48-char truncation (notes etc.)
corvus secret list --limit 50 --page-size 20   # pagination controls
corvus secret list --no-paginate       # first page only (disable auto-pagination)
corvus secret get API_KEY
corvus secret get API_KEY -o value     # for scripts
corvus secret get prod/db/password -o value
```

KEY and VALUE columns never truncate, so long keys and ssh private keys render in full. Tables use color (bold headers) on a TTY; pipe-safe plain text otherwise. `NO_COLOR=1` disables color.

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
corvus secret reveal API_KEY --reason "debugging prod auth #1234"
corvus request list
corvus request approve <request-id> --minutes 60   # 15/60/240/1440 only
corvus secret get API_KEY -o value
```

### Create / update

```bash
# History-safe (preferred)
printf '%s' "$NEW" | corvus secret set API_KEY --from-file=-
corvus secret set API_KEY --from-env=NEW_API_KEY
corvus secret set API_KEY --from-file=./api.key --note 'rotated in CI'

# Expiry / kind
corvus secret set API_KEY --kind plain --expires-days 90 --from-env=V
corvus secret set API_KEY --clear-expires --note 'no expiry'

# Metadata (PAT only)
corvus secret set API_KEY --meta owner=platform-team --meta env=prod
corvus secret set API_KEY --delete-meta env

# Access (PAT, project admin)
corvus secret set API_KEY --access-mode restricted --requires-approval on

# Avoid in interactive shells (lands in history):
# corvus secret set API_KEY --value 'literal'
```

Aliases: `apply secret`, `create secret`, and `set secret` all run `secret set`. Success tables omit the value. `--expires-days` must be a positive integer.

### Delete

```bash
corvus secret delete API_KEY   # soft-delete to trash
```

### SSH hosts (native `ssh <host>`)

Discovers `kind=ssh` private keys, including the default `corvus-agent` layout `hosts/<hostname>/users/<account>`. One command wires your SSH config; after that, use normal `ssh` / `scp` / `rsync` / `ansible`. Private keys are **not** left on disk: first `ssh <host>` loads that key into ssh-agent (`ssh-add -t`, default 300s / `$SS_SSH_TTL`) and writes only the `.pub`.

Start `ssh-agent` first ([Fedora / RHEL](#ssh-agent-on-fedora--rhel)), then:

```bash
corvus ssh setup             # once: Include + host list
ssh web01                    # first connect fetches that key into ssh-agent
corvus ssh list              # what can I ssh to? (no secrets fetched)
corvus ssh status            # agent / Include / loaded keys
```

* `hosts/web01/users/deploy` → `ssh web01` (or `ssh web01-deploy`) as `User deploy`, `IdentitiesOnly yes`.
* Multiple accounts on one host: first keeps the bare hostname alias; others are `web01-root` etc.
* Override aliases in `~/.config/corvus/ssh_hosts` (`host=secret_key`).
* Prefer `ProxyJump` over `ForwardAgent` — identities live in the agent.

```bash
corvus ssh sync                         # refresh host list (also installs Include if missing)
corvus ssh sync --eager                 # prefetch all keys into ssh-agent
corvus ssh sync --prefix ssh/           # custom secret prefix
corvus ssh sync --prefix ""             # kind=ssh only
corvus ssh uninstall                    # remove Include, keep fragment
corvus ssh uninstall --purge            # also delete .pub files and drop agent identities
```

`corvus ssh setup` is install + sync. `corvus ssh config install` still works. After upgrading from file-backed keys, run `corvus ssh setup` (or `sync`) so the fragment points at `.pub` files.

#### ssh-agent on Fedora / RHEL

`corvus ssh` talks to the agent at `$SSH_AUTH_SOCK`. Without it, `setup`/`sync` warn and `ssh <host>` cannot load keys.

**1. Install the OpenSSH client** (`ssh`, `ssh-add`, `ssh-agent`, and on Fedora the user systemd units):

```bash
sudo dnf install -y openssh-clients
```

**2. See if an agent is already running** (GNOME Workstation often starts GNOME Keyring):

```bash
echo "$SSH_AUTH_SOCK"
ssh-add -l
```

| `ssh-add -l` | Meaning |
|---|---|
| lists keys | Agent is up |
| `The agent has no identities.` (exit 1) | Agent is up and empty — fine for Corvus |
| `Could not open a connection…` (exit 2) | No agent — start one below |

Prefer the OpenSSH agent over GNOME Keyring so `ssh-add -t` (Corvus TTL) is honoured.

**3. Fedora, and RHEL with current `openssh-clients` — systemd user socket**

These distros ship `/usr/lib/systemd/user/ssh-agent.socket`, which listens on `$XDG_RUNTIME_DIR/ssh-agent.socket`. Enable it for your user:

```bash
systemctl --user enable --now ssh-agent.socket
systemctl --user status ssh-agent.socket
```

The unit does **not** export `SSH_AUTH_SOCK` into interactive shells (KDE Plasma does via `/etc/xdg/plasma-workspace/env/ssh-agent.sh`). Point your session at the socket:

```bash
# this shell
export SSH_AUTH_SOCK="${XDG_RUNTIME_DIR}/ssh-agent.socket"

# bash logins
grep -q SSH_AUTH_SOCK ~/.bashrc 2>/dev/null || \
  echo 'export SSH_AUTH_SOCK="${XDG_RUNTIME_DIR}/ssh-agent.socket"' >> ~/.bashrc

# systemd user services / graphical sessions (log out and back in)
mkdir -p ~/.config/environment.d
echo 'SSH_AUTH_SOCK=${XDG_RUNTIME_DIR}/ssh-agent.socket' \
  > ~/.config/environment.d/ssh-agent.conf
```

If `systemctl --user cat ssh-agent.socket` fails, the user unit is not packaged (typical of older RHEL 9). Use the fallback.

**4. Fallback — current shell, or older RHEL**

```bash
eval "$(ssh-agent -s)"
```

To start it on every interactive login (RHEL 8/9 documented method):

```bash
echo 'eval "$(ssh-agent -s)"' >> ~/.bashrc
```

**5. Check, then use Corvus**

```bash
ssh-add -l            # "The agent has no identities" is OK
corvus ssh status     # ssh-agent should say running
corvus ssh setup
ssh web01
```

### Folders

```bash
corvus folder list
corvus folder create ops/prod
corvus folder delete <folder-id>
corvus secret list             # shows FOLDER column when any secret has folder_path
```

### Teams, members, groups

```bash
corvus team list
corvus team create NewTeam
corvus team delete NewTeam --yes

corvus team get Platform
corvus member list --team Platform
corvus member add alice@example.com --team Platform --role team-admin
corvus member remove alice@example.com --team Platform

corvus group list --team Platform
corvus group create admins --team Platform
corvus group member add alice@example.com --team Platform --group admins
corvus group member remove alice@example.com --team Platform --group admins

corvus project list
corvus project get ios-app
corvus project create demo --team Platform
corvus project delete demo --yes

corvus member list                        # current project
corvus member list --project other-app    # override without switching
corvus member add bob@example.com --role project-write
```

Roles: `team-viewer` / `team-member` / `team-admin` / `team-owner`; `project-read` / `project-write` / `project-reveal` / `project-admin`; `service-read` / `service-reveal` / `service-write`.

### Machine tokens (PAT, current project)

```bash
corvus token list                              # shows SCOPE
corvus token create ci --role service-write
corvus token create ci --role service-reveal --scope 'API_KEY,prod/*' --expires-days 90
# token (copy now): ss_… scope=API_KEY,prod/*   (printed to stderr once)
corvus token delete <token-uuid>
```

If you omit `--expires-days`, the CLI warns that the token will not expire.

### Secret bindings and project settings (PAT, project admin)

```bash
corvus binding grant API_KEY --to alice@example.com --role secret-reveal
corvus binding grant API_KEY --group <group-id> --role secret-read
corvus binding revoke API_KEY <binding-id>
corvus settings --require-reveal-approval on --default-access-mode restricted
```

### Export and trash (PAT)

```bash
corvus secret export -o env --yes > .env          # --yes required (audited), auto-paginated
corvus secret export -o json --yes --limit 100
corvus trash list
corvus trash restore <secret-uuid>
corvus trash purge <secret-uuid> --yes    # permanent purge
corvus trash restore --all
corvus trash purge --all --yes
```

`secret export` warns with the count. All list endpoints auto-paginate across cursor/`page_token`/`Link: rel="next"`/`has_more`+`page`/`offset`/`total` styles. Caps `SS_MAX_PAGES` (100) and `SS_MAX_ITEMS` (50000) and loop detection stop runaway loops.

### Audit and users (global admin PAT)

```bash
corvus user list -l alice
corvus audit list                            # project audit
corvus audit list --source org               # org / secret / access (admin)
corvus secret history API_KEY
corvus team transfer Platform --to new-owner@example.com
```

### Output

| Flag | Use |
|------|-----|
| `table` | Human tables (**default**) |
| `json` | Pretty-printed JSON |
| `value` | Plaintext value only, for `$(…)` |
| `name` | Key/name/id only |
| `wide` | Alias for `table` |

Global flags: `--no-trunc` disables 48-char cell truncation (for example `corvus secret list --no-trunc`); `--no-paginate`, `--limit N`, `--page-size N` control auto-pagination on all list and export endpoints (with `SS_PAGE_SIZE`/`SS_MAX_PAGES`/`SS_MAX_ITEMS` env overrides). KEY/VALUE columns never truncate. Truncation still escapes newlines.

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
  output.py         # print_table / print_json / trunc / emit, color, never-truncate KEY/VALUE
  parser.py         # argparse wiring, --no-trunc, --expires-days, completion
  cli.py            # main() dispatcher + noun-first argv translation (_NOUN_MAP)
  commands/
    auth.py         # login / project
    secrets.py      # apply (secret)
    management.py   # get / create / delete / restore / transfer
    access.py       # reveal / approve / deny / grant / unbind / export / settings
    ssh.py          # ssh setup / list / status / sync / uninstall (ssh-agent + .pub)
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
| Switch project | `corvus project use [name\|uuid]` |
| List / get secrets | `secret list` / `secret get KEY` |
| Script value | `secret get KEY -o value` |
| Apply secret | `secret set KEY --from-env=V` |
| Teams / projects | `team list` / `project create N --team T` |
| Members | `member add email --team T --role team-member` |
| Tokens | `token create NAME --role service-write [--scope 'K1,prod/*'] [--expires-days 90]` |
| Bindings | `binding grant K --to EMAIL` / `binding revoke K <binding>` |
| Settings | `settings --require-reveal-approval on` |
| Export | `secret export -o env --yes` |
| Groups | `group list --team T` / `group create NAME --team T` |
| Trash | `trash list` / `trash restore ID` |
| SSH hosts | `ssh setup` / `ssh list` / `ssh status` / `ssh uninstall [--purge]` (needs ssh-agent; Fedora/RHEL: `openssh-clients`) |
| Admin | `user list` / `audit list --source access` |
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
| `export … --yes` | Gate | `corvus secret export -o env --yes` |
| `warning: … truncated` | Server paginated | Narrow with `-l` / `q=` |

---

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
