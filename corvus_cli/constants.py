"""Shared constants for the Corvus CLI.

Description:
    Central store for env names, paths, regexes, timeout bounds, role tuples and
    user-facing usage text so every module imports from one source. Keeps the
    version in one place via ``corvus_cli.__version__``.

Inputs:
    None at import time; values read from :mod:`pathlib` / :mod:`re`.

Outputs:
    Module-level constants (``CONFIG_PATH``, ``USAGE``, ``KNOWN_RESOURCES``, …).
    No functions.

Example:
    >>> from corvus_cli.constants import CONFIG_PATH, USAGE, _UUID_RE
    >>> bool(_UUID_RE.match("11111111-1111-1111-1111-111111111111"))
    True
"""

from __future__ import annotations

import re
from pathlib import Path

from corvus_cli import VERSION

CONFIG_DIR = Path.home() / ".config" / "corvus"
CONFIG_PATH = CONFIG_DIR / "config"

ENV_URL = "SS_URL"
ENV_TOKEN = "SS_TOKEN"
ENV_PROJECT = "SS_PROJECT"
ENV_PROJECT_ALIASES = (ENV_PROJECT, "PID")

ENV_TIMEOUT = "SS_TIMEOUT"
TIMEOUT_DEFAULT = 60
TIMEOUT_MIN = 5
TIMEOUT_MAX = 300

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Server RBAC role names, mirror app/routes/mgmt_api.
_TEAM_ROLES = ("team-viewer", "team-member", "team-admin", "team-owner")
_PROJECT_ROLES = (
    "project-read",
    "project-write",
    "project-reveal",
    "project-admin",
)
_SERVICE_ROLES = ("service-read", "service-reveal", "service-write")
_SECRET_ROLES = ("secret-read", "secret-write", "secret-reveal")

# All resource names accepted by any subcommand (for did-you-mean).
KNOWN_RESOURCES: dict[str, str] = {
    # get/list
    "secrets": "get secrets / apply secret",
    "secret": "get secret / apply secret",
    "projects": "get projects",
    "project": "get project / create project",
    "teams": "get teams",
    "team": "get team / create team",
    "members": "get members / create member",
    "member": "get members / create member",
    "tokens": "get tokens / create token",
    "token": "get tokens / create token",
    "trash": "get trash / restore trash / delete trash",
    "folders": "get folders / create folder",
    "folder": "get folders / create folder",
    "history": "get history KEY",
    "requests": "get requests",
    "request": "get requests",
    "access-requests": "get requests",
    "audit": "get audit",
    "users": "get users",
    "user": "get users",
    "groups": "get groups",
    "group": "get groups / create group",
    "group-member": "create group-member / delete group-member",
}

USAGE = f"""\
corvus {VERSION} - command-line client for Corvus

Quick start:

    corvus login --url https://secrets.example --token ss_… --project <uuid>
    corvus secret list
    corvus secret get API_KEY -o value
    printf '%s' "$NEW" | corvus secret set API_KEY --from-file=-

Usage:
    corvus <noun> <verb> [name] [flags]
    (verb-first aliases still work: `get secret`, `apply secret`, …)

Tokens:
    ss_…   machine token: one project, secrets only (UUID)
    pat_…  personal access token: secrets + teams/projects/admin (name or UUID)
    sso_…  CLI session token: user-scoped, short-lived (name or UUID)

Commands (noun verb; KEY/VALUE columns never truncate; tables use color on TTY)
  Secrets
    secret list [ -l QUERY ]          list metadata for the current project
    secret get KEY [ -o value|json ]  read a secret
    secret set KEY …                  create/update a secret (prefer --from-file or --from-env)
    secret delete KEY                 soft-delete a secret (to trash)
    secret reveal KEY [ --reason … ]  request reveal approval (or print when allowed)
    secret history KEY                version history of a secret
    secret export [ -o env|json|csv ] dump plaintext secrets (audited, needs --yes)
    request list                      list reveal access requests
    request approve|deny REQ [ --minutes N ]  resolve a pending request (admin)

  Access & metadata
    binding grant KEY --to EMAIL|--group ID|--sa ID --role R    secret role binding
    binding revoke KEY BINDING_ID                               remove a binding
    settings [ --require-reveal-approval on ]                   project settings

  Projects
    project [ NAME ] | project use NAME   show or switch default project
    project list                           list projects (PAT)
    project get|create|delete NAME [--team TEAM]

  Teams, members, groups
    team list | team get NAME | team create|delete NAME
    member list [ --team TEAM ] | member add|remove EMAIL [ --role R ]
    team transfer NAME --to USER       change team ownership
    group list --team TEAM | group create|delete NAME --team TEAM
    group member add|remove EMAIL --team TEAM --group GROUP

  Tokens & trash
    token list                         list machine tokens (shows key scope)
    token create NAME --role service-write [ --scope 'K1,prod/*' ]
    token delete ID
    trash list | trash restore ID | trash purge ID
    trash restore --all | trash purge --all          # bulk restore / purge

  Folders
    folder list                        list folders for current project
    folder create PATH                 create a folder (e.g. 'ops/prod')
    folder delete ID                   delete an empty folder (use from list)

  SSH hosts (native ssh <host>)
    ssh setup                         once: wire ~/.ssh/config + discover hosts
    ssh list | status | sync [--eager] [--clean]
    ssh uninstall [--purge]           remove Include (and keys with --purge)

  Admin (global)
    user list [ -l QUERY ] | audit list [ --source project|org|secret|access ]
    help                              this summary

  Completion
    completion bash|zsh|fish          print shell completion script

Options
    -o, --output FORMAT   table (default) | json | value | name | wide
    -l, --selector QUERY  filter secrets (key/note/metadata), members, or users
    --project NAME|UUID   target a project without switching
    --yes                 confirm destructive / bulk export (team/project/trash purge, export)
    --no-trunc            do not truncate table cells
    -V, --version         print version

Credentials:  use env SS_URL / SS_TOKEN / SS_PROJECT, or run
    corvus login --url … --token … [--project …]   (project optional for pat_…/sso_…)
"""

