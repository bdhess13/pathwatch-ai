"""
tests/test_auditor_schema.py
============================
Offline unit tests covering the full audit pipeline.

Sections
--------
1. VisionFinding — Pydantic schema validation (valid & invalid inputs).
2. HazardAssessment — Pydantic schema validation (valid & invalid inputs).
3. audit_image (mocked) — patches httpx.post so Ollama is never called;
   exercises the full pipeline:
     • GPS fallback path
     • VisionFinding Pydantic round-trip
     • PROWAG enrichment via lookup_ada_standard
     • 311 jurisdiction routing via find_jurisdiction_contact
     • Markdown export (export_311_ticket_markdown)
     • RFC 7946 GeoJSON export (export_geojson)
4. Markdown export — standalone content/format assertions.
5. GeoJSON export — RFC 7946 structural assertions.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from core.schemas import HazardAssessment, VisionFinding


# ===========================================================================
# Helpers
# ===========================================================================

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


def _make_assessment(**overrides) -> HazardAssessment:
    return HazardAssessment(**_valid_assessment(**overrides))


# ===========================================================================
# 1. VisionFinding — valid inputs
# ===========================================================================

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

    def test_recommended_action_is_optional_and_preserved(self):
        data = _valid_finding()
        del data["recommended_action"]

        assert VisionFinding(**data).recommended_action is None
        assert VisionFinding(**_valid_finding()).recommended_action == (
            "Mill and resurface affected panel."
        )

    def test_round_trip_json(self):
        original = VisionFinding(**_valid_finding())
        restored = VisionFinding.model_validate_json(original.model_dump_json())
        assert original == restored


# ===========================================================================
# 2. VisionFinding — invalid inputs
# ===========================================================================

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


class TestSanitizeFinding:
    @pytest.mark.parametrize("severity", ["HIGH", "MEDIUM", "LOW"])
    def test_compliant_nonzero_severity_becomes_surface_hazard(self, severity):
        from core.auditor import _sanitize_finding

        finding = VisionFinding(**_valid_finding(
            barrier_type="COMPLIANT",
            severity=severity,
            recommended_action="",
        ))

        sanitized = _sanitize_finding(finding)

        assert sanitized.barrier_type == "HEAVED_OR_CRACKED_PAVEMENT"
        assert sanitized.severity == severity
        assert sanitized.recommended_action == (
            "Grind or replace the uplifted concrete to create a flush, stable walking surface."
        )

    def test_compliant_none_severity_stays_compliant(self):
        from core.auditor import _sanitize_finding

        finding = VisionFinding(**_valid_finding(
            barrier_type="COMPLIANT",
            severity="NONE",
        ))

        sanitized = _sanitize_finding(finding)

        assert sanitized.barrier_type == "COMPLIANT"
        assert sanitized.severity == "NONE"

    @pytest.mark.parametrize(
        ("barrier_type", "description", "expected_severity"),
        [
            ("MISSING_CURB_RAMP", "No additional details.", "HIGH"),
            ("HEAVED_OR_CRACKED_PAVEMENT", "An uplifted slab.", "HIGH"),
            ("HEAVED_OR_CRACKED_PAVEMENT", "A rough surface.", "MEDIUM"),
        ],
    )
    def test_noncompliant_none_severity_is_upgraded(
        self, barrier_type, description, expected_severity
    ):
        from core.auditor import _sanitize_finding

        finding = VisionFinding(**_valid_finding(
            barrier_type=barrier_type,
            severity="NONE",
            description=description,
        ))

        sanitized = _sanitize_finding(finding)

        assert sanitized.severity == expected_severity

    def test_deduplicates_consecutive_description_sentences(self):
        from core.auditor import _sanitize_finding

        finding = VisionFinding(**_valid_finding(
            description="A raised joint spans the path. A raised joint spans the path. "
            "The adjacent panel is cracked.",
        ))

        sanitized = _sanitize_finding(finding)

        assert sanitized.description == (
            "A raised joint spans the path. The adjacent panel is cracked."
        )

    @pytest.mark.parametrize(
        "action",
        [None, "", "repair-details.json", "A crack blocks the route."],
    )
    def test_replaces_empty_filename_or_description_action(self, action):
        from core.auditor import _sanitize_finding

        finding = VisionFinding(**_valid_finding(
            barrier_type="PATH_OBSTRUCTION",
            description="A crack blocks the route.",
            recommended_action=action,
        ))

        sanitized = _sanitize_finding(finding)

        assert sanitized.recommended_action == (
            "Remove the obstruction and keep the accessible route clear."
        )


# ===========================================================================
# 3. HazardAssessment — valid inputs
# ===========================================================================

class TestHazardAssessmentValid:
    def test_minimal_construction(self):
        ha = _make_assessment()
        assert ha.gps_source == "fallback"
        assert ha.latitude == pytest.approx(43.0766)
        assert ha.longitude == pytest.approx(-87.8812)
        assert ha.ada_standard == []
        assert ha.jurisdiction == {}
        assert ha.timestamp is not None

    def test_gps_source_exif(self):
        ha = _make_assessment(gps_source="exif")
        assert ha.gps_source == "exif"

    @pytest.mark.parametrize("gps_source", ["manual", "browser"])
    def test_gps_source_user_coordinates(self, gps_source):
        ha = _make_assessment(gps_source=gps_source)
        assert ha.gps_source == gps_source

    def test_with_enrichment(self):
        ha = _make_assessment(
            ada_standard=[{"rule_code": "R304.2.1"}],
            jurisdiction={"jurisdiction_name": "City of Milwaukee — Public Works (311)"},
        )
        assert ha.ada_standard[0]["rule_code"] == "R304.2.1"
        assert "Milwaukee" in ha.jurisdiction["jurisdiction_name"]

    def test_finding_is_embedded(self):
        ha = _make_assessment()
        assert isinstance(ha.finding, VisionFinding)
        assert ha.finding.barrier_type == "HEAVED_OR_CRACKED_PAVEMENT"

    def test_round_trip_json(self):
        original = _make_assessment()
        restored = HazardAssessment.model_validate_json(original.model_dump_json())
        assert original.image_path == restored.image_path
        assert original.finding == restored.finding

    def test_timestamp_is_utc(self):
        ha = _make_assessment()
        assert ha.timestamp.tzinfo == timezone.utc


# ===========================================================================
# 4. HazardAssessment — invalid inputs
# ===========================================================================

class TestHazardAssessmentInvalid:
    def test_bad_gps_source(self):
        with pytest.raises(ValidationError):
            HazardAssessment(**_valid_assessment(gps_source="unknown"))

    def test_missing_image_path(self):
        data = _valid_assessment()
        del data["image_path"]
        with pytest.raises(ValidationError):
            HazardAssessment(**data)


# ===========================================================================
# 5. audit_image — mocked Ollama (offline pipeline smoke tests)
# ===========================================================================

def _mock_ollama_response(barrier_type: str, severity: str, confidence: float = 0.88):
    """Build a fake httpx.Response that mimics Ollama's /api/chat JSON reply."""
    finding_json = json.dumps({
        "barrier_type": barrier_type,
        "severity": severity,
        "confidence": confidence,
        "description": "Synthetic test finding.",
        "recommended_action": "Synthetic test action.",
    })
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"message": {"content": finding_json}}
    return mock_resp


