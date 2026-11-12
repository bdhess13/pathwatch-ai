# DEVELOPMENT_WITH_BOB.md
# Developing PathWatch AI with IBM Bob

## Overview

PathWatch AI was architected, implemented, and tested entirely inside **IBM Bob** — the AI-powered IDE from IBM. Bob was not used merely as a code completer; it served as the project's technical lead: generating architectural plans, implementing all modules, wiring the MCP server, and authoring the full offline `pytest` suite. This document records the specific Bob features used at each phase and explains *why* each capability was the right tool for that problem.

---

## How IBM Bob Was Used

### 1. `AGENTS.md` — Machine-Readable Project Blueprint

The project begins with [`AGENTS.md`](AGENTS.md), a file that IBM Bob auto-loads as workspace context at the start of every conversation. It encodes:

- **Five strict architectural guardrails** (zero cloud dependencies, Pydantic-validated outputs, deterministic ADA grounding via MCP, safe GPS fallback, one-command reproducibility).
- The **canonical directory structure** every module must follow.
- The **tech stack** (FastMCP, Pydantic v2, Ollama, SQLite, FastAPI, Leaflet.js).

Writing `AGENTS.md` first meant that every subsequent Bob prompt was answered in the context of these constraints — Bob never proposed OpenAI calls, never invented non-local model endpoints, and produced path layouts that matched the spec exactly. It is the single most leveraged artifact in the project.

**Key guardrail enforced this way:** Bob's code never imported `openai`, `google.cloud`, or any external-inference library, because `AGENTS.md` declared those out of scope before the first line of code was written.

---

### 2. Plan Mode — Phased Architecture Before Code

Before writing any code, Bob's **Plan Mode** was used to decompose the project into four phases and produce detailed sub-task specifications:

| Phase | Plan document | Scope |
|-------|---------------|-------|
| 1 | [`phase1-plan.md`](phase1-plan.md) | Directory scaffold, `requirements.txt`, `ada_seed.py`, `mcp_server/server.py`, `.bob/mcp.json` |
| 2 | *(inline)* | Pydantic schemas (`core/schemas.py`), EXIF GPS extractor and keyframe sampler (`core/ingest.py`), Ollama inference pipeline (`core/auditor.py`) |
| 3 | *(inline)* | Exporters (`core/exporter.py`), FastAPI web server (`web/app.py`), Leaflet.js map dashboard |
| 4 | *(this session)* | `setup.sh`, full offline `pytest` suite, this document |

Each plan specified:
- Sub-task dependencies and ordering constraints.
- Exact expected file paths and function signatures.
- Data shapes (e.g., the 12 PROWAG/ADA rule rows, the two jurisdiction contacts).
- Test coverage strategy before implementation.

Plan Mode's value was **forcing explicit scope boundaries** — "out of scope" sections prevented scope creep in every implementation prompt that followed.

---

### 3. `.bob/mcp.json` — Project-Scoped MCP Server

[`.bob/mcp.json`](.bob/mcp.json) registers `pathwatch-mcp` as a project-scoped Model Context Protocol server:

```json
{
  "mcpServers": {
    "pathwatch-mcp": {
      "command": "python",
      "args": ["mcp_server/server.py"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```

This single file gives Bob live, in-session access to `lookup_ada_standard` and `find_jurisdiction_contact` — the same tools the application code calls. During development, Bob could call these tools directly to verify that the SQLite knowledge base was seeded correctly and that keyword routing (UWM vs. Milwaukee 311) behaved as specified, without leaving the editor.

**Architectural significance:** The MCP server is not a development-time convenience; it is the application's deterministic regulatory grounding layer (AGENTS.md guardrail 3). Using it in both Bob's assistant context and the production `auditor.py` ensures the dev and runtime environments are identical.

---

### 4. Agent Mode — Phased Implementation Prompts

All code was generated in Bob's **Agent Mode**, which provides:

- Read/write access to files across the project tree.
- Tool calls (`read_file`, `grep`, `apply_diff`, `write_file`) that Bob used to inspect existing code before editing.
- An `update_todo_list` tool that Bob maintained in real-time, marking tasks complete only after verifying the produced code was correct.

#### Token-Disciplined Prompts

