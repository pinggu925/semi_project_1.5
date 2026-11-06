from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class Landmark:
    x: float
    y: float
    visibility: float


Landmarks = dict[str, Landmark]
JOINTS = ("shoulder", "hip", "knee", "ankle")
LANDMARK_NAMES = tuple(f"{side}_{j}" for side in ("left", "right") for j in JOINTS)


class Phase(str, Enum):
    STANDING = "STANDING"
    DESCENDING = "DESCENDING"
    BOTTOM = "BOTTOM"
    ASCENDING = "ASCENDING"


@dataclass
class RepResult:
    rep_no: int
    min_knee_angle: float
    max_trunk_tilt: float
    score: int
    feedback_codes: list[str] = field(default_factory=list)


@dataclass
class FrameResult:
    phase: Phase
    rep_count: int
    knee_angle: float | None
    trunk_tilt: float | None
    feedback: list[str]
    warning: str | None
    side: str | None
    completed_rep: RepResult | None = None
