"""Load KEY=VALUE lines from .env into the environment (no dependency)."""
import os

_p = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_p):
    for _l in open(_p):
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            _k, _v = _l.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
