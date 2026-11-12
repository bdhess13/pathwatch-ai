"""
core/schemas.py
===============
PathWatch AI — Pydantic v2 data models.

All VLM responses and enriched hazard records flow through these schemas,
satisfying AGENTS.md guardrail 2 (structured vision outputs only).
"""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations (Literal unions so JSON schema stays tight)
# ---------------------------------------------------------------------------

BarrierType = Literal[
    "MISSING_CURB_RAMP",
    "MISSING_TACTILE_PAVING",
    "HEAVED_OR_CRACKED_PAVEMENT",
    "PATH_OBSTRUCTION",
    "COMPLIANT",
]

Severity = Literal["HIGH", "MEDIUM", "LOW", "NONE"]


# ---------------------------------------------------------------------------
# VisionFinding — raw validated output from Ollama
# ---------------------------------------------------------------------------

class VisionFinding(BaseModel):
    """Structured output produced by the local VLM.

    This schema is passed directly to Ollama's ``format`` parameter so the
    model is constrained to emit valid JSON that Pydantic can parse.
    """

    barrier_type: BarrierType = Field(
        ...,
        description="The primary accessibility barrier category detected in the image.",
    )
    severity: Severity = Field(
        ...,
        description=(
            "How urgently this barrier needs repair: "
            "HIGH = immediate safety risk, MEDIUM = impedes mobility, "
            "LOW = minor non-compliance, NONE = no barrier detected."
        ),
    )
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        ...,
        description="Model confidence in the classification, 0.0–1.0.",
    )
    description: str = Field(
        ...,
        description="One-sentence plain-English description of the detected condition.",
    )
    recommended_action: str = Field(
        ...,
        description="Concise repair or remediation action for a facilities team.",
    )


# ---------------------------------------------------------------------------
# HazardAssessment — enriched record (VisionFinding + GPS + ADA + jurisdiction)
# ---------------------------------------------------------------------------

class HazardAssessment(BaseModel):
    """Fully enriched hazard record stored in the database and shown on the map.

    Combines the raw VLM finding with GPS provenance, ADA/PROWAG rule codes
    from the MCP server, and 311/facilities routing — satisfying guardrails
    2, 3, and 4 simultaneously.
    """

    # --- Image provenance ---
    image_path: str = Field(..., description="Absolute or relative path to the source image.")
    latitude: float = Field(..., description="Decimal-degree latitude of the hazard.")
    longitude: float = Field(..., description="Decimal-degree longitude of the hazard.")
    gps_source: Literal["exif", "fallback"] = Field(
        ...,
        description=(
            "'exif' when GPS was read from image metadata; "
            "'fallback' when the default campus coordinates were used."
        ),
    )

    # --- VLM classification (embedded) ---
    finding: VisionFinding

    # --- ADA/PROWAG grounding (from MCP server) ---
    ada_standard: list[dict] = Field(
        default_factory=list,
        description="Matching ADA/PROWAG rule records returned by lookup_ada_standard.",
    )

    # --- Jurisdiction routing (from MCP server) ---
    jurisdiction: dict = Field(
        default_factory=dict,
        description="311 / facilities-repair contact returned by find_jurisdiction_contact.",
    )

    # --- Audit timestamp ---
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the audit was performed.",
    )
