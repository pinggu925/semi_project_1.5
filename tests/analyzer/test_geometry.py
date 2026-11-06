import pytest

from app.analyzer.geometry import joint_angle, trunk_tilt


def test_right_angle():
    assert joint_angle((0, 1), (0, 0), (1, 0)) == pytest.approx(90)


def test_straight_leg():
    assert joint_angle((0, 0), (0, 1), (0, 2)) == pytest.approx(180)


def test_upright_trunk():
    assert trunk_tilt((0.5, 0.2), (0.5, 0.5)) == pytest.approx(0)


def test_horizontal_trunk():
    assert trunk_tilt((0.8, 0.5), (0.5, 0.5)) == pytest.approx(90)


def test_degenerate_points_do_not_raise():
    assert joint_angle((0, 0), (0, 0), (1, 0)) == 0.0
    assert trunk_tilt((0.5, 0.5), (0.5, 0.5)) == 0.0
