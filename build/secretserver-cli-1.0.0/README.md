# secretserver-cli

CLI for **Sigaint Secret Server** machine API (`/eso/v1`). Stdlib Python only; RHEL 9+.

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
| `SS_TOKEN` | `token` | Project machine token `ss_…` (**write** to modify/delete) |
| `SS_PROJECT` | `project` | Project UUID |

Config path: `~/.config/secretserver/config` (mode `0600`).

```bash
secretserver login --url https://secrets.example.com --token ss_… --project <uuid>
# or
export SS_URL=… SS_TOKEN=… SS_PROJECT=…
```

## Usage

```bash
secretserver list
secretserver list -q api
secretserver get API_KEY
secretserver edit API_KEY --value 'new' --note 'rotated'
secretserver edit API_KEY --note 'label only'
secretserver delete API_KEY
```

