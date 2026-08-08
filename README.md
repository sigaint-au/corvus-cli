# secretserver-cli

CLI for **Sigaint Secret Server** machine API (`/eso/v1`). Stdlib Python only; RHEL 9+.

API contract: sibling repo `secretserver` → [docs/api.md](../secretserver/docs/api.md).

## Install

```bash
# from source
sudo install -m 0755 secretserver /usr/bin/secretserver

# RPM (on a box with rpmbuild)
make rpm
sudo dnf install -y dist/secretserver-cli-*.noarch.rpm
```

## Credentials

Env vars win over the config file:

| Env | Config key | Meaning |
|-----|------------|---------|
| `SS_URL` | `url` | Base URL, no trailing slash |
| `SS_TOKEN` | `token` | Project machine token `ss_…` (**write** to edit/delete) |
| `SS_PROJECT` | `project` | Project UUID |
| `PID` | — | Legacy alias for `SS_PROJECT` (docs/curl) |

Config path: `~/.config/secretserver/config` (mode `0600`).

```bash
secretserver login --url https://secrets.example.com --token ss_… --project <uuid>
# alias: secretserver configure …
# or
export SS_URL=… SS_TOKEN=… SS_PROJECT=…
```

## Usage

Maps to the machine API:

| Command | HTTP |
|---------|------|
| `list` | `GET …/secrets?meta=1` |
| `get KEY` | `GET …/secrets/{key}` |
| `edit KEY --value …` | `PUT …/secrets/{key}` (upsert) |
| `edit KEY --note …` | `PATCH …/secrets/{key}` |
| `delete KEY` | `DELETE …/secrets/{key}` |

```bash
secretserver list
secretserver list -q api
secretserver get API_KEY
secretserver edit API_KEY --value 'new' --note 'rotated'
secretserver edit API_KEY --note 'label only'
secretserver delete API_KEY
```
