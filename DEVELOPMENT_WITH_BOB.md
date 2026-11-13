# DEVELOPMENT_WITH_BOB.md
# Developing PathWatch AI with IBM Bob

## Overview

PathWatch AI was designed, implemented, and tested entirely within **IBM Bob**, IBM's AI-powered IDE. Bob was not used only as a code completer; it served as the project's technical lead, generating architectural plans, implementing the modules, wiring the MCP server, and authoring the full offline `pytest` suite. This document records the specific Bob features used in each phase and explains why each capability was the right tool for the job.

---

## How IBM Bob Was Used

### 1. `AGENTS.md` — Machine-Readable Project Blueprint

The project begins with [`AGENTS.md`](AGENTS.md), a file that IBM Bob auto-loads as workspace context at the start of every conversation. It defines:

- **Five strict architectural guardrails** (zero cloud dependencies, Pydantic-validated outputs, deterministic ADA grounding through MCP, safe GPS fallback, and one-command reproducibility).
- The **canonical directory structure** every module must follow.
- The **tech stack** (FastMCP, Pydantic v2, Ollama, SQLite, FastAPI, and Leaflet.js).

Writing `AGENTS.md` first meant that every subsequent Bob prompt was answered within these constraints. Bob never proposed OpenAI calls, never invented non-local model endpoints, and produced directory layouts that matched the specification exactly. It is the single most valuable artifact in the project.

**Key guardrail enforced in this way:** Bob's code never imported `openai`, `google.cloud`, or any external inference library, because `AGENTS.md` declared those out of scope before the first line of code was written.

---

### 2. Plan Mode — Phased Architecture Before Code

Before writing any code, Bob's **Plan Mode** was used to decompose the project into four phases and produce detailed sub-task specifications:

| Phase | Plan document | Scope |
|-------|---------------|-------|
| 1 | [`phase1-plan.md`](phase1-plan.md) | Directory scaffold, `requirements.txt`, `ada_seed.py`, `mcp_server/server.py`, `.bob/mcp.json` |
| 2 | *(inline)* | Pydantic schemas (`core/schemas.py`), EXIF GPS extraction and keyframe sampling (`core/ingest.py`), Ollama inference pipeline (`core/auditor.py`) |
| 3 | *(inline)* | Exporters (`core/exporter.py`), FastAPI web server (`web/app.py`), Leaflet.js map dashboard |
| 4 | *(this session)* | `setup.sh`, full offline `pytest` suite, this document |

Each plan specified:
- Sub-task dependencies and ordering constraints.
- The exact expected file paths and function signatures.
- Data shapes (for example, the 12 PROWAG/ADA rule rows and the two jurisdiction contacts).
- A test coverage strategy before implementation.

The value of Plan Mode was **explicit scope boundaries**. The "out of scope" sections prevented drift in every implementation prompt that followed.

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

This single file gives Bob live, in-session access to `lookup_ada_standard` and `find_jurisdiction_contact`, the same tools the application code calls. During development, Bob could call these tools directly to confirm that the SQLite knowledge base was seeded correctly and that keyword routing (UWM versus Milwaukee 311) behaved as specified, without leaving the editor.

**Architectural significance:** The MCP server is not merely a development convenience; it is the application's deterministic regulatory grounding layer (AGENTS.md guardrail 3). Using it in both Bob's assistant context and the production `auditor.py` ensures that the development and runtime environments are identical.

---

### 4. Agent Mode — Phased Implementation Prompts

All code was generated in Bob's **Agent Mode**, which provides:

- Read/write access to files across the project tree.
- Tool calls (`read_file`, `grep`, `apply_diff`, `write_file`) that Bob used to inspect existing code before editing.
- An `update_todo_list` tool that Bob maintained in real time, marking tasks complete only after verifying the code was correct.

#### Token-Disciplined Prompts

Each phase was submitted as a single, self-contained prompt that:
1. Cited the relevant AGENTS.md section and phase plan.
2. Listed the exact files to create or modify.
3. Specified the expected public API (function signatures, return types, and Pydantic model fields).
4. Included test-driven acceptance criteria (for example, "the fallback coordinates must be `43.0766, -87.8812`").

