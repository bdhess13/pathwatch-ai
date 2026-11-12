"""
web/app.py
==========
PathWatch AI — Phase 3 FastAPI server.

Endpoints
---------
GET  /                         Serves the Leaflet.js dashboard (web/static/index.html).
POST /api/audit                Accepts a photo upload, runs the audit pipeline, returns JSON.
GET  /api/hazards              Returns all audited HazardAssessments as JSON.
GET  /api/export/geojson       Returns a RFC 7946 GeoJSON FeatureCollection of all hazards.
GET  /api/export/ticket/{idx}  Returns the Markdown 311 repair ticket for hazard at index idx.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from core.auditor import audit_image
from core.exporter import export_311_ticket_markdown, export_geojson
from core.schemas import HazardAssessment

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BASE_DIR = Path(__file__).resolve().parent.parent
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_UPLOAD_DIR = _BASE_DIR / "sample_data" / "uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# In-memory store (list of HazardAssessment)
# Survives the process lifetime; suitable for the competition demo.
# ---------------------------------------------------------------------------

_hazards: list[HazardAssessment] = []

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PathWatch AI",
    description=(
        "Offline-first ADA/PROWAG sidewalk accessibility auditor. "
        "All inference runs locally via Ollama — no cloud services required."
    ),
    version="0.3.0",
)


# ---------------------------------------------------------------------------
# API routes  (must be registered BEFORE static mount)
# ---------------------------------------------------------------------------

@app.post("/api/audit", summary="Upload a photo and run the accessibility audit.")
async def api_audit(photo: UploadFile = File(...)) -> JSONResponse:
    """Accept a JPEG/PNG photo upload, audit it, store the result, and return JSON.

    The file is saved to ``sample_data/uploads/`` before inference so the
    pipeline has a stable path to read EXIF GPS from.
    """
    # Save upload to disk
    dest = _UPLOAD_DIR / (photo.filename or "upload.jpg")
    # Avoid clobbering previous uploads with the same name
    stem, suffix = dest.stem, dest.suffix
    counter = 0
    while dest.exists():
        counter += 1
        dest = _UPLOAD_DIR / f"{stem}_{counter}{suffix}"

    with dest.open("wb") as fh:
        shutil.copyfileobj(photo.file, fh)

    logger.info("Saved upload to %s", dest)

    try:
        assessment = audit_image(dest)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    _hazards.append(assessment)
    return JSONResponse(content=assessment.model_dump(mode="json"))


@app.get("/api/hazards", summary="Return all audited hazard assessments.")
async def api_hazards() -> JSONResponse:
    """Return the in-memory list of all audited HazardAssessments as JSON."""
    return JSONResponse(content=[h.model_dump(mode="json") for h in _hazards])


@app.get("/api/export/geojson", summary="Export all hazards as RFC 7946 GeoJSON.")
async def api_export_geojson() -> JSONResponse:
    """Return a GeoJSON FeatureCollection with OSM accessibility tags."""
    return JSONResponse(
        content=export_geojson(_hazards),
        media_type="application/geo+json",
    )


@app.get(
    "/api/export/ticket/{index}",
    summary="Export the 311 repair ticket for hazard at the given index.",
    response_class=PlainTextResponse,
)
async def api_export_ticket(index: int) -> PlainTextResponse:
    """Return the Markdown 311 repair packet for the hazard at list position ``index``."""
    if index < 0 or index >= len(_hazards):
        raise HTTPException(
            status_code=404,
            detail=f"No hazard at index {index}. Currently {len(_hazards)} hazard(s) stored.",
        )
    ticket_md = export_311_ticket_markdown(_hazards[index])
    return PlainTextResponse(content=ticket_md, media_type="text/markdown")


# ---------------------------------------------------------------------------
# Static files + SPA root  (registered LAST so API routes take precedence)
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    """Serve the Leaflet.js dashboard."""
    return FileResponse(str(_STATIC_DIR / "index.html"))
