import tomllib
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Thresholds:
    descend_below: float = 140
    stand_above: float = 160
    reversal_delta: float = 5
    depth_target: float = 100
    trunk_max: float = 45
    min_visibility: float = 0.5
    smoothing_window: int = 5


def load_thresholds(path: str) -> Thresholds:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    known = {f.name for f in fields(Thresholds)}
    values = {k: v for k, v in data.items() if k in known}
    if "smoothing_window" in values:
        values["smoothing_window"] = int(values["smoothing_window"])
    return Thresholds(**values)
