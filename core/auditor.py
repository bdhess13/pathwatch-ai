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
PATHWATCH_MODEL   Ollama model tag to use (default: ``qwen2.5vl:3b``).
"""

import base64
import io
import logging
import os
import re
from pathlib import Path

import httpx
from pydantic import ValidationError

from core.ingest import extract_gps_from_image
from core.schemas import HazardAssessment, VisionFinding
from mcp_server.server import find_jurisdiction_contact, lookup_ada_standard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OLLAMA_BASE_URL: str = "http://localhost:11434"
_DEFAULT_MODEL: str = "qwen2.5vl:3b"

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
    "HEAVED_OR_CRACKED_PAVEMENT": "sidewalk",
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
    "If the sidewalk has visible cracks, uplifted concrete slabs, vertical lip "
    "displacement, missing curb ramps, missing yellow tactile domes, or "
    "obstructions, you MUST classify it as the matching hazard "
    "(`HEAVED_OR_CRACKED_PAVEMENT`, `MISSING_CURB_RAMP`, "
    "`MISSING_TACTILE_PAVING`, or `PATH_OBSTRUCTION`) and NEVER `COMPLIANT`. "
    "Use `severity: NONE` ONLY when `barrier_type` is `COMPLIANT`. For any hazard "
    "(`HEAVED_OR_CRACKED_PAVEMENT`, `MISSING_CURB_RAMP`, `MISSING_TACTILE_PAVING`, "
    "`PATH_OBSTRUCTION`), `severity` MUST be `HIGH`, `MEDIUM`, or `LOW`. "
    "For description, write 1–2 concise, factual sentences describing the "
    "physical sidewalk, curb ramp, tactile paving, or obstruction visible "
    "in the photo. Never repeat sentences or mention JSON filenames. "
    "For recommended_action, write 1 concrete physical remediation step, "
    "such as installing a truncated dome tactile warning surface at a ramp "
    "threshold or grinding an uplifted concrete joint to eliminate a trip hazard. "
    "Return ONLY valid JSON matching the schema — no markdown, no explanation."
)

_DEFAULT_ACTIONS: dict[str, str] = {
    "MISSING_CURB_RAMP": "Install a compliant curb ramp at the crossing.",
    "MISSING_TACTILE_PAVING": (
        "Install truncated dome tactile warning surface at the ramp threshold."
    ),
    "HEAVED_OR_CRACKED_PAVEMENT": (
        "Grind or replace the uplifted concrete to create a flush, stable walking surface."
    ),
    "PATH_OBSTRUCTION": "Remove the obstruction and keep the accessible route clear.",
    "COMPLIANT": "Maintain the sidewalk and curb ramp in their current accessible condition.",
}

# Maximum pixel dimension for the image sent to the model.
_MAX_IMAGE_PX: int = 1024


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sanitize_finding(finding: VisionFinding) -> VisionFinding:
    """Normalize contradictory findings and clean descriptions/actions."""
    barrier_type = finding.barrier_type
    severity = finding.severity
    if barrier_type == "COMPLIANT" and severity in {"HIGH", "MEDIUM", "LOW"}:
        barrier_type = "HEAVED_OR_CRACKED_PAVEMENT"
    if barrier_type == "COMPLIANT":
        severity = "NONE"
    elif severity == "NONE":
        high_severity_terms = ("uplifted", "heaved", "missing", "blocked")
        if (
            barrier_type in {"MISSING_CURB_RAMP", "PATH_OBSTRUCTION"}
            or any(term in finding.description.casefold() for term in high_severity_terms)
        ):
            severity = "HIGH"
        else:
            severity = "MEDIUM"

    sentences = re.split(r"(?<=[.!?])\s+", finding.description.strip())
    unique_sentences: list[str] = []
    previous_sentence: str | None = None
    for sentence in sentences:
        normalized = " ".join(sentence.split()).casefold()
        if normalized and normalized != previous_sentence:
            unique_sentences.append(sentence)
        previous_sentence = normalized
    description = " ".join(unique_sentences)

    action = (finding.recommended_action or "").strip()
    normalized_action = " ".join(action.split()).casefold()
    normalized_description = " ".join(description.split()).casefold()
    if (
        not normalized_action
        or ".json" in normalized_action
        or ".jpg" in normalized_action
        or (
            normalized_description
            and normalized_description in normalized_action
        )
    ):
        action = _DEFAULT_ACTIONS[barrier_type]

    return finding.model_copy(
        update={
            "barrier_type": barrier_type,
            "severity": severity,
            "description": description,
            "recommended_action": action,
        }
    )


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

def audit_image(
    image_path: Path,
    model: str | None = None,
    override_lat: float | None = None,
    override_lon: float | None = None,
) -> HazardAssessment:
    """Full single-image audit pipeline.

    Steps
    -----
    1. Use supplied coordinates, or extract GPS from EXIF (or use fallback coordinates).
    2. Resize & base64-encode the image.
    3. Send to local Ollama; validate response as ``VisionFinding``.
    4. Enrich with ADA standards and jurisdiction routing via MCP server functions.
    5. Return a ``HazardAssessment``.

    Args:
        image_path: Path to the image file to audit.
        model:      Ollama model tag override.  Defaults to the value of the
                    ``PATHWATCH_MODEL`` env-var, or ``qwen2.5vl:3b``.
        override_lat: Optional latitude used when image GPS metadata is absent.
        override_lon: Optional longitude used when image GPS metadata is absent.

    Returns:
        A fully populated ``HazardAssessment`` instance.

    Raises:
        RuntimeError: If Ollama is unreachable.
        pydantic.ValidationError: If the VLM response cannot be parsed.
    """
    resolved_model = model or os.getenv("PATHWATCH_MODEL", _DEFAULT_MODEL)
    image_path = Path(image_path)

    # Step 1 — GPS
    latitude, longitude, gps_source = extract_gps_from_image(
        image_path,
        override_lat=override_lat,
        override_lon=override_lon,
    )
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
    finding = _sanitize_finding(_call_ollama(image_b64, resolved_model))
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
