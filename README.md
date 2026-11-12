# PathWatch AI

**100% Local, Offline-First Sidewalk & Curb-Ramp Accessibility Auditor**

Built for the **IBM AI Builders Challenge – North America (AI for Good)** using **IBM Bob** as the primary development environment.

PathWatch AI turns everyday sidewalk, walkway, and crosswalk photos (or walk-along videos) into structured, GPS-mapped accessibility audits grounded in **2023 PROWAG / ADA** standards—generating ready-to-submit municipal 311 repair tickets and OpenStreetMap GeoJSON patches without sending a single byte of data to the cloud.

---

## Why PathWatch AI?
Broken sidewalks, missing curb ramps, and absent tactile warning domes create daily injury risks and mobility lockouts for wheelchair users and pedestrians with visual or mobility disabilities. PathWatch AI closes the gap between spotting a hazard and getting it fixed:
- **Zero Cloud Cost & Total Privacy:** Runs 100% locally via **Ollama** (`moondream`) and **SQLite**—no cloud API keys, subscriptions, or external telemetry.
- **Deterministic Regulatory Grounding (MCP):** Rather than letting an LLM guess accessibility laws, PathWatch AI queries a local **Model Context Protocol (MCP)** server backed by SQLite to attach verified **PROWAG 2023** rule sections and local municipal 311 / campus facilities contacts.
- **Actionable Exports:** Produces printable Markdown repair packets for city 311 portals and RFC 7946 **GeoJSON** files tagged for OpenStreetMap (`wheelchair`, `kerb`, `surface`, `ada_section`).

---

## System Architecture

```text
Phone Photo / Video (.jpg / .mp4)
        │
        ▼
[1. Local Ingestion — core/ingest.py]
  • Extracts EXIF GPS coordinates (with safe campus fallback)
  • Samples sharp keyframes via OpenCV Laplacian variance
        │
        ▼
[2. Local Vision Auditor — core/auditor.py]
  • Local Ollama multimodal inference (moondream)
  • Strict Pydantic v2 validation (HazardAssessment)
        │
        ▼
[3. Custom MCP Knowledge Server — mcp_server/server.py]
  • FastMCP + SQLite (pathwatch.db)
  • Attaches PROWAG 2023 citations & 311 jurisdiction routing
        │
        ▼
[4. Interactive Map & Exporters — web/ & core/exporter.py]
  • FastAPI + Leaflet.js color-coded hazard map
  • One-click 311 Repair Ticket & OpenStreetMap GeoJSON download