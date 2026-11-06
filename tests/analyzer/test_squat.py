from app.analyzer.squat import FEEDBACK_MESSAGES, SquatAnalyzer
from app.analyzer.thresholds import Thresholds, load_thresholds
from app.analyzer.types import Phase
from tests.analyzer.helpers import angles_to_frames, make_landmarks, squat_sequence

CFG = "config/thresholds.toml"


def analyzer() -> SquatAnalyzer:
    return SquatAnalyzer(load_thresholds(CFG))


def run(a, frames):
    results = [a.update(f) for f in frames]
    return results, [r.completed_rep for r in results if r.completed_rep]


def test_five_good_squats_count_five():
    a = analyzer()
    for _ in range(5):
        run(a, squat_sequence(min_knee=85))
    assert a.update(make_landmarks(170)).rep_count == 5


def test_good_rep_scores_100_without_feedback():
    _, reps = run(analyzer(), squat_sequence(85))
    assert len(reps) == 1 and reps[0].score == 100 and reps[0].feedback_codes == []
    assert reps[0].rep_no == 1


def test_shallow_rep_flags_depth():
    _, reps = run(analyzer(), squat_sequence(120))
    assert "DEPTH_SHALLOW" in reps[0].feedback_codes and reps[0].score == 60


def test_leaning_rep_flags_trunk():
    _, reps = run(analyzer(), squat_sequence(85, trunk=60))
    assert reps[0].feedback_codes == ["TRUNK_LEAN"] and reps[0].score == 70


def test_feedback_persists_until_next_rep():
    a = analyzer()
    run(a, squat_sequence(120))
    assert a.update(make_landmarks(170)).feedback == ["DEPTH_SHALLOW"]


def test_half_squat_above_descend_threshold_counts_nothing():
    results, reps = run(analyzer(), squat_sequence(145))
    assert reps == [] and results[-1].rep_count == 0 and results[-1].phase == Phase.STANDING


def test_jitter_around_stand_boundary_counts_once():
    down = list(range(170, 84, -5))
    up = list(range(90, 156, 5))
    jitter = [158, 162] * 15
    frames = angles_to_frames(down + up) + [make_landmarks(a) for a in jitter] + angles_to_frames([170])
    results, reps = run(analyzer(), frames)
    assert len(reps) == 1 and results[-1].rep_count == 1


def test_landmarks_none_mid_rep_freezes_state():
    a = analyzer()
    down = list(range(170, 84, -5))
    run(a, angles_to_frames(down + [90, 100, 110, 120]))
    before = a.update(make_landmarks(120))
    frozen = [a.update(None) for _ in range(20)]
    assert all(r.warning == "NOT_FULL_BODY" for r in frozen)
    assert all(r.rep_count == 0 and r.phase == before.phase for r in frozen)
    _, reps = run(a, angles_to_frames(list(range(125, 171, 5))))
    assert len(reps) == 1


def test_low_visibility_is_not_full_body():
    r = analyzer().update(make_landmarks(170, visibility=0.2))
    assert r.warning == "NOT_FULL_BODY" and r.knee_angle is None


def test_picks_more_visible_side():
    r = analyzer().update(make_landmarks(170, visibility=0.9, right_visibility=0.2))
    assert r.side == "left" and r.warning is None
    r = analyzer().update(make_landmarks(170, visibility=0.2, right_visibility=0.9))
    assert r.side == "right"


def test_angle_window_never_exceeds_five():
    a = analyzer()
    for _ in range(50):
        a.update(make_landmarks(170))
    assert len(a.angle_window) == 5


def test_feedback_messages_match_spec():
    assert FEEDBACK_MESSAGES == {
        "DEPTH_SHALLOW": "더 깊이 앉으세요",
        "TRUNK_LEAN": "상체를 세우세요",
        "NOT_FULL_BODY": "전신이 보이게 서주세요",
    }


def test_load_thresholds_reads_toml(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text(
        "descend_below = 130\nstand_above = 165\nreversal_delta = 4\ndepth_target = 95\n"
        "trunk_max = 40\nmin_visibility = 0.6\nsmoothing_window = 3\n"
    )
    assert load_thresholds(str(p)) == Thresholds(130, 165, 4, 95, 40, 0.6, 3)


def test_one_invisible_joint_is_not_full_body():
    """Feet cut off at the bottom of the frame: the mean visibility passes but the ankle is a guess."""
    from dataclasses import replace

    from app.analyzer.thresholds import Thresholds
    from app.analyzer.squat import SquatAnalyzer
    from tests.analyzer.helpers import make_landmarks

    a = SquatAnalyzer(Thresholds())
    for angle in [170, 120, 90, 120, 170] * 4:
        lm = make_landmarks(angle)
        for side in ("left", "right"):
            lm[f"{side}_ankle"] = replace(lm[f"{side}_ankle"], visibility=0.05)
        res = a.update(lm)
        assert res.warning == "NOT_FULL_BODY"
    assert res.rep_count == 0