This "one prompt, one phase" discipline kept context windows focused and prevented Bob from mixing concerns across phases.

#### `investigate_before_answering` Rule

Bob's project rules include an `investigate_before_answering` directive: Bob must read any file it intends to modify before proposing changes. In practice, every Phase 2–4 session opened with parallel `read_file` calls across all core modules, ensuring diffs were grounded in the actual current state rather than in remembered summaries.

---

### 5. Offline-First pytest Suite

The test suite was explicitly designed to run with **zero network access**:

| Test file | Strategy |
|-----------|----------|
| [`tests/test_mcp_server.py`](tests/test_mcp_server.py) | Calls `lookup_ada_standard` and `find_jurisdiction_contact` directly as Python functions against the local `pathwatch.db`. No MCP protocol overhead and no server process are involved. |
| [`tests/test_ingest.py`](tests/test_ingest.py) | Synthesizes minimal JPEG images with Pillow (with and without `piexif` GPS EXIF) and a synthetic checkerboard or uniform-grey `.mp4` with OpenCV. No real photos or videos are required. |
| [`tests/test_auditor_schema.py`](tests/test_auditor_schema.py) | Patches `httpx.post` with `unittest.mock.patch` so `audit_image` runs end to end (GPS → VisionFinding validation → PROWAG enrichment → jurisdiction routing → Markdown/GeoJSON export) without needing Ollama to run. |

Bob generated all three test files from first principles, reasoning about the minimal synthetic fixture for each scenario and writing helpers such as `_make_plain_jpeg`, `_make_gps_jpeg`, `_make_synthetic_video`, `_fake_image`, and `_mock_ollama_response` to keep the tests self-contained.

---

### 6. Key Decisions Made Inside Bob

| Decision | Rationale recorded in Bob session |
|----------|----------------------------------|
| Use the standalone `fastmcp` package (not `mcp.server.fastmcp`) | FastMCP v4 ships as its own package; Bob verified the import path before writing `server.py`. |
| `_BARRIER_DB_QUERY` map in `auditor.py` | A direct mapping from `BarrierType` literals to lowercase database search terms guarantees test-deterministic MCP enrichment without relying on partial-match luck. |
| `gps_source: "fallback"` as a Pydantic Literal field | Makes GPS provenance machine-checkable. Tests can assert `source == "fallback"` instead of inferring provenance from floating-point coordinates. |
| `moondream` as the default model | It is the smallest Ollama multimodal model that runs on CPU and is overridable through the `PATHWATCH_MODEL` environment variable without code changes. |
| RFC 7946 `[longitude, latitude]` coordinate order | Bob flagged the GeoJSON spec requirement while authoring `export_geojson` and added a dedicated test, `test_geojson_coordinate_order_is_lon_lat`, to guard it. |
| `INSERT OR IGNORE` in `ada_seed.py` | Idempotency: `setup.sh` calls the seeder on every boot, so duplicate rows must be silently dropped. |

---

## Running the Full Test Suite

```bash
# From the repo root with the venv activated:
source .venv/bin/activate

# Seed the DB (required before test_mcp_server.py):
python mcp_server/ada_seed.py

# Run all offline tests (Ollama not required):
pytest tests/ -v
```

Expected result: all tests in `test_mcp_server.py`, `test_ingest.py`, and `test_auditor_schema.py` pass. Tests that require optional packages such as `piexif` or `cv2` are automatically skipped with an informative reason if those packages are absent.

---

## Summary

IBM Bob was the primary engineering environment for PathWatch AI, from a blank repository to a working application. The combination of:

- **`AGENTS.md`** as a machine-readable spec that Bob auto-loaded in every session,
- **Plan Mode** for phase-gated architecture before implementation,
- **`.bob/mcp.json`** for live MCP tool access during development,
- **Agent Mode** with token-disciplined phased prompts for implementation,
- and **offline `pytest`** authoring that Bob drove from first principles,

produced a fully local, offline-first accessibility auditor in four phases without a single external API call.
