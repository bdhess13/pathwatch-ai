"""
tests/test_ingest.py
====================
Unit tests for core/ingest.py.

Strategy
--------
- GPS tests use synthetic in-memory JPEG images created with Pillow so no
  real photos are required.  One image carries a valid GPSInfo EXIF IFD;
  another has no EXIF at all.
- Frame-extraction tests create a tiny synthetic video with OpenCV and verify
  that the blur-threshold filter correctly separates sharp from blurry frames.
- All tests avoid network I/O and filesystem side-effects beyond tmp_path.
"""

import io
import struct
from pathlib import Path

import pytest

from core.ingest import _dms_to_decimal, extract_gps, extract_sharp_frames


# ---------------------------------------------------------------------------
# _dms_to_decimal — unit tests
# ---------------------------------------------------------------------------

class TestDmsToDecimal:
    def test_positive_latitude(self):
        # 43° 4' 35.76" N  → 43 + 4/60 + 35.76/3600 ≈ 43.0766
        result = _dms_to_decimal([(43, 1), (4, 1), (3576, 100)], "N")
        assert result == pytest.approx(43.0766, rel=1e-4)

    def test_negative_longitude(self):
        # 87° 52' 52.32" W → -(87 + 52/60 + 52.32/3600) ≈ -87.8812
        result = _dms_to_decimal([(87, 1), (52, 1), (5232, 100)], "W")
        assert result == pytest.approx(-87.8812, rel=1e-4)

    def test_south_is_negative(self):
        result = _dms_to_decimal([(10, 1), (0, 1), (0, 1)], "S")
        assert result < 0

    def test_east_is_positive(self):
        result = _dms_to_decimal([(90, 1), (0, 1), (0, 1)], "E")
        assert result > 0

    def test_float_inputs(self):
        result = _dms_to_decimal([43.0, 4.0, 35.76], "N")
        assert result == pytest.approx(43.0766, rel=1e-4)


# ---------------------------------------------------------------------------
# Helpers to build synthetic JPEG files
# ---------------------------------------------------------------------------

def _make_plain_jpeg(tmp_path: Path, name: str = "plain.jpg") -> Path:
    """Create a minimal 16×16 RGB JPEG with no EXIF data."""
    from PIL import Image

    img = Image.new("RGB", (16, 16), color=(128, 64, 0))
    out = tmp_path / name
    img.save(out, format="JPEG")
    return out


