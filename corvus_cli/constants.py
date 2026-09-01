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

# Server RBAC role names — mirror app/routes/mgmt_api.
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
    corvus get secrets
    corvus get secret API_KEY -o value
    printf '%s' "$NEW" | corvus apply secret API_KEY --from-file=-

Usage:
    corvus <command> [resource] [name] [flags]

Tokens:
    ss_…   machine token: one project, secrets only (UUID)
    pat_…  personal access token: secrets + teams/projects/admin (name or UUID)
    sso_…  CLI session token: user-scoped, short-lived (name or UUID)

Commands
  Secrets
    get secrets [ -l QUERY ]          list metadata for the current project
    get secret KEY [ -o value|json ]  read a secret
    apply|set secret KEY …            create/update a secret (prefer --from-file or --from-env)
    delete secret KEY                 soft-delete a secret (to trash)
    reveal secret KEY [ --reason … ]  request reveal approval (or print when allowed)
    approve|deny REQ [ --minutes N ]  resolve a pending reveal request (admin)
    export [ PROJECT ] [ -o env|json|csv ]   dump plaintext secrets (audited, needs --yes)

  Access & metadata
    grant secret KEY --to EMAIL|--group ID|--sa ID --role R    secret role binding
    unbind secret KEY BINDING_ID                               remove a binding
    settings [ --require-reveal-approval on ]                  project settings

  Projects
    project [ NAME ]                  show or switch default project
    get projects                      list projects (PAT)
    get project NAME | create|delete project NAME --team TEAM

  Teams, members, groups
    get teams | team NAME | create|delete team NAME
    get members [ --team TEAM ] | create|delete member EMAIL [ --role R ]
    transfer team NAME --email USER   change team ownership
    get groups --team TEAM | create|delete group NAME --team TEAM
    create|delete group-member EMAIL --team TEAM --group GROUP

  Tokens & trash
    get tokens                        list machine tokens (shows key scope)
    create token NAME --role service-write [ --scope 'K1,prod/*' ]
    delete token ID
    get trash | restore trash ID | delete trash ID
    restore trash --all | delete trash --all          # bulk restore / purge

  Folders
    get folders                        list folders for current project
    create folder PATH                 create a folder (e.g. 'ops/prod')
    delete folder ID                   delete an empty folder (use from list)

  Admin (global)
    get users [ -l QUERY ] | get audit [ --source project|org|secret|access ]
    get history KEY                   version history of a secret
    get requests                      list reveal access requests
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
    "bash": r"""# corvus bash completion — source or drop in /etc/bash_completion.d/corvus
_corvus_completions() {
  local cur="${COMP_WORDS[COMP_CWORD]}" cmds="login project get create delete apply set reveal approve deny restore transfer grant unbind export settings help completion"
  local resources="secrets secret projects project teams team members member tokens token groups group group-member trash folders folder history requests audit users user"
  if [[ $COMP_CWORD -eq 1 ]]; then COMPREPLY=( $(compgen -W "$cmds" -- "$cur") ); return; fi
  case "${COMP_WORDS[1]}" in get|create|delete|apply|set|restore|grant) COMPREPLY=( $(compgen -W "$resources" -- "$cur") );; esac
}
complete -F _corvus_completions corvus
""",
    "zsh": r"""#compdef corvus
# corvus zsh completion — put in $fpath as _corvus
_corvus() {
  local -a cmds=(login project get create delete apply set reveal approve deny restore transfer grant unbind export settings help completion)
  local -a res=(secrets secret projects project teams team members member tokens token groups group group-member trash folders folder history requests audit users user)
  if (( CURRENT == 2 )); then _describe 'command' cmds; else _describe 'resource' res; fi
}
compdef _corvus corvus
""",
    "fish": r"""# corvus fish completion — put in ~/.config/fish/completions/corvus.fish
complete -c corvus -n '__fish_seen_subcommand_from get create delete apply set restore grant' -a 'secrets secret projects project teams team members member tokens token groups group group-member trash folders folder history requests audit users user'
complete -c corvus -n 'not __fish_seen_subcommand_from login project get create delete apply set reveal approve deny restore transfer grant unbind export settings help completion' -a 'login project get create delete apply set reveal approve deny restore transfer grant unbind export settings help completion'
""",
}
