"""Veyra API entrypoint.

A minimal FastAPI application exposing Veyra's current system status and
the analysis pipeline. The polished command-center UI arrives in Phase 6;
this establishes the backend contract and health surface in Phase 0.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .. import __version__
from ..config import get_settings

app = FastAPI(
    title="Veyra API",
    version=__version__,
    description="Veyra market intelligence API",
)


@app.get("/")
def root() -> JSONResponse:
    return JSONResponse(
        {
            "name": "veyra",
            "version": __version__,
            "status": "online",
        }
    )


@app.get("/health")
def health() -> JSONResponse:
    settings = get_settings()
    return JSONResponse(
        {
            "status": "online",
            "environment": settings.environment,
            "timeframes": settings.timeframes,
        }
    )
