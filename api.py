"""Backward-compatibility shim: ``uvicorn api:app`` still works."""
from api import app  # noqa: F401
