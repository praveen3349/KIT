"""Vercel's Python entrypoint.

The application itself remains in kit.main so local Uvicorn, Docker, Render and
Vercel all run the same reviewed code.
"""
from kit.main import app