def _fake_image(tmp_path: Path) -> Path:
    """Create a minimal JPEG on disk so PIL.Image.open succeeds."""
    from PIL import Image
    img = Image.new("RGB", (32, 32), color=(100, 150, 200))
    p = tmp_path / "fake.jpg"
    img.save(p, format="JPEG")
    return p


class TestAuditImageMocked:
    """Calls audit_image with httpx.post patched — no Ollama process needed."""

    def test_returns_hazard_assessment(self, tmp_path):
        from core.auditor import audit_image
        img = _fake_image(tmp_path)
        mock_resp = _mock_ollama_response("MISSING_CURB_RAMP", "HIGH")
        with patch("httpx.post", return_value=mock_resp):
            result = audit_image(img)
        assert isinstance(result, HazardAssessment)

    def test_default_model_and_prompt_require_hazard_classification(
        self, tmp_path, monkeypatch
    ):
        from core.auditor import audit_image
        img = _fake_image(tmp_path)
        mock_resp = _mock_ollama_response("MISSING_CURB_RAMP", "HIGH")
        monkeypatch.delenv("PATHWATCH_MODEL", raising=False)
        with patch("httpx.post", return_value=mock_resp) as post:
            audit_image(img)

        payload = post.call_args.kwargs["json"]
        assert payload["model"] == "qwen2.5vl:3b"
        user_prompt = next(
            message["content"]
            for message in payload["messages"]
            if message["role"] == "user"
        )
        assert (
            "If the sidewalk has visible cracks, uplifted concrete slabs, vertical lip "
            "displacement, missing curb ramps, missing yellow tactile domes, or "
            "obstructions, you MUST classify it as the matching hazard "
            "(`HEAVED_OR_CRACKED_PAVEMENT`, `MISSING_CURB_RAMP`, "
            "`MISSING_TACTILE_PAVING`, or `PATH_OBSTRUCTION`) and NEVER `COMPLIANT`."
        ) in user_prompt
        assert (
            "Use `severity: NONE` ONLY when `barrier_type` is `COMPLIANT`. For any "
            "hazard (`HEAVED_OR_CRACKED_PAVEMENT`, `MISSING_CURB_RAMP`, "
            "`MISSING_TACTILE_PAVING`, `PATH_OBSTRUCTION`), `severity` MUST be "
            "`HIGH`, `MEDIUM`, or `LOW`."
        ) in user_prompt

    def test_pathwatch_model_environment_override(self, tmp_path, monkeypatch):
        from core.auditor import audit_image
        img = _fake_image(tmp_path)
        mock_resp = _mock_ollama_response("MISSING_CURB_RAMP", "HIGH")
        monkeypatch.setenv("PATHWATCH_MODEL", "custom-local-model")
        with patch("httpx.post", return_value=mock_resp) as post:
            audit_image(img)

        assert post.call_args.kwargs["json"]["model"] == "custom-local-model"

    def test_gps_fallback_on_no_exif(self, tmp_path):
        from core.auditor import audit_image
        img = _fake_image(tmp_path)
        mock_resp = _mock_ollama_response("PATH_OBSTRUCTION", "MEDIUM")
        with patch("httpx.post", return_value=mock_resp):
            result = audit_image(img)
        # A plain JPEG with no GPS EXIF must trigger the fallback path
        assert result.gps_source == "fallback"
        assert result.latitude == pytest.approx(43.0766)
        assert result.longitude == pytest.approx(-87.8812)

    def test_manual_coordinates_reach_assessment_when_exif_is_absent(self, tmp_path):
        from core.auditor import audit_image

        img = _fake_image(tmp_path)
        mock_resp = _mock_ollama_response("PATH_OBSTRUCTION", "MEDIUM")
        with patch("httpx.post", return_value=mock_resp):
            result = audit_image(
                img,
                override_lat=40.7128,
                override_lon=-74.006,
            )

        assert (result.latitude, result.longitude, result.gps_source) == (
            40.7128,
            -74.006,
            "manual",
        )

    def test_vision_finding_pydantic_validated(self, tmp_path):
        from core.auditor import audit_image
        img = _fake_image(tmp_path)
        mock_resp = _mock_ollama_response("HEAVED_OR_CRACKED_PAVEMENT", "HIGH", 0.95)
        with patch("httpx.post", return_value=mock_resp):
            result = audit_image(img)
        assert isinstance(result.finding, VisionFinding)
        assert result.finding.barrier_type == "HEAVED_OR_CRACKED_PAVEMENT"
        assert result.finding.severity == "HIGH"
        assert result.finding.confidence == pytest.approx(0.95)

    def test_prowag_enrichment_attached(self, tmp_path):
        """MISSING_CURB_RAMP must trigger lookup_ada_standard and attach rules."""
        from core.auditor import audit_image
        img = _fake_image(tmp_path)
        mock_resp = _mock_ollama_response("MISSING_CURB_RAMP", "HIGH")
        with patch("httpx.post", return_value=mock_resp):
            result = audit_image(img)
        # ada_standard must be populated (seeded DB has PROWAG-R304.x curb ramp rules)
        assert isinstance(result.ada_standard, list)
        assert len(result.ada_standard) > 0
        rule_codes = [r["rule_code"] for r in result.ada_standard]
        assert any(code.startswith("PROWAG-R304") for code in rule_codes), (
            f"Expected PROWAG-R304.x rules; got {rule_codes}"
        )

    def test_detectable_warning_enrichment(self, tmp_path):
        from core.auditor import audit_image
        img = _fake_image(tmp_path)
        mock_resp = _mock_ollama_response("MISSING_TACTILE_PAVING", "MEDIUM")
        with patch("httpx.post", return_value=mock_resp):
            result = audit_image(img)
        assert len(result.ada_standard) > 0
        rule_codes = [r["rule_code"] for r in result.ada_standard]
        assert any(code.startswith("PROWAG-R305") for code in rule_codes)

    def test_jurisdiction_routing_attached(self, tmp_path):
        from core.auditor import audit_image
        img = _fake_image(tmp_path)
        mock_resp = _mock_ollama_response("MISSING_CURB_RAMP", "HIGH")
        with patch("httpx.post", return_value=mock_resp):
            result = audit_image(img)
        assert isinstance(result.jurisdiction, dict)
        assert "jurisdiction_id" in result.jurisdiction
        # Fallback GPS → non-campus → should route to Milwaukee 311
        assert result.jurisdiction["jurisdiction_id"] == "milwaukee_311"

    def test_compliant_finding_has_no_ada_standard(self, tmp_path):
        """COMPLIANT findings must produce an empty ada_standard list."""
        from core.auditor import audit_image
        img = _fake_image(tmp_path)
        mock_resp = _mock_ollama_response("COMPLIANT", "NONE", 0.99)
        with patch("httpx.post", return_value=mock_resp):
            result = audit_image(img)
        assert result.ada_standard == []

    def test_compliant_nonzero_severity_gets_surface_standards(self, tmp_path):
        from core.auditor import audit_image
        img = _fake_image(tmp_path)
        mock_resp = _mock_ollama_response("COMPLIANT", "HIGH")
        with patch("httpx.post", return_value=mock_resp):
            result = audit_image(img)

        assert result.finding.barrier_type == "HEAVED_OR_CRACKED_PAVEMENT"
        assert result.finding.severity == "HIGH"
        assert any(
            standard["rule_code"].startswith("PROWAG-R302")
            for standard in result.ada_standard
        )

    def test_image_path_preserved(self, tmp_path):
        from core.auditor import audit_image
        img = _fake_image(tmp_path)
        mock_resp = _mock_ollama_response("PATH_OBSTRUCTION", "LOW")
        with patch("httpx.post", return_value=mock_resp):
            result = audit_image(img)
        assert result.image_path == str(img)

    def test_invalid_ollama_json_raises_validation_error(self, tmp_path):
        """If Ollama returns malformed JSON, Pydantic must raise ValidationError."""
        from core.auditor import audit_image
        img = _fake_image(tmp_path)
        bad_resp = MagicMock()
        bad_resp.raise_for_status = MagicMock()
        bad_resp.json.return_value = {"message": {"content": '{"barrier_type": "ALIEN"}'}}
        with patch("httpx.post", return_value=bad_resp):
            with pytest.raises(ValidationError):
                audit_image(img)

    def test_ollama_unreachable_raises_runtime_error(self, tmp_path):
        """ConnectError must surface as RuntimeError (not crash silently)."""
        import httpx
        from core.auditor import audit_image
        img = _fake_image(tmp_path)
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(RuntimeError, match="Ollama"):
                audit_image(img)


