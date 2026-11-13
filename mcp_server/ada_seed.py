"""
mcp_server/ada_seed.py
======================
Seeds pathwatch.db (SQLite, repo root) with:
  - ada_standards: ADA/PROWAG curb-ramp and sidewalk rules
  - jurisdiction_contacts: Milwaukee 311 and UWM Facilities routing

Run directly: python mcp_server/ada_seed.py
Idempotent: uses INSERT OR IGNORE; safe to re-run at any time.
"""

import sqlite3
from pathlib import Path

# Canonical DB path — shared by mcp_server/ and core/
DB_PATH = Path(__file__).resolve().parent.parent / "pathwatch.db"


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ada_standards (
            rule_code   TEXT PRIMARY KEY,
            category    TEXT NOT NULL,          -- e.g. "curb_ramp", "sidewalk"
            barrier_type TEXT NOT NULL,         -- short label matched by lookup tool
            description TEXT NOT NULL,          -- human-readable rule text
            threshold   TEXT NOT NULL,          -- measurable limit / spec
            citation    TEXT NOT NULL           -- document + section reference
        );

        CREATE TABLE IF NOT EXISTS jurisdiction_contacts (
            jurisdiction_id   TEXT PRIMARY KEY,
            jurisdiction_name TEXT NOT NULL,
            phone             TEXT NOT NULL,
            email             TEXT NOT NULL,
            portal_url        TEXT NOT NULL,
            notes             TEXT NOT NULL
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# ADA / PROWAG standards (12 rules)
# ---------------------------------------------------------------------------

ADA_STANDARDS = [
    # -- Curb ramp rules (PROWAG 2023) ----------------------------------------
    (
        "PROWAG-R304.2.1",
        "curb_ramp",
        "curb ramp running slope",
        "Curb ramp running slope shall not exceed 8.33% (1:12 ratio).",
        "≤ 8.33% (1:12)",
        "PROWAG 2023, Section R304.2.1",
    ),
    (
        "PROWAG-R304.2.2",
        "curb_ramp",
        "curb ramp cross slope",
        "Curb ramp cross slope shall not exceed 2.0%.",
        "≤ 2.0%",
        "PROWAG 2023, Section R304.2.2",
    ),
    (
        "PROWAG-R304.3.1",
        "curb_ramp",
        "curb ramp landing",
        "A landing at least 60 inches by 60 inches (1525 mm × 1525 mm) shall be "
        "provided at the top of a curb ramp.",
        "≥ 60 in × 60 in (1525 mm × 1525 mm)",
        "PROWAG 2023, Section R304.3.1",
    ),
    # -- Detectable warning surface rules (PROWAG 2023) -----------------------
    (
        "PROWAG-R305.1",
        "detectable_warning",
        "detectable warning surface required",
        "Detectable warning surfaces shall be provided where a shared use path "
        "or pedestrian access route connects to a vehicular way.",
        "Required at all curb ramp flush transitions and blended transitions",
        "PROWAG 2023, Section R305.1",
    ),
    (
        "PROWAG-R305.2.1",
        "detectable_warning",
        "detectable warning truncated dome diameter",
        "Truncated domes on detectable warning surfaces shall have a base diameter "
        "of 0.9 inches (23 mm) ± 0.1 inch (2.5 mm).",
        "Base diameter 0.9 in ± 0.1 in (23 mm ± 2.5 mm)",
        "PROWAG 2023, Section R305.2.1",
    ),
    (
        "PROWAG-R305.3",
        "detectable_warning",
        "detectable warning contrast",
        "Detectable warning surfaces shall contrast visually with adjacent "
        "walking surfaces (light-on-dark or dark-on-light).",
        "Visual contrast required; yellow preferred per MUTCD",
        "PROWAG 2023, Section R305.3",
    ),
    # -- Sidewalk / pedestrian access route rules (PROWAG 2023) ---------------
    (
        "PROWAG-R302.1",
        "sidewalk",
        "sidewalk running slope",
        "The running slope of a pedestrian access route shall not exceed 5.0% "
        "except where a pedestrian access route is contained within a street or "
        "highway with a steeper profile.",
        "≤ 5.0% (non-ramp segment)",
        "PROWAG 2023, Section R302.1",
    ),
    (
        "PROWAG-R302.2",
        "sidewalk",
        "sidewalk cross slope",
        "The cross slope of a pedestrian access route shall not exceed 2.0%.",
        "≤ 2.0%",
        "PROWAG 2023, Section R302.2",
    ),
    (
        "PROWAG-R302.3",
        "sidewalk",
        "sidewalk clear width",
        "Pedestrian access routes shall have a clear width of 60 inches (1525 mm) "
        "minimum; 36 inches (915 mm) is the absolute minimum where space is "
        "constrained.",
        "≥ 60 in (1525 mm) preferred; ≥ 36 in (915 mm) absolute minimum",
        "PROWAG 2023, Section R302.3",
    ),
    # -- Surface quality rules (ADA Standards for Accessible Design 2010) -----
    (
        "ADA-2010-4.5.1",
        "sidewalk",
        "surface firmness stability",
        "Ground and floor surfaces along accessible routes and in accessible "
        "rooms and spaces shall be stable, firm, and slip-resistant.",
        "Stable, firm, slip-resistant surface required",
        "ADA Standards for Accessible Design 2010, Section 4.5.1",
    ),
    (
        "ADA-2010-4.5.2",
        "sidewalk",
        "vertical surface discontinuity",
        "Changes in level between ¼ inch (6.4 mm) and ½ inch (12.7 mm) shall "
        "be beveled with a slope no greater than 1:2. Changes in level greater "
        "than ½ inch must be ramped.",
        "≤ ¼ in (6 mm) vertical; ¼–½ in beveled at ≤ 1:2; > ½ in requires ramp",
        "ADA Standards for Accessible Design 2010, Section 4.5.2",
    ),
    # -- Protruding objects (ADA Standards 2010) -------------------------------
    (
        "ADA-2010-4.4.1",
        "sidewalk",
        "protruding object",
        "Objects projecting from walls or posts into circulation paths shall "
        "not protrude more than 4 inches (100 mm) into the circulation path "
        "between 27 inches (685 mm) and 80 inches (2030 mm) above the floor.",
        "Max 4 in (100 mm) protrusion between 27 in and 80 in height",
        "ADA Standards for Accessible Design 2010, Section 4.4.1",
    ),
]


def seed_ada_standards(conn: sqlite3.Connection) -> int:
    conn.executemany(
        """INSERT OR IGNORE INTO ada_standards
           (rule_code, category, barrier_type, description, threshold, citation)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ADA_STANDARDS,
    )
    conn.commit()
    cursor = conn.execute("SELECT COUNT(*) FROM ada_standards")
    return cursor.fetchone()[0]


# ---------------------------------------------------------------------------
# Jurisdiction contacts
# ---------------------------------------------------------------------------

JURISDICTION_CONTACTS = [
    (
        "milwaukee_311",
        "City of Milwaukee — Public Works (311)",
        "(414) 286-2489",
        "dpw@milwaukee.gov",
        "https://city.milwaukee.gov/311",
        "Submit 'Sidewalk / Curb Repair' category. Include address, GPS coords, "
        "and photo evidence. Response SLA: 10 business days for non-emergency.",
    ),
    (
        "uwm_facilities",
        "University of Wisconsin–Milwaukee — Facilities Management",
        "(414) 229-4242",
        "facilitiesmanagement@uwm.edu",
        "https://uwm.edu/facilities/",
        "Submit via UWM Facilities online work-order portal. Select 'Exterior / "
        "Grounds' and 'ADA / Accessibility Barrier'. Attach GPS coordinates and "
        "photo evidence. Priority repairs acknowledged within 2 business days.",
    ),
]


def seed_jurisdiction_contacts(conn: sqlite3.Connection) -> int:
    conn.executemany(
        """INSERT OR IGNORE INTO jurisdiction_contacts
           (jurisdiction_id, jurisdiction_name, phone, email, portal_url, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        JURISDICTION_CONTACTS,
    )
    conn.commit()
    cursor = conn.execute("SELECT COUNT(*) FROM jurisdiction_contacts")
    return cursor.fetchone()[0]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"[ada_seed] Opening database: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        create_tables(conn)
        std_count = seed_ada_standards(conn)
        jur_count = seed_jurisdiction_contacts(conn)
        print(f"[ada_seed] ✓ ada_standards        : {std_count} rows")
        print(f"[ada_seed] ✓ jurisdiction_contacts : {jur_count} rows")
    finally:
        conn.close()
    print("[ada_seed] Done.")


if __name__ == "__main__":
    main()
