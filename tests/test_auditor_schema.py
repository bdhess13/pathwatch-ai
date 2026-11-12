"""
tests/test_auditor_schema.py
============================
Unit tests for core/schemas.py Pydantic models.

Validates:
- VisionFinding accepts all valid barrier_type / severity combinations.
- VisionFinding rejects invalid enum values.
- HazardAssessment round-trips correctly with all required fields.
- HazardAssessment defaults (timestamp, ada_standard list, jurisdiction dict).
- gps_source only accepts "exif" or "fallback".
"""

import pytest
from pydantic import ValidationError

from core.schemas import HazardAssessment, VisionFinding


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_finding(**overrides) -> dict:
    base = {
        "barrier_type": "HEAVED_OR_CRACKED_PAVEMENT",
        "severity": "HIGH",
        "confidence": 0.92,
        "description": "Large crack spanning the full sidewalk width.",
        "recommended_action": "Mill and resurface affected panel.",
    }
    base.update(overrides)
    return base


def _valid_assessment(**overrides) -> dict:
    base = {
        "image_path": "/tmp/test.jpg",
        "latitude": 43.0766,
        "longitude": -87.8812,
        "gps_source": "fallback",
        "finding": _valid_finding(),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# VisionFinding — valid inputs
# ---------------------------------------------------------------------------

class TestVisionFindingValid:
    @pytest.mark.parametrize("barrier_type", [
        "MISSING_CURB_RAMP",
        "MISSING_TACTILE_PAVING",
        "HEAVED_OR_CRACKED_PAVEMENT",
        "PATH_OBSTRUCTION",
        "COMPLIANT",
    ])
    def test_all_barrier_types(self, barrier_type):
        f = VisionFinding(**_valid_finding(barrier_type=barrier_type))
        assert f.barrier_type == barrier_type

    @pytest.mark.parametrize("severity", ["HIGH", "MEDIUM", "LOW", "NONE"])
    def test_all_severities(self, severity):
        f = VisionFinding(**_valid_finding(severity=severity))
        assert f.severity == severity

    def test_confidence_bounds(self):
        VisionFinding(**_valid_finding(confidence=0.0))
        VisionFinding(**_valid_finding(confidence=1.0))
        VisionFinding(**_valid_finding(confidence=0.5))

    def test_model_json_schema_is_dict(self):
        schema = VisionFinding.model_json_schema()
        assert isinstance(schema, dict)
        assert "properties" in schema

    def test_round_trip_json(self):
        original = VisionFinding(**_valid_finding())
        restored = VisionFinding.model_validate_json(original.model_dump_json())
        assert original == restored


# ---------------------------------------------------------------------------
# VisionFinding — invalid inputs
# ---------------------------------------------------------------------------

class TestVisionFindingInvalid:
    def test_bad_barrier_type(self):
        with pytest.raises(ValidationError):
            VisionFinding(**_valid_finding(barrier_type="ALIEN_INVASION"))

    def test_bad_severity(self):
        with pytest.raises(ValidationError):
            VisionFinding(**_valid_finding(severity="CRITICAL"))

    def test_confidence_above_1(self):
        with pytest.raises(ValidationError):
            VisionFinding(**_valid_finding(confidence=1.1))

    def test_confidence_below_0(self):
        with pytest.raises(ValidationError):
            VisionFinding(**_valid_finding(confidence=-0.01))

    def test_missing_required_field(self):
        data = _valid_finding()
        del data["barrier_type"]
        with pytest.raises(ValidationError):
            VisionFinding(**data)


# ---------------------------------------------------------------------------
# HazardAssessment — valid inputs
# ---------------------------------------------------------------------------

class TestHazardAssessmentValid:
    def test_minimal_construction(self):
        ha = HazardAssessment(**_valid_assessment())
        assert ha.gps_source == "fallback"
        assert ha.latitude == pytest.approx(43.0766)
        assert ha.longitude == pytest.approx(-87.8812)
        assert ha.ada_standard == []
        assert ha.jurisdiction == {}
        assert ha.timestamp is not None

    def test_gps_source_exif(self):
        ha = HazardAssessment(**_valid_assessment(gps_source="exif"))
        assert ha.gps_source == "exif"

    def test_with_enrichment(self):
        ha = HazardAssessment(**_valid_assessment(
            ada_standard=[{"rule_code": "R304.2.1"}],
            jurisdiction={"jurisdiction_name": "City of Milwaukee — Public Works (311)"},
        ))
        assert ha.ada_standard[0]["rule_code"] == "R304.2.1"
        assert "Milwaukee" in ha.jurisdiction["jurisdiction_name"]

    def test_finding_is_embedded(self):
        ha = HazardAssessment(**_valid_assessment())
        assert isinstance(ha.finding, VisionFinding)
        assert ha.finding.barrier_type == "HEAVED_OR_CRACKED_PAVEMENT"

    def test_round_trip_json(self):
        original = HazardAssessment(**_valid_assessment())
        restored = HazardAssessment.model_validate_json(original.model_dump_json())
        assert original.image_path == restored.image_path
        assert original.finding == restored.finding

    def test_timestamp_is_utc(self):
        from datetime import timezone
        ha = HazardAssessment(**_valid_assessment())
        assert ha.timestamp.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# HazardAssessment — invalid inputs
# ---------------------------------------------------------------------------

class TestHazardAssessmentInvalid:
    def test_bad_gps_source(self):
        with pytest.raises(ValidationError):
            HazardAssessment(**_valid_assessment(gps_source="manual"))

    def test_missing_image_path(self):
        data = _valid_assessment()
        del data["image_path"]
        with pytest.raises(ValidationError):
            HazardAssessment(**data)