# ===========================================================================
# 6. export_311_ticket_markdown — content & format tests
# ===========================================================================

class TestExport311TicketMarkdown:
    def _enriched_assessment(self) -> HazardAssessment:
        return HazardAssessment(
            image_path="/tmp/sidewalk.jpg",
            latitude=43.0766,
            longitude=-87.8812,
            gps_source="fallback",
            finding=VisionFinding(**_valid_finding(
                barrier_type="HEAVED_OR_CRACKED_PAVEMENT",
                severity="HIGH",
                confidence=0.92,
            )),
            ada_standard=[{
                "rule_code": "ADA-2010-4.5.2",
                "category": "sidewalk",
                "barrier_type": "vertical surface discontinuity",
                "description": "Changes in level > ½ inch must be ramped.",
                "threshold": "> ½ in requires ramp",
                "citation": "ADA Standards 2010, Section 4.5.2",
            }],
            jurisdiction={
                "jurisdiction_id": "milwaukee_311",
                "jurisdiction_name": "City of Milwaukee — Public Works (311)",
                "phone": "(414) 286-2489",
                "email": "dpw@milwaukee.gov",
                "portal_url": "https://city.milwaukee.gov/311",
                "notes": "Submit 'Sidewalk / Curb Repair' category.",
            },
        )

    def test_returns_string(self):
        from core.exporter import export_311_ticket_markdown
        md = export_311_ticket_markdown(self._enriched_assessment())
        assert isinstance(md, str)

    def test_contains_barrier_type(self):
        from core.exporter import export_311_ticket_markdown
        md = export_311_ticket_markdown(self._enriched_assessment())
        assert "Heaved Or Cracked Pavement" in md

    def test_contains_severity_label(self):
        from core.exporter import export_311_ticket_markdown
        md = export_311_ticket_markdown(self._enriched_assessment())
        assert "HIGH" in md

    def test_contains_gps_coordinates(self):
        from core.exporter import export_311_ticket_markdown
        md = export_311_ticket_markdown(self._enriched_assessment())
        assert "43.076600" in md
        assert "-87.881200" in md

    def test_contains_gps_source(self):
        from core.exporter import export_311_ticket_markdown
        md = export_311_ticket_markdown(self._enriched_assessment())
        assert "fallback" in md

    def test_contains_prowag_rule_code(self):
        from core.exporter import export_311_ticket_markdown
        md = export_311_ticket_markdown(self._enriched_assessment())
        assert "ADA-2010-4.5.2" in md

    def test_contains_jurisdiction_name(self):
        from core.exporter import export_311_ticket_markdown
        md = export_311_ticket_markdown(self._enriched_assessment())
        assert "City of Milwaukee" in md

    def test_contains_portal_url(self):
        from core.exporter import export_311_ticket_markdown
        md = export_311_ticket_markdown(self._enriched_assessment())
        assert "city.milwaukee.gov/311" in md

    def test_contains_image_path(self):
        from core.exporter import export_311_ticket_markdown
        md = export_311_ticket_markdown(self._enriched_assessment())
        assert "sidewalk.jpg" in md

    def test_starts_with_markdown_heading(self):
        from core.exporter import export_311_ticket_markdown
        md = export_311_ticket_markdown(self._enriched_assessment())
        assert md.startswith("# PathWatch AI")

    def test_compliant_no_ada_citations_block(self):
        from core.exporter import export_311_ticket_markdown
        ha = HazardAssessment(
            image_path="/tmp/ok.jpg",
            latitude=43.0,
            longitude=-87.0,
            gps_source="exif",
            finding=VisionFinding(**_valid_finding(barrier_type="COMPLIANT", severity="NONE")),
            ada_standard=[],
            jurisdiction={
                "jurisdiction_id": "milwaukee_311",
                "jurisdiction_name": "City of Milwaukee — Public Works (311)",
                "phone": "(414) 286-2489",
                "email": "dpw@milwaukee.gov",
                "portal_url": "https://city.milwaukee.gov/311",
                "notes": "Submit request.",
            },
        )
        md = export_311_ticket_markdown(ha)
        assert "COMPLIANT" in md.upper() or "No specific ADA" in md

    def test_remediation_action_is_not_included(self):
        from core.exporter import export_311_ticket_markdown
        md = export_311_ticket_markdown(self._enriched_assessment())
        assert "Recommended Action" not in md
        assert "Mill and resurface affected panel." not in md