def _make_gps_jpeg(tmp_path: Path, lat: float, lon: float, name: str = "gps.jpg") -> Path:
    """Create a JPEG with a synthetic GPSInfo EXIF IFD embedded via piexif.

    Falls back to writing a plain JPEG (no EXIF) if piexif is not installed,
    which means ``extract_gps`` should then return the fallback coordinates —
    the test is skipped in that scenario to avoid a false failure.
    """
    try:
        import piexif  # optional, used only in this helper
    except ImportError:
        return None  # type: ignore[return-value]  # caller must handle None

    from PIL import Image

    def _decimal_to_dms_rational(value: float):
        value = abs(value)
        d = int(value)
        m = int((value - d) * 60)
        s = round(((value - d) * 60 - m) * 60 * 100)
        return ((d, 1), (m, 1), (s, 100))

    lat_ref = b"N" if lat >= 0 else b"S"
    lon_ref = b"E" if lon >= 0 else b"W"

    gps_ifd = {
        piexif.GPSIFD.GPSLatitudeRef: lat_ref,
        piexif.GPSIFD.GPSLatitude: _decimal_to_dms_rational(lat),
        piexif.GPSIFD.GPSLongitudeRef: lon_ref,
        piexif.GPSIFD.GPSLongitude: _decimal_to_dms_rational(lon),
    }
    exif_dict = {"GPS": gps_ifd}
    exif_bytes = piexif.dump(exif_dict)

    img = Image.new("RGB", (16, 16), color=(0, 128, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif_bytes)
    buf.seek(0)

    out = tmp_path / name
    out.write_bytes(buf.read())
    return out


# ---------------------------------------------------------------------------
# extract_gps — fallback path (no EXIF)
# ---------------------------------------------------------------------------

class TestExtractGpsFallback:
    def test_no_exif_returns_fallback(self, tmp_path):
        img = _make_plain_jpeg(tmp_path)
        lat, lon, source = extract_gps(img)
        assert source == "fallback"
        assert lat == pytest.approx(43.0766)
        assert lon == pytest.approx(-87.8812)

    def test_nonexistent_file_returns_fallback(self, tmp_path):
        lat, lon, source = extract_gps(tmp_path / "ghost.jpg")
        assert source == "fallback"
        assert lat == pytest.approx(43.0766)
        assert lon == pytest.approx(-87.8812)

    def test_fallback_source_string_is_literal(self, tmp_path):
        img = _make_plain_jpeg(tmp_path)
        _, _, source = extract_gps(img)
        assert source == "fallback"


# ---------------------------------------------------------------------------
# extract_gps — happy path (EXIF GPS present)
# ---------------------------------------------------------------------------

class TestExtractGpsExif:
    def test_exif_gps_round_trip(self, tmp_path):
        piexif = pytest.importorskip("piexif", reason="piexif not installed")
        img = _make_gps_jpeg(tmp_path, lat=43.0766, lon=-87.8812)
        if img is None:
            pytest.skip("piexif unavailable")
        lat, lon, source = extract_gps(img)
        assert source == "exif"
        # Allow ~0.001° tolerance for the integer DMS round-trip
        assert lat == pytest.approx(43.0766, abs=0.001)
        assert lon == pytest.approx(-87.8812, abs=0.001)

    def test_exif_source_string_is_literal(self, tmp_path):
        piexif = pytest.importorskip("piexif", reason="piexif not installed")
        img = _make_gps_jpeg(tmp_path, lat=40.7128, lon=-74.0060)
        if img is None:
            pytest.skip("piexif unavailable")
        _, _, source = extract_gps(img)
        assert source == "exif"


# ---------------------------------------------------------------------------
# extract_sharp_frames — synthetic video
# ---------------------------------------------------------------------------

def _make_synthetic_video(tmp_path: Path, num_frames: int = 10, fps: int = 10) -> Path:
    """Write a tiny synthetic .mp4: alternating sharp (checkerboard) and blurry
    (solid grey) frames so we can test the Laplacian filter."""
    import cv2
    import numpy as np

    out_path = tmp_path / "test_walk.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, fps, (64, 64))

    for i in range(num_frames):
        if i % 2 == 0:
            # Sharp: 8×8 checkerboard
            tile = np.zeros((8, 8), dtype=np.uint8)
            tile[::2, ::2] = 255
            tile[1::2, 1::2] = 255
            grey = np.tile(tile, (8, 8))
            frame = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
        else:
            # Blurry: uniform grey
            frame = np.full((64, 64, 3), 128, dtype=np.uint8)
        writer.write(frame)

    writer.release()
    return out_path


class TestExtractSharpFrames:
    def test_returns_list_of_paths(self, tmp_path):
        cv2 = pytest.importorskip("cv2", reason="opencv not installed")
        video = _make_synthetic_video(tmp_path, num_frames=10, fps=10)
        output_dir = tmp_path / "frames"
        frames = extract_sharp_frames(video, output_dir, sample_every_sec=0.1)
        assert isinstance(frames, list)
        assert all(isinstance(p, Path) for p in frames)

    def test_output_dir_created(self, tmp_path):
        cv2 = pytest.importorskip("cv2", reason="opencv not installed")
        video = _make_synthetic_video(tmp_path)
        output_dir = tmp_path / "does_not_exist_yet" / "frames"
        assert not output_dir.exists()
        extract_sharp_frames(video, output_dir, sample_every_sec=0.1)
        assert output_dir.exists()

    def test_sharp_frames_saved_as_jpeg(self, tmp_path):
        cv2 = pytest.importorskip("cv2", reason="opencv not installed")
        video = _make_synthetic_video(tmp_path, num_frames=10, fps=10)
        output_dir = tmp_path / "frames"
        frames = extract_sharp_frames(video, output_dir, sample_every_sec=0.1)
        for p in frames:
            assert p.suffix == ".jpg"
            assert p.exists()

    def test_blurry_frames_excluded(self, tmp_path):
        """With a high blur_threshold, the uniform-grey frames must be dropped."""
        cv2 = pytest.importorskip("cv2", reason="opencv not installed")
        video = _make_synthetic_video(tmp_path, num_frames=10, fps=10)
        output_dir = tmp_path / "frames_strict"
        # Checkerboard has high Laplacian variance; solid grey has near-zero.
        frames = extract_sharp_frames(
            video, output_dir, sample_every_sec=0.1, blur_threshold=100.0
        )
        # At least some frames should have been excluded.
        all_candidate_count = 10  # one per 0.1s in a 1s video at 10fps
        assert len(frames) < all_candidate_count

    def test_missing_video_returns_empty(self, tmp_path):
        cv2 = pytest.importorskip("cv2", reason="opencv not installed")
        frames = extract_sharp_frames(
            tmp_path / "no_such_file.mp4",
            tmp_path / "out",
        )
        assert frames == []

    def test_frames_are_sorted(self, tmp_path):
        cv2 = pytest.importorskip("cv2", reason="opencv not installed")
        video = _make_synthetic_video(tmp_path, num_frames=20, fps=10)
        output_dir = tmp_path / "frames_sorted"
        frames = extract_sharp_frames(video, output_dir, sample_every_sec=0.1)
        assert frames == sorted(frames)