Each phase was submitted as a single, self-contained prompt that:
1. Cited the relevant AGENTS.md section and phase plan.
2. Listed the exact files to create or modify.
3. Specified the expected public API (function signatures, return types, Pydantic model fields).
4. Included test-driven acceptance criteria (e.g., "the fallback coordinates must be `43.0766, -87.8812`").

This "one prompt, one phase" discipline kept context windows focused and prevented Bob from mixing concerns across phases.

#### `investigate_before_answering` Rule

Bob's project rules include an `investigate_before_answering` directive: Bob must read any file it intends to modify before proposing changes. In practice, every Phase 2–4 session opened with parallel `read_file` calls across all core modules, ensuring diffs were grounded in the actual current state rather than remembered summaries.

---

### 5. Offline-First pytest Suite

The test suite was explicitly designed to run with **zero network access**:

| Test file | Strategy |
|-----------|----------|
| [`tests/test_mcp_server.py`](tests/test_mcp_server.py) | Calls `lookup_ada_standard` and `find_jurisdiction_contact` directly as Python functions against the local `pathwatch.db`. No MCP protocol overhead, no server process. |
| [`tests/test_ingest.py`](tests/test_ingest.py) | Synthesises minimal JPEG images with Pillow (with and without `piexif` GPS EXIF) and a synthetic checkerboard/uniform-grey `.mp4` with OpenCV. No real photos or videos required. |
| [`tests/test_auditor_schema.py`](tests/test_auditor_schema.py) | Patches `httpx.post` with `unittest.mock.patch` so `audit_image` runs end-to-end (GPS → VisionFinding validation → PROWAG enrichment → jurisdiction routing → Markdown/GeoJSON export) without Ollama running. |

Bob generated all three test files from first principles, reasoning about what the minimal synthetic fixture for each scenario would be and writing helpers (`_make_plain_jpeg`, `_make_gps_jpeg`, `_make_synthetic_video`, `_fake_image`, `_mock_ollama_response`) to keep tests self-contained.

---

### 6. Key Decisions Made Inside Bob

| Decision | Rationale recorded in Bob session |
|----------|----------------------------------|
| Use `fastmcp` standalone package (not `mcp.server.fastmcp`) | FastMCP v4 ships as its own package; Bob verified the import path before writing `server.py`. |
| `_BARRIER_DB_QUERY` map in `auditor.py` | Direct mapping from `BarrierType` literals to lowercase DB search terms guarantees test-deterministic MCP enrichment without relying on partial-match luck. |
| `gps_source: "fallback"` as a Pydantic Literal field | Makes the GPS provenance machine-checkable — tests can assert `source == "fallback"` rather than comparing floating-point coordinates to infer provenance. |
| `moondream` as the default model | Smallest Ollama multimodal model that runs on CPU; overridable via `PATHWATCH_MODEL` env-var without code changes. |
| RFC 7946 `[longitude, latitude]` coordinate order | Bob flagged the GeoJSON spec requirement during `export_geojson` authoring and added a dedicated test (`test_geojson_coordinate_order_is_lon_lat`) to guard it. |
| `INSERT OR IGNORE` in `ada_seed.py` | Idempotency: `setup.sh` calls the seeder on every boot; duplicate rows must be silently dropped. |

---

## Running the Full Test Suite

```bash
# From repo root with the venv activated:
source .venv/bin/activate

# Seed the DB (required before test_mcp_server.py):
python mcp_server/ada_seed.py

# Run all offline tests (Ollama not required):
pytest tests/ -v
```

Expected result: all tests in `test_mcp_server.py`, `test_ingest.py`, and `test_auditor_schema.py` pass. Tests that require optional packages (`piexif`, `cv2`) are automatically skipped with an informative reason if those packages are absent.

---

## Summary

IBM Bob was the primary engineering environment for PathWatch AI from blank repository to working application. The combination of:

- **`AGENTS.md`** as a machine-readable spec that Bob auto-loaded in every session,
- **Plan Mode** for phase-gated architecture before implementation,
- **`.bob/mcp.json`** for live MCP tool access during development,
- **Agent Mode** with token-disciplined phased prompts for implementation,
- and **offline `pytest`** authoring that Bob drove from first principles,

produced a fully local, offline-first accessibility auditor in four phases without a single external API call.
