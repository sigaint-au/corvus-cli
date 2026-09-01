"""Corvus CLI package.

Single version source for the entire project.

Token kinds
-----------
``ss_…`` machine token, ``pat_…`` personal access token, ``sso_…`` CLI session.
"""

from __future__ import annotations

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("corvus-cli")
except Exception:
    __version__ = "1.0.0"

VERSION = __version__
