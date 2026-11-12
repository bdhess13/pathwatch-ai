# Phase 1 Plan — PathWatch AI Scaffold & MCP Server

## Overview

Bootstrap the entire repository skeleton that all later phases depend on.
This phase produces no ML inference code; it only establishes the project
layout, declares dependencies, populates the local ADA/PROWAG knowledge base,
stands up the FastMCP server that exposes that knowledge, and registers the
server with Bob's project-scoped MCP config.

**Scope boundaries:**
- In scope: directory tree, `requirements.txt`, `ada_seed.py`,
  `mcp_server/server.py`, `.bob/mcp.json`.
- Out of scope: Pydantic schemas (`core/`), Ollama inference (`auditor.py`),
  FastAPI web app (`web/`), front-end, tests, `setup.sh`.

---

## Sub-Task 1 — Create directory structure and stub files

**Intent:**  
Establish every directory and empty placeholder file listed in AGENTS.md so
all future sub-tasks can write into predictable paths without caring about
directory creation.

**Expected Outcomes:**
- The following directories exist: `mcp_server/`, `core/`, `web/web/static/`,
  `sample_data/photos/`, `tests/`, `.bob/`.
- Each source directory contains an `__init__.py` (empty) so Python treats
  them as packages.
- Stub `.py` files exist (empty/`pass`) for every module listed in AGENTS.md
  that is NOT delivered in this phase (`core/schemas.py`, `core/ingest.py`,
  `core/auditor.py`, `core/exporter.py`, `web/app.py`).
- A `.gitkeep` sits in `sample_data/photos/` and `web/static/`.

**Todo List:**
1. Create `mcp_server/__init__.py` (empty).
2. Create `core/__init__.py` (empty).
3. Create `web/__init__.py` (empty).
4. Create `tests/__init__.py` (empty).
5. Create stub `core/schemas.py` (single `# TODO` comment).
6. Create stub `core/ingest.py`.
7. Create stub `core/auditor.py`.
8. Create stub `core/exporter.py`.
9. Create stub `web/app.py`.
10. Create `.gitkeep` in `sample_data/photos/` and `web/static/`.
11. Create empty stub test files: `tests/test_mcp_server.py`,
    `tests/test_ingest.py`, `tests/test_auditor_schema.py`.

**Relevant Context:**
- Target layout defined in `AGENTS.md` Section 3.
- Nothing to import or reuse — this is a greenfield repo.

**Status:** `[ ] pending`

---

## Sub-Task 2 — Write `requirements.txt`

**Intent:**  
Declare every Python dependency needed for Phase 1 (MCP server + seeder) and
stub out version pins for Phase 2+ modules so evaluators get a reproducible
`pip install -r requirements.txt`.

**Expected Outcomes:**
- `requirements.txt` lists all direct dependencies with minimum version pins.
- No cloud-SDK packages are present (guardrail 1 in AGENTS.md).

**Dependencies to include:**

| Package | Purpose |
|---------|---------|
| `fastmcp>=4.0` | FastMCP server framework (standalone package, `from fastmcp import FastMCP`) |
| `pydantic>=2.0` | Schema validation (used in Phase 2) |
| `fastapi>=0.111` | Web server (Phase 3) |
| `uvicorn[standard]>=0.29` | ASGI runner |
| `ollama>=0.2` | Local Ollama Python client (Phase 2) |
| `Pillow>=10.0` | EXIF extraction (Phase 2) |
| `opencv-python-headless>=4.9` | Keyframe sampling (Phase 2) |
| `httpx>=0.27` | Async HTTP in tests |
| `pytest>=8.0` | Test runner |
| `pytest-asyncio>=0.23` | Async test support |

**Todo List:**
1. Write `requirements.txt` with the packages and pins from the table above.

**Relevant Context:**
- FastMCP v4+ uses `from fastmcp import FastMCP` (standalone `fastmcp` package,
  not `mcp.server.fastmcp`).
- `opencv-python-headless` is preferred over `opencv-python` to avoid GUI
  dependencies on CI/servers.

**Status:** `[ ] pending`

---

## Sub-Task 3 — Write `mcp_server/ada_seed.py`

**Intent:**  
Populate `pathwatch.db` (SQLite) with two tables:
1. `ada_standards` — ADA/PROWAG rules covering curb ramps, sidewalk cross-
   slope, running slope, width, landing, detectable warning surfaces, etc.
2. `jurisdiction_contacts` — 311 / facilities-repair routing for Milwaukee
   (City of Milwaukee Public Works) and UWM Campus (UWM Facilities Management).

This is the authoritative local knowledge base; all MCP tool responses draw
from it. The script must be idempotent (safe to re-run).

**Expected Outcomes:**
- Running `python mcp_server/ada_seed.py` creates/updates `pathwatch.db` in
  the repo root.
