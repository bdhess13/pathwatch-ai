"""
core/auditor.py
===============
PathWatch AI — Local VLM inference + MCP enrichment pipeline.

``audit_image`` is the single entry-point for the pipeline:

1. Resize & base64-encode the image (max 1024 px on the longest side).
2. Send a structured-output chat request to local Ollama, constraining
   the response to ``VisionFinding.model_json_schema()``.
3. Validate the JSON response through Pydantic (``VisionFinding``).
4. Enrich with ADA/PROWAG standards and 311 routing via the MCP server's
   plain Python functions (``lookup_ada_standard``, ``find_jurisdiction_contact``).
5. Return a fully populated ``HazardAssessment``.

Environment variables
---------------------
PATHWATCH_MODEL   Ollama model tag to use (default: ``moondream``).
"""

import base64
import io
import logging
import os
from pathlib import Path

import httpx
from pydantic import ValidationError

from core.ingest import extract_gps
from core.schemas import HazardAssessment, VisionFinding
from mcp_server.server import find_jurisdiction_contact, lookup_ada_standard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OLLAMA_BASE_URL: str = "http://localhost:11434"
_DEFAULT_MODEL: str = "moondream"

# Inference options: low temperature for deterministic classifications.
_OLLAMA_OPTIONS: dict = {"temperature": 0.1, "repeat_penalty": 1.3}

# ---------------------------------------------------------------------------
# Barrier-type -> DB search term mapping
# Maps uppercase BarrierType literals to lowercase phrases that match the
# barrier_type / description columns seeded in pathwatch.db.
# ---------------------------------------------------------------------------

_BARRIER_DB_QUERY: dict[str, str] = {
    "MISSING_CURB_RAMP": "curb ramp",
    "MISSING_TACTILE_PAVING": "detectable warning",
    "HEAVED_OR_CRACKED_PAVEMENT": "surface",
    "PATH_OBSTRUCTION": "protruding object",
}

_SYSTEM_PROMPT = (
    "You are PathWatch AI, an expert ADA/PROWAG accessibility auditor. "
    "Analyse the sidewalk or walkway image and return a JSON object that "
    "strictly matches the provided schema. Be concise and specific."
)

_USER_PROMPT = (
    "Examine this sidewalk image carefully. "
    "Identify the primary accessibility barrier (or confirm compliance). "
    "Return ONLY valid JSON matching the schema — no markdown, no explanation."
)

# Maximum pixel dimension for the image sent to the model.
_MAX_IMAGE_PX: int = 1024


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resize_and_encode(image_path: Path) -> str:
    """Return a base64-encoded JPEG string, resized to fit within _MAX_IMAGE_PX.

    This keeps the payload small for local inference on consumer hardware.
    """
    from PIL import Image  # lazy import — not needed for GPS-only usage

    with Image.open(image_path) as img:
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > _MAX_IMAGE_PX:
            scale = _MAX_IMAGE_PX / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii")


def _call_ollama(image_b64: str, model: str) -> VisionFinding:
    """Send a multimodal chat request to Ollama and parse the structured response.

    Raises
    ------
    RuntimeError
        If Ollama is unreachable (httpx.ConnectError / ConnectTimeout).
    ValidationError
        If the model returns JSON that does not satisfy VisionFinding schema.
    """
    payload = {
        "model": model,
        "stream": False,
        "options": _OLLAMA_OPTIONS,
        "format": VisionFinding.model_json_schema(),
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _USER_PROMPT,
                "images": [image_b64],
            },
        ],
    }

    try:
        response = httpx.post(
            f"{_OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=120.0,  # local inference can be slow on CPU
        )
        response.raise_for_status()
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(
            "Cannot reach Ollama at http://localhost:11434. "
            "Start it with: ollama serve"
        ) from exc

    raw_content: str = response.json()["message"]["content"]
    logger.debug("Raw Ollama response: %s", raw_content)

    # Ollama with `format` set should return pure JSON, but guard against
    # accidental markdown fences just in case.
    cleaned = raw_content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    return VisionFinding.model_validate_json(cleaned)


# ---------------------------------------------------------------------------
# Public: audit_image
# ---------------------------------------------------------------------------

def audit_image(image_path: Path, model: str | None = None) -> HazardAssessment:
    """Full single-image audit pipeline.

    Steps
    -----
    1. Extract GPS from EXIF (or use fallback coordinates).
    2. Resize & base64-encode the image.
    3. Send to local Ollama; validate response as ``VisionFinding``.
    4. Enrich with ADA standards and jurisdiction routing via MCP server functions.
    5. Return a ``HazardAssessment``.

    Args:
        image_path: Path to the image file to audit.
        model:      Ollama model tag override.  Defaults to the value of the
                    ``PATHWATCH_MODEL`` env-var, or ``moondream``.

    Returns:
        A fully populated ``HazardAssessment`` instance.

    Raises:
        RuntimeError: If Ollama is unreachable.
        pydantic.ValidationError: If the VLM response cannot be parsed.
    """
    resolved_model = model or os.getenv("PATHWATCH_MODEL", _DEFAULT_MODEL)
    image_path = Path(image_path)

    # Step 1 — GPS
    latitude, longitude, gps_source = extract_gps(image_path)
    logger.info(
        "GPS for %s: (%.6f, %.6f) source=%s",
        image_path.name,
        latitude,
        longitude,
        gps_source,
    )

    # Step 2 — encode
    image_b64 = _resize_and_encode(image_path)

    # Step 3 — VLM inference
    logger.info("Sending %s to Ollama model '%s' …", image_path.name, resolved_model)
    finding = _call_ollama(image_b64, resolved_model)
    logger.info(
        "VLM result: barrier=%s severity=%s confidence=%.2f",
        finding.barrier_type,
        finding.severity,
        finding.confidence,
    )

    # Step 4 — ADA/PROWAG grounding (skip enrichment for COMPLIANT findings)
    # Use the explicit DB query map to guarantee matches against seeded rows.
    if finding.barrier_type != "COMPLIANT":
        db_query = _BARRIER_DB_QUERY.get(
            finding.barrier_type,
            finding.barrier_type.replace("_", " ").lower(),
        )
        ada_standard = lookup_ada_standard(db_query)
        location_hint = f"({latitude:.4f}, {longitude:.4f})"
        jurisdiction = find_jurisdiction_contact(location_hint)
    else:
        ada_standard = []
        jurisdiction = find_jurisdiction_contact("milwaukee")

    # Step 5 — assemble
    return HazardAssessment(
        image_path=str(image_path),
        latitude=latitude,
        longitude=longitude,
        gps_source=gps_source,
        finding=finding,
        ada_standard=ada_standard,
        jurisdiction=jurisdiction,
    )
