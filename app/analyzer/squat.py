"""Squat rep counter and posture feedback. Pure Python: knows nothing about servers or storage."""
from collections import deque

from app.analyzer.geometry import joint_angle, trunk_tilt
from app.analyzer.thresholds import Thresholds
from app.analyzer.types import JOINTS, FrameResult, Landmarks, Phase, RepResult

FEEDBACK_MESSAGES = {
    "DEPTH_SHALLOW": "더 깊이 앉으세요",
    "TRUNK_LEAN": "상체를 세우세요",
    "NOT_FULL_BODY": "전신이 보이게 서주세요",
}


def score_rep(min_knee: float, max_tilt: float, t: Thresholds) -> tuple[int, list[str]]:
    codes = []
    if min_knee > t.depth_target:
        codes.append("DEPTH_SHALLOW")
    if max_tilt > t.trunk_max:
        codes.append("TRUNK_LEAN")
    penalty = min(50, 2 * max(0, min_knee - t.depth_target)) + min(50, 2 * max(0, max_tilt - t.trunk_max))
    return int(round(max(0, min(100, 100 - penalty)))), codes


def select_side(landmarks: Landmarks) -> tuple[str, float]:
    """Return (side, mean visibility) of the better-visible side."""
    best = ("left", -1.0)
    for side in ("left", "right"):
        try:
            vis = sum(landmarks[f"{side}_{j}"].visibility for j in JOINTS) / len(JOINTS)
        except KeyError:
            continue
        if vis > best[1]:
            best = (side, vis)
    return best


class SquatAnalyzer:
    def __init__(self, thresholds: Thresholds):
        self.t = thresholds
        self.phase = Phase.STANDING
        self.rep_count = 0
        self._knees: deque[float] = deque(maxlen=thresholds.smoothing_window)
        self._tilts: deque[float] = deque(maxlen=thresholds.smoothing_window)
        self._prev_knee: float | None = None
        self._rep_min: float | None = None
        self._rep_max_tilt: float = 0.0
        self._last_feedback: list[str] = []

    @property
    def angle_window(self) -> list[float]:
        return list(self._knees)

    @property
    def current_min_angle(self) -> float | None:
        return self._rep_min

    def _frozen(self) -> FrameResult:
        return FrameResult(self.phase, self.rep_count, None, None, list(self._last_feedback), "NOT_FULL_BODY", None)

    def update(self, landmarks: Landmarks | None) -> FrameResult:
        if not landmarks:
            return self._frozen()
        side, vis = select_side(landmarks)
        if vis < self.t.min_visibility:
            return self._frozen()

        p = {j: (landmarks[f"{side}_{j}"].x, landmarks[f"{side}_{j}"].y) for j in JOINTS}
        self._knees.append(joint_angle(p["hip"], p["knee"], p["ankle"]))
        self._tilts.append(trunk_tilt(p["shoulder"], p["hip"]))
        knee = sum(self._knees) / len(self._knees)
        tilt = sum(self._tilts) / len(self._tilts)

        completed = self._step(knee, tilt)
        self._prev_knee = knee
        return FrameResult(self.phase, self.rep_count, knee, tilt, list(self._last_feedback), None, side, completed)

    def _step(self, knee: float, tilt: float) -> RepResult | None:
        t = self.t
        if self.phase == Phase.STANDING:
            if knee < t.descend_below:
                self.phase = Phase.DESCENDING
                self._rep_min, self._rep_max_tilt = knee, tilt
            return None

        self._rep_min = min(self._rep_min, knee)
        self._rep_max_tilt = max(self._rep_max_tilt, tilt)

        if self.phase == Phase.DESCENDING:
            if knee >= self._rep_min + t.reversal_delta:
                self.phase = Phase.BOTTOM
        elif self.phase == Phase.BOTTOM:
            if knee < self._rep_min + 1e-9 and knee < (self._prev_knee or knee):
                self.phase = Phase.DESCENDING
            elif self._prev_knee is not None and knee > self._prev_knee:
                self.phase = Phase.ASCENDING

        if self.phase in (Phase.BOTTOM, Phase.ASCENDING) and knee > t.stand_above:
            return self._finish_rep()
        return None

    def _finish_rep(self) -> RepResult:
        self.rep_count += 1
        score, codes = score_rep(self._rep_min, self._rep_max_tilt, self.t)
        rep = RepResult(self.rep_count, round(self._rep_min, 1), round(self._rep_max_tilt, 1), score, codes)
        self._last_feedback = codes
        self.phase = Phase.STANDING
        self._rep_min, self._rep_max_tilt = None, 0.0
        return rep
