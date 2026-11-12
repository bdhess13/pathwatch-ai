"""
core/exporter.py
================
PathWatch AI — Phase 3 export functions.

Provides two export targets:

``export_311_ticket_markdown``
    Generates a ready-to-submit municipal 311 / campus facilities repair
    packet in Markdown, citing matched PROWAG/ADA sections, GPS coordinates,
    severity, and jurisdiction submission instructions.

``export_geojson``
    Outputs a valid RFC 7946 GeoJSON FeatureCollection with OpenStreetMap
    accessibility tags (wheelchair, kerb, surface, ada_section).
"""

from __future__ import annotations

from datetime import timezone

from core.schemas import HazardAssessment

# ---------------------------------------------------------------------------
# Severity → human-readable priority label
# ---------------------------------------------------------------------------

_SEVERITY_LABEL: dict[str, str] = {
    "HIGH": "HIGH — Immediate Safety Risk",
    "MEDIUM": "MEDIUM — Impedes Mobility",
    "LOW": "LOW — Minor Non-Compliance",
    "NONE": "NONE — No Barrier Detected",
}

# ---------------------------------------------------------------------------
# BarrierType → OSM tag hints
# ---------------------------------------------------------------------------

_OSM_TAGS: dict[str, dict[str, str]] = {
    "MISSING_CURB_RAMP": {"kerb": "raised", "wheelchair": "no"},
    "MISSING_TACTILE_PAVING": {"tactile_paving": "no", "wheelchair": "limited"},
    "HEAVED_OR_CRACKED_PAVEMENT": {"surface": "poor", "wheelchair": "limited", "smoothness": "bad"},
    "PATH_OBSTRUCTION": {"wheelchair": "no", "access": "restricted"},
    "COMPLIANT": {"wheelchair": "yes", "kerb": "lowered"},
}


# ---------------------------------------------------------------------------
# Public: export_311_ticket_markdown
# ---------------------------------------------------------------------------

def export_311_ticket_markdown(assessment: HazardAssessment) -> str:
    """Generate a ready-to-submit municipal 311 / campus facilities repair packet.

    The returned Markdown string cites matched PROWAG/ADA sections, GPS
    coordinates, severity, and jurisdiction submission instructions.

    Args:
        assessment: A fully populated ``HazardAssessment`` instance.

    Returns:
        A Markdown string suitable for pasting into a 311 submission form or
        attaching as a PDF to a facilities work-order ticket.
    """
    f = assessment.finding
    j = assessment.jurisdiction
    ts = assessment.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    severity_label = _SEVERITY_LABEL.get(f.severity, f.severity)

    # --- ADA/PROWAG citations block ------------------------------------------
    if assessment.ada_standard:
        citations_lines = []
        for rule in assessment.ada_standard:
            citations_lines.append(
                f"- **{rule['rule_code']}** — {rule['description']}\n"
                f"  - *Threshold:* {rule['threshold']}\n"
                f"  - *Citation:* {rule['citation']}"
            )
        citations_block = "\n".join(citations_lines)
    else:
        citations_block = "_No specific ADA/PROWAG rule matched (finding: COMPLIANT)._"

    # --- Jurisdiction submission block ----------------------------------------
    submission_block = (
        f"- **Agency:** {j.get('jurisdiction_name', 'N/A')}\n"
        f"- **Phone:** {j.get('phone', 'N/A')}\n"
        f"- **Email:** {j.get('email', 'N/A')}\n"
        f"- **Portal:** {j.get('portal_url', 'N/A')}\n"
        f"- **Instructions:** {j.get('notes', 'N/A')}"
    )

    return f"""# PathWatch AI — Accessibility Barrier Repair Request

**Generated:** {ts}
**Source Image:** `{assessment.image_path}`
**GPS Coordinates:** {assessment.latitude:.6f}, {assessment.longitude:.6f} *(source: {assessment.gps_source})*

---

## Barrier Summary

| Field | Value |
|---|---|
| **Barrier Type** | {f.barrier_type.replace('_', ' ').title()} |
| **Severity** | {severity_label} |
| **Model Confidence** | {f.confidence:.0%} |
| **Description** | {f.description} |
| **Recommended Action** | {f.recommended_action} |

---

## Applicable ADA / PROWAG Standards

{citations_block}

---

## Jurisdiction Submission Instructions

{submission_block}

---

*This report was generated automatically by PathWatch AI, an offline-first ADA/PROWAG
accessibility auditor. All inference ran locally on device — no cloud services were used.*
"""


# ---------------------------------------------------------------------------
# Public: export_geojson
# ---------------------------------------------------------------------------

def export_geojson(assessments: list[HazardAssessment]) -> dict:
    """Build a valid RFC 7946 GeoJSON FeatureCollection from a list of assessments.

    Each Feature carries OpenStreetMap accessibility tags (wheelchair, kerb,
    surface, smoothness, ada_section) derived from the barrier type and the
    matched ADA/PROWAG rule codes.

    Args:
        assessments: List of ``HazardAssessment`` instances.

    Returns:
        A dict that is directly JSON-serialisable as a GeoJSON FeatureCollection.
    """
    features = []
    for assessment in assessments:
        f = assessment.finding
        osm_base = _OSM_TAGS.get(f.barrier_type, {})

        # Attach all matched rule codes as a comma-separated ada_section tag.
        if assessment.ada_standard:
            rule_codes = ", ".join(r["rule_code"] for r in assessment.ada_standard)
        else:
            rule_codes = ""

        properties = {
            "barrier_type": f.barrier_type,
            "severity": f.severity,
            "confidence": f.confidence,
            "description": f.description,
            "recommended_action": f.recommended_action,
            "image_path": assessment.image_path,
            "gps_source": assessment.gps_source,
            "timestamp": assessment.timestamp.isoformat(),
            "ada_section": rule_codes,
            "jurisdiction": assessment.jurisdiction.get("jurisdiction_name", ""),
            **osm_base,
        }

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [assessment.longitude, assessment.latitude],  # GeoJSON: [lon, lat]
            },
            "properties": properties,
        })

    return {
        "type": "FeatureCollection",
        "features": features,
    }
