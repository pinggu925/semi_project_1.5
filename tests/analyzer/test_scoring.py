from app.analyzer.squat import score_rep
from app.analyzer.thresholds import load_thresholds

T = load_thresholds("config/thresholds.toml")


def test_perfect_rep():
    assert score_rep(85, 10, T) == (100, [])


def test_both_faults_and_floor():
    score, codes = score_rep(180, 90, T)
    assert score == 0 and codes == ["DEPTH_SHALLOW", "TRUNK_LEAN"]


def test_partial_penalties():
    assert score_rep(110, 50, T) == (70, ["DEPTH_SHALLOW", "TRUNK_LEAN"])
