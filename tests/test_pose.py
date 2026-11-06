import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.analyzer.squat import SquatAnalyzer
from app.analyzer.thresholds import load_thresholds
from app.analyzer.types import LANDMARK_NAMES
from app.pose import FrameDecodeError, MediaPipeLandmarkSource, iter_video_landmarks

DATA = Path("tests/data")
MANIFEST = json.loads((DATA / "squat_manifest.json").read_text())
VIDEOS = [(DATA / name, spec["expected_reps"]) for name, spec in MANIFEST.items()]
HAVE_VIDEO = any(p.exists() for p, _ in VIDEOS)


@pytest.fixture(scope="module")
def source():
    return MediaPipeLandmarkSource()


def test_garbage_bytes_raise_decode_error(source):
    with pytest.raises(FrameDecodeError):
        source.landmarks_from_jpeg(b"not a jpeg")


def test_empty_bytes_raise_decode_error(source):
    with pytest.raises(FrameDecodeError):
        source.landmarks_from_jpeg(b"")


def test_blank_image_returns_none(source):
    ok, buf = cv2.imencode(".jpg", np.zeros((240, 320, 3), np.uint8))
    assert ok and source.landmarks_from_jpeg(buf.tobytes()) is None


@pytest.mark.skipif(not HAVE_VIDEO, reason="record tests/data/squat_5reps.mp4 (see README)")
def test_landmark_keys_cover_both_sides():
    path = next(p for p, _ in VIDEOS if p.exists())
    found = next(lm for lm in iter_video_landmarks(path) if lm)
    assert set(found) == set(LANDMARK_NAMES)


@pytest.mark.skipif(not HAVE_VIDEO, reason="record tests/data/squat_5reps.mp4 (see README)")
@pytest.mark.parametrize("path,expected", [v for v in VIDEOS if v[0].exists()])
def test_sample_video_rep_count_matches_manifest(path, expected):
    a = SquatAnalyzer(load_thresholds("config/thresholds.toml"))
    last = None
    for lm in iter_video_landmarks(path):
        last = a.update(lm)
    assert last is not None and last.rep_count == expected
