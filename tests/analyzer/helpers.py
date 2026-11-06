import math

from app.analyzer.types import Landmark, Landmarks

WINDOW = 5
_KNEE, _ANKLE, _LEN = (0.4, 0.6), (0.4, 0.9), 0.3


def _side(knee_angle: float, trunk: float, visibility: float, mirror: bool) -> dict[str, Landmark]:
    th, tt = math.radians(knee_angle), math.radians(trunk)
    hip = (_KNEE[0] + _LEN * math.sin(th), _KNEE[1] + _LEN * math.cos(th))
    shoulder = (hip[0] + _LEN * math.sin(tt), hip[1] - _LEN * math.cos(tt))
    pts = {"shoulder": shoulder, "hip": hip, "knee": _KNEE, "ankle": _ANKLE}
    prefix = "right" if mirror else "left"
    return {
        f"{prefix}_{k}": Landmark(1 - x if mirror else x, y, visibility) for k, (x, y) in pts.items()
    }


def make_landmarks(
    knee_angle: float,
    trunk_tilt: float = 10.0,
    visibility: float = 0.9,
    right_visibility: float | None = None,
) -> Landmarks:
    rv = visibility if right_visibility is None else right_visibility
    return {**_side(knee_angle, trunk_tilt, visibility, False), **_side(knee_angle, trunk_tilt, rv, True)}


def angles_to_frames(angles: list[float], trunk: float = 10.0) -> list[Landmarks]:
    return [make_landmarks(a, trunk) for a in angles for _ in range(WINDOW)]


def squat_angles(min_knee: float) -> list[float]:
    down = list(range(170, int(min_knee) - 1, -5))
    return down + down[-2::-1]


def squat_sequence(min_knee: float, trunk: float = 10.0) -> list[Landmarks]:
    return angles_to_frames(squat_angles(min_knee), trunk)
