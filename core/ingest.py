"""
core/ingest.py
==============
PathWatch AI — Image and video ingestion utilities.

Two public functions:

* ``extract_gps_from_image`` — reads EXIF GPS tags from a photo and converts
                               DMS values to decimal degrees, then uses
                               supplied coordinates or the campus fallback.
* ``extract_sharp_frames`` — samples non-blurry keyframes from a walk-along
                             video using OpenCV's Laplacian variance metric.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Default fallback coordinates (UWM campus, Milwaukee, WI) — AGENTS.md guardrail 4.
_FALLBACK_LAT: float = 43.0766
_FALLBACK_LON: float = -87.8812


# ---------------------------------------------------------------------------
# GPS helpers
# ---------------------------------------------------------------------------

def _dms_to_decimal(dms_values: list, ref: str) -> float:
    """Convert a list of IFDRational/tuple DMS values to a signed decimal degree.

    ``dms_values`` is the raw sequence returned by Pillow's EXIF tag 34853
    (GPSInfo):  [degrees, minutes, seconds], each as a (numerator, denominator)
    tuple or a float.
    """
    def _to_float(v) -> float:
        # Pillow returns IFDRational objects that support __float__, or plain
        # (numerator, denominator) tuples from older builds.
        if hasattr(v, "numerator"):  # IFDRational
            return float(v)
        if isinstance(v, tuple):
            if not v[1]:
                raise ValueError("Invalid EXIF rational with a zero denominator")
            return v[0] / v[1]
        return float(v)

    if len(dms_values) != 3:
        raise ValueError("GPS coordinates must contain degrees, minutes, and seconds")

    degrees = _to_float(dms_values[0])
    minutes = _to_float(dms_values[1])
    seconds = _to_float(dms_values[2])
    decimal = degrees + minutes / 60.0 + seconds / 3600.0
    if isinstance(ref, bytes):
        ref = ref.decode("ascii")
    ref = ref.strip().upper()
    if ref in ("S", "W"):
        decimal = -decimal
    elif ref not in ("N", "E"):
        raise ValueError(f"Invalid GPS coordinate reference: {ref!r}")
    return decimal


# ---------------------------------------------------------------------------
# Public: extract_gps_from_image
# ---------------------------------------------------------------------------

def extract_gps_from_image(
    image_path: Path,
    override_lat: float | None = None,
    override_lon: float | None = None,
) -> tuple[float, float, str]:
    """Extract GPS coordinates from image EXIF metadata.

    Uses Pillow to read the GPSInfo IFD (tag 34853), converting DMS triplets
    to signed decimal degrees. EXIF coordinates take precedence over supplied
    coordinates.

    Args:
        image_path: Path to a JPEG, PNG, TIFF, or other Pillow-supported image.
        override_lat: Optional user-supplied latitude used when EXIF GPS is absent.
        override_lon: Optional user-supplied longitude used when EXIF GPS is absent.

    Returns:
        A ``(latitude, longitude, gps_source)`` tuple where ``gps_source`` is
        ``"exif"`` on success, ``"manual"`` when supplied coordinates are used,
        or ``"fallback"`` when neither EXIF nor override coordinates are available.
        The fallback coordinates are ``(43.0766, -87.8812)`` (UWM campus).
    """
    try:
        from PIL import Image
        from PIL.ExifTags import GPSTAGS

        with Image.open(image_path) as img:
            gps_ifd = img.getexif().get_ifd(0x8825)

        gps = {GPSTAGS.get(key, key): value for key, value in gps_ifd.items()}
        lat = _dms_to_decimal(gps["GPSLatitude"], gps["GPSLatitudeRef"])
        lon = _dms_to_decimal(gps["GPSLongitude"], gps["GPSLongitudeRef"])
        return lat, lon, "exif"

    except Exception as exc:  # noqa: BLE001
        logger.debug("GPS extraction failed for %s: %s", image_path, exc)

    if override_lat is not None and override_lon is not None:
        return float(override_lat), float(override_lon), "manual"

    logger.debug("No EXIF GPS or complete override supplied for %s — using fallback", image_path)
    return _FALLBACK_LAT, _FALLBACK_LON, "fallback"


# Backwards-compatible name for existing callers.
extract_gps = extract_gps_from_image


# ---------------------------------------------------------------------------
# Public: extract_sharp_frames
# ---------------------------------------------------------------------------

def extract_sharp_frames(
    video_path: Path,
    output_dir: Path,
    sample_every_sec: float = 2.0,
    blur_threshold: float = 80.0,
) -> list[Path]:
    """Sample non-blurry keyframes from a walk-along video.

    Steps every ``sample_every_sec`` seconds through the video, computes the
    variance of the Laplacian (a standard blur metric), and saves frames whose
    score exceeds ``blur_threshold`` as JPEG files in ``output_dir``.

    Args:
        video_path:       Path to the source ``.mp4`` (or any OpenCV-readable
                          video format).
        output_dir:       Directory where extracted JPEG frames are written.
                          Created automatically if it does not exist.
        sample_every_sec: Interval between candidate frames in seconds.
        blur_threshold:   Laplacian variance below this value is considered
                          too blurry to be useful.  Lower = more permissive.

    Returns:
        Sorted list of ``Path`` objects for the saved frame images.
        Returns an empty list if the video cannot be opened.
    """
    try:
        import cv2  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "opencv-python-headless is required for video frame extraction. "
            "Install it with: pip install opencv-python-headless"
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.warning("Could not open video: %s", video_path)
        return saved

    try:
        fps: float = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step_frames = max(1, int(fps * sample_every_sec))
        frame_idx: int = 0
        saved_count: int = 0

        while True:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok:
                break

            # Blur detection: convert to greyscale, compute Laplacian variance.
            grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            sharpness = cv2.Laplacian(grey, cv2.CV_64F).var()

            if sharpness >= blur_threshold:
                out_path = output_dir / f"frame_{saved_count:05d}.jpg"
                cv2.imwrite(str(out_path), frame)
                saved.append(out_path)
                saved_count += 1
                logger.debug(
                    "Saved frame %d (t=%.1fs, sharpness=%.1f) → %s",
                    frame_idx,
                    frame_idx / fps,
                    sharpness,
                    out_path,
                )
            else:
                logger.debug(
                    "Skipped blurry frame %d (sharpness=%.1f < %.1f)",
                    frame_idx,
                    sharpness,
                    blur_threshold,
                )

            frame_idx += step_frames

    finally:
        cap.release()

    saved.sort()
    logger.info("Extracted %d sharp frames from %s", len(saved), video_path)
    return saved
