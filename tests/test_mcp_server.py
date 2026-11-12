"""
tests/test_mcp_server.py
========================
Integration-style unit tests for mcp_server/server.py.

Strategy
--------
- Tests run directly against the live ``pathwatch.db`` seeded by
  ``mcp_server/ada_seed.py``.  The DB must exist before running the suite;
  ``setup.sh`` (or ``python mcp_server/ada_seed.py``) seeds it.
- All database I/O is local SQLite — zero network access.
- We call the public Python functions (``lookup_ada_standard``,
  ``find_jurisdiction_contact``) directly, not via MCP protocol, so no
  server process is needed.
"""

import pytest

from mcp_server.server import find_jurisdiction_contact, lookup_ada_standard


# ---------------------------------------------------------------------------
# lookup_ada_standard — happy paths
# ---------------------------------------------------------------------------

class TestLookupAdaStandard:
    """Verify that seeded ADA/PROWAG rules are retrievable via keyword search."""

    def test_curb_ramp_returns_results(self):
        results = lookup_ada_standard("curb ramp")
        assert len(results) > 0, "Expected at least one curb ramp rule"

    def test_result_has_required_keys(self):
        results = lookup_ada_standard("curb ramp")
        required_keys = {"rule_code", "category", "barrier_type", "description",
                         "threshold", "citation"}
        for row in results:
            assert required_keys.issubset(row.keys()), (
                f"Row missing keys: {required_keys - row.keys()}"
            )

    def test_detectable_warning_returns_results(self):
        results = lookup_ada_standard("detectable warning")
        assert len(results) > 0

    def test_surface_returns_results(self):
        results = lookup_ada_standard("surface")
        assert len(results) > 0

    def test_protruding_object_returns_results(self):
        results = lookup_ada_standard("protruding object")
        assert len(results) > 0

    def test_curb_ramp_contains_prowag_rule(self):
        results = lookup_ada_standard("curb ramp")
        rule_codes = [r["rule_code"] for r in results]
        # The seed includes PROWAG-R304.2.1 (running slope) for curb ramps
        assert any(code.startswith("PROWAG-R304") for code in rule_codes), (
            f"Expected a PROWAG-R304.x rule; got: {rule_codes}"
        )

    def test_detectable_warning_contains_prowag_r305(self):
        results = lookup_ada_standard("detectable warning")
        rule_codes = [r["rule_code"] for r in results]
        assert any(code.startswith("PROWAG-R305") for code in rule_codes), (
            f"Expected a PROWAG-R305.x rule; got: {rule_codes}"
        )

    def test_unknown_barrier_returns_empty_list(self):
        results = lookup_ada_standard("XYZZY_NONEXISTENT_HAZARD")
        assert results == []

    def test_search_is_case_insensitive(self):
        lower = lookup_ada_standard("curb ramp")
        upper = lookup_ada_standard("CURB RAMP")
        mixed = lookup_ada_standard("Curb Ramp")
        assert len(lower) == len(upper) == len(mixed), (
            "Case sensitivity mismatch in lookup_ada_standard"
        )

    def test_returns_list_of_dicts(self):
        results = lookup_ada_standard("surface")
        assert isinstance(results, list)
        assert all(isinstance(r, dict) for r in results)

    def test_citation_is_nonempty_string(self):
        results = lookup_ada_standard("curb ramp")
        for row in results:
            assert isinstance(row["citation"], str)
            assert len(row["citation"]) > 0

    def test_rule_code_values(self):
        """Spot-check that at least one exact seeded rule code is present."""
        results = lookup_ada_standard("curb ramp")
        rule_codes = {r["rule_code"] for r in results}
        assert "PROWAG-R304.2.1" in rule_codes

    @pytest.mark.parametrize("query,expected_prefix", [
        ("curb ramp", "PROWAG-R304"),
        ("detectable warning", "PROWAG-R305"),
        ("sidewalk", "PROWAG-R302"),
        ("protruding object", "ADA-2010-4.4"),
        ("surface firmness", "ADA-2010-4.5"),
    ])
    def test_barrier_query_map_coverage(self, query, expected_prefix):
        """Each auditor._BARRIER_DB_QUERY value must return at least one rule."""
        results = lookup_ada_standard(query)
        assert any(r["rule_code"].startswith(expected_prefix) for r in results), (
            f"Query '{query}' did not return a rule starting with '{expected_prefix}'. "
            f"Got: {[r['rule_code'] for r in results]}"
        )


# ---------------------------------------------------------------------------
# find_jurisdiction_contact — happy paths
# ---------------------------------------------------------------------------

class TestFindJurisdictionContact:
    """Verify that jurisdiction routing returns valid Milwaukee / UWM records."""

    def test_milwaukee_hint_returns_dict(self):
        result = find_jurisdiction_contact("milwaukee")
        assert isinstance(result, dict)

    def test_result_has_required_keys(self):
        result = find_jurisdiction_contact("milwaukee")
        required_keys = {
            "jurisdiction_id", "jurisdiction_name", "phone", "email",
            "portal_url", "notes",
        }
        assert required_keys.issubset(result.keys()), (
            f"Missing keys: {required_keys - result.keys()}"
        )

    def test_milwaukee_routes_to_311(self):
        result = find_jurisdiction_contact("milwaukee")
        assert result["jurisdiction_id"] == "milwaukee_311"

    def test_uwm_keyword_routes_to_facilities(self):
        result = find_jurisdiction_contact("UWM campus near Kenwood Ave")
        assert result["jurisdiction_id"] == "uwm_facilities"

    def test_campus_keyword_routes_to_facilities(self):
        result = find_jurisdiction_contact("near the campus library")
        assert result["jurisdiction_id"] == "uwm_facilities"

    def test_university_keyword_routes_to_facilities(self):
        result = find_jurisdiction_contact("University of Wisconsin")
        assert result["jurisdiction_id"] == "uwm_facilities"

    def test_unknown_hint_defaults_to_milwaukee_311(self):
        result = find_jurisdiction_contact("(43.0766, -87.8812)")
        assert result["jurisdiction_id"] == "milwaukee_311"

    def test_milwaukee_phone_is_nonempty(self):
        result = find_jurisdiction_contact("milwaukee")
        assert len(result["phone"]) > 0

    def test_milwaukee_portal_url_contains_http(self):
        result = find_jurisdiction_contact("milwaukee")
        assert result["portal_url"].startswith("http")

    def test_uwm_email_contains_at_sign(self):
        result = find_jurisdiction_contact("uwm")
        assert "@" in result["email"]

    def test_coordinates_hint_returns_milwaukee_311(self):
        """GPS-derived hints like '(43.0766, -87.8812)' should resolve to Milwaukee 311."""
        result = find_jurisdiction_contact("(43.0800, -87.8900)")
        assert result["jurisdiction_id"] == "milwaukee_311"

    def test_jurisdiction_name_is_nonempty(self):
        result = find_jurisdiction_contact("milwaukee downtown")
        assert len(result["jurisdiction_name"]) > 0

    @pytest.mark.parametrize("hint,expected_id", [
        ("milwaukee", "milwaukee_311"),
        ("UWM", "uwm_facilities"),
        ("campus", "uwm_facilities"),
        ("university", "uwm_facilities"),
        ("random street hint", "milwaukee_311"),
    ])
    def test_routing_matrix(self, hint, expected_id):
        result = find_jurisdiction_contact(hint)
        assert result["jurisdiction_id"] == expected_id
