import math

Point = tuple[float, float]


def _angle_between(v1: Point, v2: Point) -> float:
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return 0.0
    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def joint_angle(a: Point, b: Point, c: Point) -> float:
    """Angle at b (degrees, 0..180) formed by a-b-c."""
    return _angle_between((a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1]))


def trunk_tilt(shoulder: Point, hip: Point) -> float:
    """Angle between the hip->shoulder line and vertical-up (image y grows downward)."""
    return _angle_between((shoulder[0] - hip[0], shoulder[1] - hip[1]), (0.0, -1.0))
