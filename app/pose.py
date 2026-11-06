"""OpenCV frame decoding + MediaPipe Pose landmark extraction."""
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import cv2
import mediapipe as mp
import numpy as np

from app.analyzer.types import Landmark, Landmarks

MAX_W, MAX_H = 320, 240
_PL = mp.solutions.pose.PoseLandmark
_INDEX = {
    f"{side}_{joint}": getattr(_PL, f"{side.upper()}_{joint.upper()}")
    for side in ("left", "right")
    for joint in ("shoulder", "hip", "knee", "ankle")
}


class FrameDecodeError(Exception):
    """Raised when bytes are not a decodable image."""


class LandmarkSource(Protocol):
    def landmarks_from_jpeg(self, data: bytes) -> Landmarks | None: ...


def decode_jpeg(data: bytes) -> np.ndarray:
    if not data:
        raise FrameDecodeError("empty frame")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise FrameDecodeError("not a decodable image")
    return img


def _fit(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    scale = min(MAX_W / w, MAX_H / h, 1.0)
    if scale < 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return img


class MediaPipeLandmarkSource:
    """Not thread-safe: use one instance per worker/connection."""

    def __init__(self, model_complexity: int = 1):
        # 1 = "full" model, bundled in the mediapipe wheel (0 = lite needs a download on first use)
        self._pose = mp.solutions.pose.Pose(
            static_image_mode=False, model_complexity=model_complexity,
            min_detection_confidence=0.5, min_tracking_confidence=0.5,
        )

    def landmarks_from_bgr(self, img: np.ndarray) -> Landmarks | None:
        rgb = cv2.cvtColor(_fit(img), cv2.COLOR_BGR2RGB)
        res = self._pose.process(rgb)
        if not res.pose_landmarks:
            return None
        lms = res.pose_landmarks.landmark
        return {name: Landmark(lms[i].x, lms[i].y, lms[i].visibility) for name, i in _INDEX.items()}

    def landmarks_from_jpeg(self, data: bytes) -> Landmarks | None:
        return self.landmarks_from_bgr(decode_jpeg(data))

    def close(self) -> None:
        self._pose.close()


def iter_video_landmarks(
    path: Path, source: MediaPipeLandmarkSource | None = None, target_fps: float = 10.0
) -> Iterator[Landmarks | None]:
    src = source or MediaPipeLandmarkSource()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise FileNotFoundError(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or target_fps
    step = max(1, round(fps / target_fps))
    i = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if i % step == 0:
                yield src.landmarks_from_bgr(frame)
            i += 1
    finally:
        cap.release()
        if source is None:
            src.close()
