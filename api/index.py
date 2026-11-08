"""Vercel Python function entrypoint — exposes the FastAPI app as `app`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.server import app  # noqa: E402,F401