# Bash/zsh/fish completion snippets (kept tiny; emitted via `corvus completion <shell>`).
_COMPLETION_SH: dict[str, str] = {
    "bash": r"""# corvus bash completion - source or drop in /etc/bash_completion.d/corvus
_corvus_completions() {
  local cur="${COMP_WORDS[COMP_CWORD]}" cmds="login project secret folder request binding team member group token trash user audit get create delete apply set reveal approve deny restore transfer grant unbind export settings ssh help completion"
  local resources="secrets secret projects project teams team members member tokens token groups group group-member trash folders folder history requests audit users user"
  local ssh_sub="setup list status sync uninstall"
  if [[ $COMP_CWORD -eq 1 ]]; then COMPREPLY=( $(compgen -W "$cmds" -- "$cur") ); return; fi
  if [[ "${COMP_WORDS[1]}" == "ssh" ]]; then
    if [[ $COMP_CWORD -eq 2 ]]; then COMPREPLY=( $(compgen -W "$ssh_sub" -- "$cur") ); return; fi
    return
  fi
  case "${COMP_WORDS[1]}" in get|create|delete|apply|set|restore|grant) COMPREPLY=( $(compgen -W "$resources" -- "$cur") );; esac
}
complete -F _corvus_completions corvus
""",
    "zsh": r"""#compdef corvus
# corvus zsh completion - put in $fpath as _corvus
_corvus() {
  local -a cmds=(login project secret folder request binding team member group token trash user audit get create delete apply set reveal approve deny restore transfer grant unbind export settings ssh help completion)
  local -a res=(secrets secret projects project teams team members member tokens token groups group group-member trash folders folder history requests audit users user)
  local -a ssh_sub=(setup list status sync uninstall)
  if (( CURRENT == 2 )); then _describe 'command' cmds
  elif [[ $words[2] == ssh ]] && (( CURRENT == 3 )); then _describe 'ssh subcommand' ssh_sub
  else _describe 'resource' res; fi
}
compdef _corvus corvus
""",
    "fish": r"""# corvus fish completion - put in ~/.config/fish/completions/corvus.fish
complete -c corvus -n '__fish_seen_subcommand_from get create delete apply set restore grant' -a 'secrets secret projects project teams team members member tokens token groups group group-member trash folders folder history requests audit users user'
complete -c corvus -n 'not __fish_seen_subcommand_from login project secret folder request binding team member group token trash user audit get create delete apply set reveal approve deny restore transfer grant unbind export settings ssh help completion' -a 'login project secret folder request binding team member group token trash user audit get create delete apply set reveal approve deny restore transfer grant unbind export settings ssh help completion'
complete -c corvus -n '__fish_seen_subcommand_from ssh' -a 'setup list status sync uninstall'
""",
}
