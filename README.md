# secretserver-cli

CLI for **Sigaint Secret Server** unified secret API (`/eso/v1`).  
Stdlib Python only; RHEL 9+.

API contract: sibling repo `secretserver` → [docs/api.md](../secretserver/docs/api.md).

## Install

```bash
sudo install -m 0755 secretserver /usr/bin/secretserver
# or: make rpm && sudo dnf install -y dist/secretserver-cli-*.noarch.rpm
```

## Credentials

Env vars win over `~/.config/secretserver/config` (mode `0600`):

| Env | Config | Meaning |
|-----|--------|---------|
| `SS_URL` | `url` | Base URL, no trailing slash |
| `SS_TOKEN` | `token` | `ss_…` machine **or** `pat_…` personal access token |
| `SS_PROJECT` | `project` | Project UUID (`ss_…`) or UUID/name (`pat_…`) |
| `PID` | — | Legacy alias for `SS_PROJECT` |

| Token | Auth | Project |
|-------|------|---------|
| `ss_…` | Project machine token | **UUID only** |
| `pat_…` | Personal access token (user RLS) | UUID or unique **name** |

Both use the same endpoints: `/eso/v1/projects/<project>/secrets…`.

```bash
# Machine token
secretserver login --url https://secrets.example.com --token ss_… --project <uuid>

# Personal access token (name ok)
secretserver login --url https://secrets.example.com --token pat_… --project ios-app
```

## Usage

```bash
secretserver list
secretserver list -q api
secretserver get API_KEY
secretserver edit API_KEY --value 'new' --note 'rotated'
secretserver edit API_KEY --note 'label only'
secretserver delete API_KEY
secretserver projects          # PAT only
```
