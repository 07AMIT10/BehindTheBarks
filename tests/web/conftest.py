"""Hermetic test isolation for the whole web test-suite.

Two repo-owned paths leak out of tmp when tests build a real app:

- `backend/web/settings.py::WEB_DEFAULTS` sets the store to `sqlite:///out/btb.db`,
  and `backend/main.py:113` falls back to that same URL — every app created from
  `load_config(<nonexistent>.yaml)` silently reads/writes the repo's `out/btb.db`.
- `backend/main.py:1198,1207,1210` hardcode CWD-relative `Path("out/snippets")`
  for audio snippet storage.

This autouse fixture (a) re-points both defaults at this test's `tmp_path` —
`load_config` deep-copies `WEB_DEFAULTS` on every call (`_merge`), so patching
the module dict is safe — and (b) wraps `backend.main.Path` so the hardcoded
`out/...` string arguments resolve under `tmp_path/out` instead of the working
directory. Tests that configure their own `log_dir`/`database_url` keep working
unchanged; the defaults only matter when a test does not set them.
"""

from pathlib import Path as _RealPath

import pytest

from backend.web import settings as _web_settings


@pytest.fixture(autouse=True)
def _hermetic_store_and_snippets(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setitem(_web_settings.WEB_DEFAULTS, "log_dir", str(out))
    monkeypatch.setitem(
        _web_settings.WEB_DEFAULTS, "database_url", f"sqlite:///{tmp_path / 'btb.db'}"
    )

    def _ShimPath(*args, **kwargs):
        if args and isinstance(args[0], str):
            arg = args[0]
            if arg == "out":
                args = (str(out),)
            elif arg.startswith("out/"):
                args = (str(out / arg[4:]),)
        return _RealPath(*args, **kwargs)

    import backend.main as _backend_main

    monkeypatch.setattr(_backend_main, "Path", _ShimPath)