# ===========================================================================
# 7. export_geojson — RFC 7946 structural tests
# ===========================================================================

class TestExportGeoJSON:
    def _sample_assessment(self, barrier_type="MISSING_CURB_RAMP", severity="HIGH") -> HazardAssessment:
        return HazardAssessment(
            image_path="/tmp/curb.jpg",
            latitude=43.0766,
            longitude=-87.8812,
            gps_source="fallback",
            finding=VisionFinding(**_valid_finding(
                barrier_type=barrier_type,
                severity=severity,
            )),
            ada_standard=[{
                "rule_code": "PROWAG-R304.2.1",
                "category": "curb_ramp",
                "barrier_type": "curb ramp running slope",
                "description": "Slope ≤ 8.33%.",
                "threshold": "≤ 8.33%",
                "citation": "PROWAG 2023, R304.2.1",
            }],
            jurisdiction={
                "jurisdiction_id": "milwaukee_311",
                "jurisdiction_name": "City of Milwaukee — Public Works (311)",
                "phone": "(414) 286-2489",
                "email": "dpw@milwaukee.gov",
                "portal_url": "https://city.milwaukee.gov/311",
                "notes": "Submit request.",
            },
        )

    def test_returns_dict(self):
        from core.exporter import export_geojson
        result = export_geojson([self._sample_assessment()])
        assert isinstance(result, dict)

    def test_type_is_feature_collection(self):
        from core.exporter import export_geojson
        result = export_geojson([self._sample_assessment()])
        assert result["type"] == "FeatureCollection"

    def test_features_is_list(self):
        from core.exporter import export_geojson
        result = export_geojson([self._sample_assessment()])
        assert isinstance(result["features"], list)

    def test_feature_count_matches_input(self):
        from core.exporter import export_geojson
        assessments = [
            self._sample_assessment("MISSING_CURB_RAMP", "HIGH"),
            self._sample_assessment("HEAVED_OR_CRACKED_PAVEMENT", "MEDIUM"),
        ]
        result = export_geojson(assessments)
        assert len(result["features"]) == 2

    def test_empty_list_gives_empty_features(self):
        from core.exporter import export_geojson
        result = export_geojson([])
        assert result["features"] == []

    def test_feature_type_is_feature(self):
        from core.exporter import export_geojson
        result = export_geojson([self._sample_assessment()])
        assert result["features"][0]["type"] == "Feature"

    def test_geometry_type_is_point(self):
        from core.exporter import export_geojson
        result = export_geojson([self._sample_assessment()])
        geom = result["features"][0]["geometry"]
        assert geom["type"] == "Point"

    def test_geojson_coordinate_order_is_lon_lat(self):
        """RFC 7946 mandates [longitude, latitude] coordinate order."""
        from core.exporter import export_geojson
        result = export_geojson([self._sample_assessment()])
        coords = result["features"][0]["geometry"]["coordinates"]
        # longitude first (negative for Milwaukee), then latitude
        assert coords[0] == pytest.approx(-87.8812)
        assert coords[1] == pytest.approx(43.0766)

    def test_properties_contain_barrier_type(self):
        from core.exporter import export_geojson
        result = export_geojson([self._sample_assessment()])
        props = result["features"][0]["properties"]
        assert props["barrier_type"] == "MISSING_CURB_RAMP"

    def test_properties_contain_severity(self):
        from core.exporter import export_geojson
        result = export_geojson([self._sample_assessment()])
        props = result["features"][0]["properties"]
        assert props["severity"] == "HIGH"

    def test_properties_contain_ada_section(self):
        from core.exporter import export_geojson
        result = export_geojson([self._sample_assessment()])
        props = result["features"][0]["properties"]
        assert "PROWAG-R304.2.1" in props["ada_section"]

    def test_properties_contain_osm_wheelchair_tag(self):
        from core.exporter import export_geojson
        result = export_geojson([self._sample_assessment("MISSING_CURB_RAMP")])
        props = result["features"][0]["properties"]
        assert "wheelchair" in props

    def test_properties_contain_gps_source(self):
        from core.exporter import export_geojson
        result = export_geojson([self._sample_assessment()])
        props = result["features"][0]["properties"]
        assert props["gps_source"] == "fallback"

    def test_is_json_serialisable(self):
        """The returned dict must serialise to valid JSON without error."""
        from core.exporter import export_geojson
        result = export_geojson([self._sample_assessment()])
        serialised = json.dumps(result, default=str)
        reparsed = json.loads(serialised)
        assert reparsed["type"] == "FeatureCollection"

    def test_timestamp_present_in_properties(self):
        from core.exporter import export_geojson
        result = export_geojson([self._sample_assessment()])
        props = result["features"][0]["properties"]
        assert "timestamp" in props
        assert len(props["timestamp"]) > 0