- `ada_standards` contains ≥ 12 rows covering:
  - Curb ramp running slope ≤ 8.33% (1:12) — PROWAG R304.2.1
  - Curb ramp cross slope ≤ 2% — PROWAG R304.2.2
  - Landing size ≥ 60" × 60" — PROWAG R304.3.1
  - Detectable warning surface required — PROWAG R305.1
  - Detectable warning truncated dome diameter 0.9" ± 0.1" — PROWAG R305.2.1
  - Detectable warning contrast required — PROWAG R305.3
  - Sidewalk running slope ≤ 5% (non-ramp) — PROWAG R302.1
  - Sidewalk cross slope ≤ 2% — PROWAG R302.2
  - Clear width ≥ 60" (preferred) / 36" min — PROWAG R302.3
  - Surface must be firm, stable, slip-resistant — ADA 4.5.1
  - Vertical discontinuity ≤ ¼" (½" beveled) — ADA 4.5.2
  - Protruding objects max 4" into circulation path — ADA 4.4.1
- `jurisdiction_contacts` contains at least 2 rows:
  - Milwaukee 311 (city) — phone, email, online portal URL, notes.
  - UWM Facilities Management — phone, email, online portal URL, notes.
- The script prints a confirmation row-count on success.

**Todo List:**
1. Write `mcp_server/ada_seed.py` with:
   - `create_tables(conn)` — CREATE TABLE IF NOT EXISTS for both tables.
   - `seed_ada_standards(conn)` — INSERT OR IGNORE rows for all ≥ 12 rules.
   - `seed_jurisdiction_contacts(conn)` — INSERT OR IGNORE Milwaukee + UWM rows.
   - `main()` guard that opens `pathwatch.db` in repo root, runs both seeds,
     prints counts, closes connection.

**Relevant Context:**
- DB path should be relative to repo root: `Path(__file__).parent.parent / "pathwatch.db"`.
- Use `INSERT OR IGNORE` with a UNIQUE constraint on `rule_code` /
  `jurisdiction_id` for idempotency.
- No ORM — plain `sqlite3` only.

**Status:** `[ ] pending`

---

## Sub-Task 4 — Write `mcp_server/server.py`

**Intent:**  
Implement the FastMCP server that exposes two tools:
1. `lookup_ada_standard(barrier_type: str)` — queries `ada_standards` and
   returns the matching rule code, description, threshold, and citation.
2. `find_jurisdiction_contact(location_hint: str)` — queries
   `jurisdiction_contacts` and returns routing info for the closest
   jurisdiction match.

These are the **only** external interfaces the auditor pipeline (Phase 2) uses
to ground every hazard with verified ADA codes and 311 routing — satisfying
AGENTS.md guardrail 3.

**Expected Outcomes:**
- `mcp_server/server.py` is runnable as `python mcp_server/server.py` (stdio
  transport for Bob MCP) and launches without error when `pathwatch.db` exists.
- Both tools return structured dicts (serialisable to JSON).
- `lookup_ada_standard` performs a case-insensitive LIKE search on the
  `barrier_type` and `description` columns; returns a list of matching rule
  dicts (empty list if none).
- `find_jurisdiction_contact` checks whether `location_hint` contains keywords
  "uwm", "campus", or "university" to route to UWM; otherwise returns
  Milwaukee 311.

**Todo List:**
1. Import `FastMCP` from `fastmcp`, `sqlite3`, `pathlib.Path`.
2. Define DB path constant (same as `ada_seed.py`).
3. Create `mcp = FastMCP("pathwatch-mcp")` instance.
4. Implement `lookup_ada_standard` tool with `@mcp.tool()` decorator.
5. Implement `find_jurisdiction_contact` tool with `@mcp.tool()` decorator.
6. Add `if __name__ == "__main__": mcp.run()` entry point (stdio transport).

**Relevant Context:**
- FastMCP v2: decorate with `@mcp.tool()`, type-annotate parameters and return
  type, docstring becomes the tool description shown to the LLM.
- Import path: `from fastmcp import FastMCP` (package `fastmcp`).
- The server runs over stdio for Bob's MCP integration; no HTTP port needed.

**Status:** `[ ] pending`

---

## Sub-Task 5 — Register MCP server in `.bob/mcp.json`

**Intent:**  
Register `pathwatch-mcp` as a project-scoped Bob MCP server so that when Bob
is opened in this workspace it can call `lookup_ada_standard` and
`find_jurisdiction_contact` directly.

**Expected Outcomes:**
- `.bob/mcp.json` exists and is valid JSON.
- It registers the server with:
  - `name`: `"pathwatch-mcp"`
  - `transport`: `"stdio"`
  - `command`: `"python"` with args `["mcp_server/server.py"]`
  - `cwd`: repo root (relative `.`)

**Todo List:**
1. Create `.bob/` directory if not already present.
2. Write `.bob/mcp.json` with the server registration object.

**Relevant Context:**
- Bob MCP project config lives at `.bob/mcp.json` (confirmed in AGENTS.md).
- Use relative `cwd` so the config is portable across machines.

**Status:** `[ ] pending`

---

## Implementation Order

Sub-tasks must run in this order (each depends on the previous):

1 → 2 → 3 → 4 → 5

Sub-task 3 (`ada_seed.py`) must be authored before sub-task 4 (`server.py`)
because the server's DB schema assumptions must match the seeder's table
definitions.
