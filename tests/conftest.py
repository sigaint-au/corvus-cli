"""Pytest conftest: make corvus_cli importable and sync shim patches."""
from __future__ import annotations

import pathlib
import sys

# Ensure project root on path so `import corvus_cli` works when pytest is
# invoked without `pip install -e .`.
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Sync mock.patch.object on the `corvus` shim to underlying corvus_cli modules
# so tests patching `ss._http` also affect `corvus_cli.api._http`, etc.
import importlib
import unittest.mock as _mock

_orig_patch_object = _mock.patch.object

_UNDERLYING_MODS = [
    "corvus_cli.api",
    "corvus_cli.config",
    "corvus_cli.constants",
    "corvus_cli.output",
    "corvus_cli.cli",
    "corvus_cli.parser",
    "corvus_cli.commands.auth",
    "corvus_cli.commands.management",
    "corvus_cli.commands.secrets",
    "corvus_cli.commands.access",
]


def _is_corvus_shim(target) -> bool:
    try:
        return getattr(target, "__name__", "") == "corvus" or hasattr(target, "VERSION")
    except Exception:
        return False


class _CombinedPatch:
    def __init__(self, target, attribute, args, kwargs):
        self.target = target
        self.attribute = attribute
        self.args = args
        self.kwargs = kwargs
        self.base_cm = _orig_patch_object(target, attribute, *args, **kwargs)
        self.extra_cms: list = []
        self.underlying: list = []
        # collect underlying modules that have attribute
        for mod_name in _UNDERLYING_MODS:
            try:
                mod = importlib.import_module(mod_name)
                if hasattr(mod, attribute):
                    self.underlying.append(mod)
            except Exception:
                continue

    def __enter__(self):
        # Enter base first to obtain the mock instance (handles return_value/side_effect)
        mock_obj = self.base_cm.__enter__()  # type: ignore[assignment]
        # Now patch underlying modules with the SAME mock instance
        for mod in self.underlying:
            # If caller supplied explicit new as positional arg, use that same new;
            # otherwise reuse mock_obj
            if self.args:
                new = self.args[0]
            elif "new" in self.kwargs:
                new = self.kwargs["new"]
            else:
                new = mock_obj
            cm = _orig_patch_object(mod, self.attribute, new)
            cm.__enter__()
            self.extra_cms.append(cm)
        # Special mapping: patching _http should also make pagination-aware
        # helpers return paginated-friendly results (first page only).
        if self.attribute == "_http":
            try:
                api_mod = importlib.import_module("corvus_cli.api")
                if hasattr(api_mod, "_http_with_headers"):
                    def _wrap_http_with_headers(*a, **kw):  # type: ignore[no-untyped-def]
                        res = mock_obj(*a, **kw)
                        # Normalize SystemExit propagation
                        return (res, {})
                    cm2 = _orig_patch_object(api_mod, "_http_with_headers", _wrap_http_with_headers)
                    cm2.__enter__()
                    self.extra_cms.append(cm2)
            except Exception:
                pass
        return mock_obj

    def __exit__(self, *exc):
        for cm in reversed(self.extra_cms):
            try:
                cm.__exit__(*exc)
            except Exception:
                pass
        return self.base_cm.__exit__(*exc)


def _patched_patch_object(target, attribute, *args, **kwargs):
    if not _is_corvus_shim(target):
        return _orig_patch_object(target, attribute, *args, **kwargs)
    return _CombinedPatch(target, attribute, args, kwargs)


_mock.patch.object = _patched_patch_object  # type: ignore[assignment]
import unittest

unittest.mock.patch.object = _patched_patch_object  # type: ignore[assignment]

# ── Sync monkeypatch.setattr for CONFIG_DIR / CONFIG_PATH etc.
try:
    from _pytest.monkeypatch import MonkeyPatch as _MP

    _orig_mp_setattr = _MP.setattr

    def _patched_mp_setattr(self, target, name, value, raising=True):  # type: ignore[no-untyped-def]
        # Do the original
        result = _orig_mp_setattr(self, target, name, value, raising=raising)
        if not _is_corvus_shim(target):
            return result
        # Propagate to underlying modules that have this name
        for mod_name in _UNDERLYING_MODS:
            try:
                mod = importlib.import_module(mod_name)
                if hasattr(mod, name):
                    try:
                        _orig_mp_setattr(self, mod, name, value, raising=False)
                    except Exception:
                        # Fallback direct set (not tracked for undo, but still works)
                        try:
                            setattr(mod, name, value)
                        except Exception:
                            pass
            except Exception:
                continue
        # Also propagate to corvus_cli.constants/config for CONFIG_DIR/PATH
        # Ensure config module's cached imports are updated as well
        # (MonkeyPatch already tracks undo, so tmp config dir reverts after test)
        return result

    _MP.setattr = _patched_mp_setattr  # type: ignore[assignment]
except Exception:
    pass
