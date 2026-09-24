from __future__ import annotations

import logging
import sys

from .storage.xdg import cache_dir


def setup_logging(level: str = "INFO") -> logging.Logger:
    log = logging.getLogger("multiprofile")
    if log.handlers:
        return log
    log.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    try:
        fh = logging.FileHandler(cache_dir() / "multiprofile.log")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError:
        pass
    return log
