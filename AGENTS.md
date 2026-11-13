# AGENTS.md — PathWatch AI Project Blueprint and Agent Rules

## 1. Project Overview
- **Project Name:** PathWatch AI (`bdhess13/pathwatch-ai`)
- **Competition:** IBM AI Builders Challenge – North America (Deadline: December 10, 2026 at 5:00 PM ET)
- **Challenge Theme:** AI for Good
- **Mission:** A 100% local, offline-first sidewalk, walkway, and curb-ramp accessibility auditor that detects ADA/PROWAG mobility barriers from phone photos or walk-along videos, maps hazard severity with GPS coordinates, and generates ready-to-submit municipal 311/campus facilities repair tickets and OpenStreetMap GeoJSON patches.

---

## 2. Strict Architectural Guardrails
1. **Zero Cloud Dependencies:** All vision inference, GPS extraction, database lookups, and map rendering must run locally on consumer hardware. Never add OpenAI, Google Cloud Vision, Mapbox, or external API keys.
2. **Structured Vision Outputs Only:** Every vision-language model response from the local Ollama instance (`gemma4:e4b` by default; overridable via `PATHWATCH_MODEL`) must be validated through a strict **Pydantic** schema (`HazardAssessment`).
3. **Deterministic ADA and Jurisdiction Grounding:** Every flagged barrier must query the local SQLite database (`pathwatch.db`) through the **MCP server** (`lookup_ada_standard` and `find_jurisdiction_contact`) to attach verified ADA/PROWAG rule codes and local 311/facilities routing.
4. **Safe Fallback for Missing GPS:** If an uploaded photo lacks EXIF GPS tags, fall back cleanly to the default campus/city coordinates (`43.0766, -87.8812`) and mark `gps_source: "fallback"` instead of crashing.
5. **One-Command Reproducibility:** Evaluators who clone this repository must be able to seed sample data and launch the dashboard using `./setup.sh` and the bundled `sample_data/` folder.

---

## 3. Directory Structure and Tech Stack
```text
pathwatch-ai/
├── AGENTS.md                     # Auto-loaded by IBM Bob
├── README.md                     # Setup guide, architecture diagram, and demo walkthrough
├── DEVELOPMENT_WITH_BOB.md       # Log of IBM Bob Plan Mode, MCP, Subagents, and pytest workflows
├── setup.sh                      # One-command venv setup, SQLite seed, and Ollama check
├── requirements.txt
├── .bob/
│   └── mcp.json                  # Project-scoped MCP server registration
├── mcp_server/
│   ├── server.py                 # FastMCP server exposing ADA/PROWAG and 311 routing tools
│   └── ada_seed.py               # Seeds pathwatch.db with ADA standards and jurisdiction contacts
├── core/
│   ├── schemas.py                # Pydantic models (BarrierType, Severity, HazardAssessment)
│   ├── ingest.py                 # Photo EXIF GPS extractor and OpenCV sharp keyframe sampler
│   ├── auditor.py                # Local Ollama VLM inference and MCP enrichment pipeline
│   └── exporter.py               # Generates 311 Markdown/HTML repair packets and OSM GeoJSON
├── web/
│   ├── app.py                    # FastAPI server (upload, audit, map API, export endpoints)
│   └── static/                   # Leaflet.js map dashboard and hazard evidence cards
├── sample_data/
│   └── photos/                   # Sample geotagged sidewalk and curb photos for an instant demo
└── tests/
    ├── test_mcp_server.py
    ├── test_ingest.py
    └── test_auditor_schema.py