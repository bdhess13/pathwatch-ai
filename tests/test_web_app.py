import importlib
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from core.schemas import HazardAssessment, VisionFinding

app_module = importlib.import_module("web.app")


def test_audits_persist_and_uploaded_photos_are_served(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    db_path = tmp_path / "pathwatch.db"
    monkeypatch.setattr(app_module, "_UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(app_module, "_DB_PATH", db_path)
    uploads_mount = next(
        route for route in app_module.app.routes if route.name == "uploads"
    )
    monkeypatch.setattr(uploads_mount.app, "directory", str(upload_dir))
    monkeypatch.setattr(uploads_mount.app, "all_directories", [str(upload_dir)])

    def fake_audit_image(
        image_path: Path,
        *,
        override_lat: float | None = None,
        override_lon: float | None = None,
    ) -> HazardAssessment:
        assert image_path.is_file()
        assert override_lat is None
        assert override_lon is None
        return HazardAssessment(
            image_path=str(image_path),
            latitude=43.0766,
            longitude=-87.8812,
            gps_source="fallback",
            finding=VisionFinding(
                barrier_type="PATH_OBSTRUCTION",
                severity="HIGH",
                confidence=0.95,
                description="An obstruction blocks the sidewalk.",
                recommended_action="Remove the obstruction.",
            ),
        )

    monkeypatch.setattr(app_module, "audit_image", fake_audit_image)

    with TestClient(app_module.app) as client:
        assert upload_dir.is_dir()
        with sqlite3.connect(db_path) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(hazards)")
            }
        assert columns == {"id", "image_path", "assessment_json", "created_at"}

        responses = [
            client.post(
                "/api/audit",
                files={"photo": ("sidewalk.jpg", b"first image", "image/jpeg")},
            ),
            client.post(
                "/api/audit",
                files={"photo": ("sidewalk.jpg", b"second image", "image/jpeg")},
            ),
        ]
        assert all(response.status_code == 200 for response in responses)

        image_paths = [Path(response.json()["image_path"]) for response in responses]
        assert len({path.name for path in image_paths}) == 2
        assert [
            client.get(f"/uploads/{path.name}").content for path in image_paths
        ] == [b"first image", b"second image"]
        assert client.get("/api/hazards").json() == [
            response.json() for response in responses
        ]
        assert len(client.get("/api/export/geojson").json()["features"]) == 2
        hazard_id = responses[0].json()["id"]
        ticket = client.get(f"/api/export/ticket/{hazard_id}")
        assert ticket.status_code == 200
        assert "An obstruction blocks the sidewalk." in ticket.text
        assert ticket.headers["content-type"] == "text/markdown; charset=utf-8"
        assert ticket.headers["content-disposition"] == (
            f'attachment; filename="311_ticket_{hazard_id}.md"'
        )
        missing_ticket = client.get("/api/export/ticket/999")
        assert missing_ticket.status_code == 404
        assert missing_ticket.json()["detail"] == "Hazard not found"

    with TestClient(app_module.app) as client:
        assert len(client.get("/api/hazards").json()) == 2


def test_audit_forwards_user_coordinates_when_provided(tmp_path, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="web.app")
    upload_dir = tmp_path / "uploads"
    db_path = tmp_path / "pathwatch.db"
    monkeypatch.setattr(app_module, "_UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(app_module, "_DB_PATH", db_path)
    uploads_mount = next(
        route for route in app_module.app.routes if route.name == "uploads"
    )
    monkeypatch.setattr(uploads_mount.app, "directory", str(upload_dir))
    monkeypatch.setattr(uploads_mount.app, "all_directories", [str(upload_dir)])
    received = {}

    def fake_audit_image(
        image_path: Path,
        *,
        override_lat: float | None = None,
        override_lon: float | None = None,
    ) -> HazardAssessment:
        received.update(
            latitude=override_lat,
            longitude=override_lon,
        )
        return HazardAssessment(
            image_path=str(image_path),
            latitude=override_lat,
            longitude=override_lon,
            gps_source="manual",
            finding=VisionFinding(
                barrier_type="PATH_OBSTRUCTION",
                severity="HIGH",
                confidence=0.95,
                description="An obstruction blocks the sidewalk.",
                recommended_action="Remove the obstruction.",
            ),
        )

    monkeypatch.setattr(app_module, "audit_image", fake_audit_image)

    with TestClient(app_module.app) as client:
        partial_response = client.post(
            "/api/audit",
            files={"photo": ("sidewalk.jpg", b"image", "image/jpeg")},
            data={"latitude": "40.7128"},
        )
        assert partial_response.status_code == 422

        response = client.post(
            "/api/audit",
            files={"photo": ("sidewalk.jpg", b"image", "image/jpeg")},
            data={
                "latitude": "40.7128",
                "longitude": "-74.006",
            },
        )

    assert response.status_code == 200
    assert received == {
        "latitude": 40.7128,
        "longitude": -74.006,
    }
    assert response.json()["gps_source"] == "manual"
    assert "latitude=40.7128 longitude=-74.006 gps_source=manual" in caplog.text


def test_delete_hazard_removes_record_and_uploaded_image(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    db_path = tmp_path / "pathwatch.db"
    monkeypatch.setattr(app_module, "_UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(app_module, "_DB_PATH", db_path)
    image_path = upload_dir / "audit.jpg"
    hazard = HazardAssessment(
        image_path=str(image_path),
        latitude=43.0766,
        longitude=-87.8812,
        gps_source="fallback",
        finding=VisionFinding(
            barrier_type="PATH_OBSTRUCTION",
            severity="HIGH",
            confidence=0.95,
            description="An obstruction blocks the sidewalk.",
            recommended_action="Remove the obstruction.",
        ),
    )

    with TestClient(app_module.app) as client:
        image_path.write_bytes(b"uploaded image")
        with sqlite3.connect(db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO hazards (image_path, assessment_json, created_at)
                VALUES (?, ?, ?)
                """,
                (str(image_path), hazard.model_dump_json(), "2026-11-12T00:00:00+00:00"),
            )
            hazard_id = cursor.lastrowid

        assert [item["id"] for item in client.get("/api/hazards").json()] == [hazard_id]
        deleted = client.delete(f"/api/hazards/{hazard_id}")

        assert deleted.status_code == 200
        assert deleted.json() == {"status": "deleted", "id": hazard_id}
        assert not image_path.exists()
        assert client.get("/api/hazards").json() == []

        missing = client.delete(f"/api/hazards/{hazard_id}")
        assert missing.status_code == 404
        assert missing.json() == {"detail": "Hazard not found"}


def test_ticket_export_uses_sqlite_id_after_earlier_hazard_is_deleted(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "pathwatch.db"
    monkeypatch.setattr(app_module, "_DB_PATH", db_path)
    assessments = [
        HazardAssessment(
            image_path="",
            latitude=43.0766,
            longitude=-87.8812,
            gps_source="fallback",
            finding=VisionFinding(
                barrier_type="PATH_OBSTRUCTION",
                severity="HIGH",
                confidence=0.95,
                description=description,
                recommended_action="Remove the obstruction.",
            ),
        )
        for description in ("First hazard description.", "Second hazard description.")
    ]

    with TestClient(app_module.app) as client:
        with sqlite3.connect(db_path) as connection:
            for hazard_id, assessment in zip((10, 42), assessments):
                connection.execute(
                    """
                    INSERT INTO hazards (id, image_path, assessment_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (hazard_id, "", assessment.model_dump_json(), "2026-11-12"),
                )

        assert client.delete("/api/hazards/10").status_code == 200
        ticket = client.get("/api/export/ticket/42")

        assert ticket.status_code == 200
        assert "Second hazard description." in ticket.text
        assert client.get("/api/export/ticket/10").status_code == 404
