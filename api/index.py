"""Vercel Python function entrypoint for the FastAPI application."""

from backend.server import app

__all__ = ["app"]
