"""
web/app.py
==========
PathWatch AI — Phase 3 FastAPI server.

Endpoints
---------
GET  /                         Serves the Leaflet.js dashboard (web/static/index.html).
POST /api/audit                Accepts a photo upload, runs the audit pipeline, and returns JSON.
GET  /api/hazards              Returns all audited HazardAssessment records with SQLite IDs.
DELETE /api/hazards/{id}       Deletes a hazard and its uploaded image.
GET  /api/export/geojson       Returns an RFC 7946 GeoJSON FeatureCollection of all hazards.
GET  /api/export/ticket/{hazard_id}  Returns the Markdown 311 repair ticket for a hazard.
"""

from __future__ import annotations

import logging
import sqlite3
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
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
_DB_PATH = _BASE_DIR / "pathwatch.db"


def _initialize_database() -> None:
    """Create the upload directory and persistent hazards table."""
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hazards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_path TEXT,
                assessment_json TEXT,
                created_at TEXT
            )
            """
        )


def _load_hazards() -> list[HazardAssessment]:
    """Load assessments in their persisted insertion order."""
    with sqlite3.connect(_DB_PATH) as connection:
        rows = connection.execute(
            "SELECT assessment_json FROM hazards ORDER BY id ASC"
        ).fetchall()
    return [HazardAssessment.model_validate_json(row[0]) for row in rows]


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    _initialize_database()
    yield


app = FastAPI(
    title="PathWatch AI",
    description=(
        "Offline-first ADA/PROWAG sidewalk accessibility auditor. "
        "All inference runs locally via Ollama — no cloud services required."
    ),
    version="0.3.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# API routes  (must be registered BEFORE static mount)
# ---------------------------------------------------------------------------

@app.post("/api/audit", summary="Upload a photo and run the accessibility audit.")
async def api_audit(
    photo: UploadFile = File(...),
    latitude: float | None = Form(None, ge=-90, le=90),
    longitude: float | None = Form(None, ge=-180, le=180),
) -> JSONResponse:
    """Accept a JPEG/PNG photo upload, audit it, store the result, and return JSON.

    The file is saved to ``sample_data/uploads/`` before inference so the
    pipeline has a stable path to read EXIF GPS from.
    """
    if (latitude is None) != (longitude is None):
        raise HTTPException(
            status_code=422,
            detail="Latitude and longitude must be provided together.",
        )

    # Save upload to disk
    original_name = (photo.filename or "upload.jpg").replace("\\", "/")
    filename = Path(original_name).name or "upload.jpg"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = _UPLOAD_DIR / f"{timestamp}_{uuid4().hex}_{filename}"

    with dest.open("wb") as fh:
        shutil.copyfileobj(photo.file, fh)

    logger.info("Saved upload to %s", dest)

    try:
        assessment = audit_image(
            dest,
            override_lat=latitude,
            override_lon=longitude,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    logger.info(
        "Audit coordinates received: latitude=%s longitude=%s gps_source=%s",
        latitude,
        longitude,
        assessment.gps_source,
    )

    with sqlite3.connect(_DB_PATH) as connection:
        cursor = connection.execute(
            """
            INSERT INTO hazards (image_path, assessment_json, created_at)
            VALUES (?, ?, ?)
            """,
            (
                assessment.image_path,
                assessment.model_dump_json(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        hazard_id = cursor.lastrowid
    response = assessment.model_dump(mode="json")
    response["id"] = hazard_id
    return JSONResponse(content=response)


@app.get("/api/hazards", summary="Return all audited hazard assessments.")
async def api_hazards() -> JSONResponse:
    """Return all persisted HazardAssessments as JSON."""
    with sqlite3.connect(_DB_PATH) as connection:
        rows = connection.execute(
            "SELECT id, assessment_json FROM hazards ORDER BY id ASC"
        ).fetchall()
    hazards = [
        {**HazardAssessment.model_validate_json(row[1]).model_dump(mode="json"), "id": row[0]}
        for row in rows
    ]
    return JSONResponse(content=hazards)


@app.delete("/api/hazards/{hazard_id}", summary="Delete an audited hazard.")
async def api_delete_hazard(hazard_id: int) -> JSONResponse:
    """Delete the hazard record and its image when the image is in the uploads directory."""
    with sqlite3.connect(_DB_PATH) as connection:
        row = connection.execute(
            "SELECT image_path FROM hazards WHERE id = ?", (hazard_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Hazard not found")

        image_path = Path(row[0]) if row[0] else None
        if image_path is not None:
            if not image_path.is_absolute():
                image_path = _BASE_DIR / image_path
            image_path = image_path.resolve()
            try:
                image_path.relative_to(_UPLOAD_DIR.resolve())
            except ValueError:
                image_path = None

        if image_path is not None and image_path.is_file():
            image_path.unlink()

        connection.execute("DELETE FROM hazards WHERE id = ?", (hazard_id,))

    return JSONResponse(content={"status": "deleted", "id": hazard_id})


@app.get("/api/export/geojson", summary="Export all hazards as RFC 7946 GeoJSON.")
async def api_export_geojson() -> JSONResponse:
    """Return a GeoJSON FeatureCollection with OSM accessibility tags."""
    return JSONResponse(
        content=export_geojson(_load_hazards()),
        media_type="application/geo+json",
    )


@app.get(
    "/api/export/ticket/{hazard_id}",
    summary="Export the 311 repair ticket for a hazard.",
)
async def api_export_ticket(hazard_id: int) -> Response:
    """Return the Markdown 311 repair packet for the hazard with this SQLite ID."""
    with sqlite3.connect(_DB_PATH) as connection:
        row = connection.execute(
            "SELECT assessment_json FROM hazards WHERE id = ?", (hazard_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Hazard not found")

    assessment = HazardAssessment.model_validate_json(row[0])
    markdown_text = export_311_ticket_markdown(assessment)
    return Response(
        content=markdown_text,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="311_ticket_{hazard_id}.md"'
        },
    )


# ---------------------------------------------------------------------------
# Static files + SPA root  (registered LAST so API routes take precedence)
# ---------------------------------------------------------------------------

app.mount(
    "/uploads",
    StaticFiles(directory=str(_UPLOAD_DIR), check_dir=False),
    name="uploads",
)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    """Serve the Leaflet.js dashboard."""
    return FileResponse(str(_STATIC_DIR / "index.html"))
