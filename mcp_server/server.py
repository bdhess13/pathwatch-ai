"""
mcp_server/server.py
====================
PathWatch AI — FastMCP server (stdio transport).

Exposes two MCP tools that ground each detected hazard with verified ADA/
PROWAG rule codes and local 311/facilities-repair routing, satisfying
AGENTS.md architectural guardrail 3.

Usage (stdio, for Bob MCP integration):
    python mcp_server/server.py

The server reads from pathwatch.db in the repo root. Run ada_seed.py first
if the database does not yet exist.
"""

import sqlite3
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_PATH: Path = Path(__file__).resolve().parent.parent / "pathwatch.db"

mcp = FastMCP("pathwatch-mcp")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    """Open a read-only connection to pathwatch.db (raises if DB missing)."""
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"pathwatch.db not found at {DB_PATH}. "
            "Run 'python mcp_server/ada_seed.py' first."
        )
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Tool 1 — lookup_ada_standard
# ---------------------------------------------------------------------------

@mcp.tool()
def lookup_ada_standard(barrier_type: str) -> list[dict[str, Any]]:
    """Look up ADA / PROWAG standards that apply to a detected accessibility barrier.

    Performs a case-insensitive search against the barrier_type and description
    columns in the local pathwatch.db knowledge base.

    Args:
        barrier_type: A short description of the detected hazard, e.g.
            "curb ramp running slope", "missing detectable warning",
            "sidewalk crack", "protruding sign post".

    Returns:
        A list of matching rule dicts, each containing:
            rule_code, category, barrier_type, description, threshold, citation.
        Returns an empty list when no matching rule is found.
    """
    pattern = f"%{barrier_type.lower()}%"
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT rule_code, category, barrier_type, description, threshold, citation
            FROM   ada_standards
            WHERE  LOWER(barrier_type) LIKE ?
               OR  LOWER(description)  LIKE ?
            ORDER BY category, rule_code
            """,
            (pattern, pattern),
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Tool 2 — find_jurisdiction_contact
# ---------------------------------------------------------------------------

@mcp.tool()
def find_jurisdiction_contact(location_hint: str) -> dict[str, Any]:
    """Return 311 / facilities-repair routing info for a given location hint.

    Checks whether the hint refers to a UWM / campus location; otherwise
    defaults to the City of Milwaukee 311 contact.

    Args:
        location_hint: Free-text location description, e.g. "UWM campus",
            "near Kenwood Ave", "Milwaukee downtown", "University of Wisconsin".

    Returns:
        A single jurisdiction dict containing:
            jurisdiction_id, jurisdiction_name, phone, email,
            portal_url, notes.
    """
    # Route UWM / campus keywords to UWM Facilities Management
    hint_lower = location_hint.lower()
    is_campus = any(kw in hint_lower for kw in ("uwm", "campus", "university"))
    jurisdiction_id = "uwm_facilities" if is_campus else "milwaukee_311"

    with _get_conn() as conn:
        row = conn.execute(
            """
            SELECT jurisdiction_id, jurisdiction_name, phone, email,
                   portal_url, notes
            FROM   jurisdiction_contacts
            WHERE  jurisdiction_id = ?
            """,
            (jurisdiction_id,),
        ).fetchone()

    if row is None:
        # Fallback: return Milwaukee 311 raw values if DB row is missing
        return {
            "jurisdiction_id": "milwaukee_311",
            "jurisdiction_name": "City of Milwaukee — Public Works (311)",
            "phone": "(414) 286-2489",
            "email": "dpw@milwaukee.gov",
            "portal_url": "https://city.milwaukee.gov/311",
            "notes": "Submit 'Sidewalk / Curb Repair' category with address and photo.",
        }

    return dict(row)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
