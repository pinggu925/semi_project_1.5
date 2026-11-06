import asyncio
import uuid

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.db import create_engine_and_sessionmaker
from app.main import create_app
from app.pose import FrameDecodeError
from app.ws import LatestFrameSlot
from tests.analyzer.helpers import make_landmarks, squat_sequence
from tests.conftest import TEST_DB_URL, TEST_REDIS_URL

pytestmark = pytest.mark.integration

GOOD = make_landmarks(170)


class FakeSource:
    """Frames are b"<index>" into a shared list, or b"none" / b"garbage"."""

    def __init__(self, frames):
        self.frames = frames
        self.calls = 0

    def landmarks_from_jpeg(self, data: bytes):
        self.calls += 1
        if data == b"garbage":
            raise FrameDecodeError("bad")
        if data == b"none":
            return None
        return self.frames[int(data)]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _sql(query, **params):
    engine, maker = create_engine_and_sessionmaker(Settings(database_url=TEST_DB_URL))
    try:
        async with maker() as db:
            res = await db.execute(text(query), params)
            if query.lstrip().upper().startswith("SELECT"):
                return res.all()
            await db.commit()
    finally:
        await engine.dispose()


async def _redis(cmd, *args):
    r = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        return await getattr(r, cmd)(*args)
    finally:
        await r.aclose()


@pytest.fixture
def clean():
    _run(_sql("TRUNCATE reps, workout_sessions, clients CASCADE"))
    _run(_redis("flushdb"))


SOURCES: list[FakeSource] = []


def make_client(frames, redis_url=TEST_REDIS_URL):
    app = create_app(Settings(database_url=TEST_DB_URL, redis_url=redis_url))

    def factory():
        SOURCES.append(FakeSource(frames))
        return SOURCES[-1]

    app.state.landmark_source_factory = factory
    return TestClient(app)


def send_and_wait_processed(ws, data, timeout=2.0):
    """For frames that get no reply: wait until the server has processed this one."""
    import time
    src = SOURCES[-1]
    before = src.calls
    ws.send_bytes(data)
    end = time.time() + timeout
    while src.calls == before and time.time() < end:
        time.sleep(0.005)
    assert src.calls == before + 1


def start(ws, client_id=None, exercise="squat"):
    ws.send_json({"type": "start", "client_id": client_id or str(uuid.uuid4()),
                  "nickname": "tester", "exercise": exercise})
    return ws.receive_json()


def play(ws, indices):
    """Lock-step: one frame, one reply. Returns the replies."""
    out = []
    for i in indices:
        ws.send_bytes(str(i).encode())
        out.append(ws.receive_json())
    return out


def squats(n):
    frames = []
    for _ in range(n):
        frames += squat_sequence(min_knee=85)
    return frames + [GOOD]


def test_full_flow_start_frames_stop_summary(clean):
    frames = squats(3)
    with make_client(frames) as c, c.websocket_connect("/ws/workout") as ws:
        sid = start(ws)["session_id"]
        replies = play(ws, range(len(frames)))
        ws.send_json({"type": "stop"})
        summary = ws.receive_json()
    assert replies[-1]["type"] == "result" and replies[-1]["rep_count"] == 3
    assert {lm["name"] for lm in replies[-1]["landmarks"]} == {"shoulder", "hip", "knee", "ankle"}
    assert summary == {"type": "summary", "total_reps": 3, "avg_score": 100.0, "session_id": sid}
    rows = _run(_sql("SELECT status, total_reps FROM workout_sessions"))
    assert [(r.status, r.total_reps) for r in rows] == [("completed", 3)]
    assert _run(_sql("SELECT count(*) AS n FROM reps"))[0].n == 3


def test_started_message_contains_session_id_and_redis_state(clean):
    cid = str(uuid.uuid4())
    with make_client([GOOD]) as c, c.websocket_connect("/ws/workout") as ws:
        msg = start(ws, client_id=cid)
        assert msg["type"] == "started"
        state = _run(_redis("hgetall", f"session:{msg['session_id']}"))
        assert state["client_id"] == cid and state["exercise"] == "squat"
        play(ws, [0])
        assert len(_run(_redis("lrange", f"session:{msg['session_id']}:angles", 0, -1))) == 1


def test_result_feedback_has_code_and_message(clean):
    frames = squat_sequence(min_knee=120) + [GOOD]
    with make_client(frames) as c, c.websocket_connect("/ws/workout") as ws:
        start(ws)
        last = play(ws, range(len(frames)))[-1]
    assert {"code": "DEPTH_SHALLOW", "message": "더 깊이 앉으세요"} in last["feedback"]


def test_not_full_body_frames_produce_warning(clean):
    with make_client([GOOD]) as c, c.websocket_connect("/ws/workout") as ws:
        start(ws)
        ws.send_bytes(b"none")
        msg = ws.receive_json()
    assert msg == {"type": "warning", "code": "NOT_FULL_BODY", "message": "전신이 보이게 서주세요"}


def _wait_for_rows(n, timeout=3.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        rows = _run(_sql("SELECT status, total_reps FROM workout_sessions"))
        if len(rows) >= n:
            return rows
        time.sleep(0.05)
    return _run(_sql("SELECT status, total_reps FROM workout_sessions"))


def test_disconnect_without_stop_stores_abandoned(clean):
    frames = squats(2)
    with make_client(frames) as c:
        with c.websocket_connect("/ws/workout") as ws:
            start(ws)
            play(ws, range(len(frames)))
        rows = _wait_for_rows(1)
    assert [(r.status, r.total_reps) for r in rows] == [("abandoned", 2)]


def test_disconnect_with_zero_reps_stores_nothing(clean):
    with make_client([GOOD]) as c:
        with c.websocket_connect("/ws/workout") as ws:
            sid = start(ws)["session_id"]
            play(ws, [0])
        import time
        time.sleep(0.3)
    assert _run(_sql("SELECT count(*) AS n FROM workout_sessions"))[0].n == 0
    assert _run(_redis("exists", f"session:{sid}")) == 0


def test_stop_with_zero_reps_returns_summary_zero_not_stored(clean):
    with make_client([GOOD]) as c, c.websocket_connect("/ws/workout") as ws:
        sid = start(ws)["session_id"]
        ws.send_json({"type": "stop"})
        summary = ws.receive_json()
    assert summary == {"type": "summary", "total_reps": 0, "avg_score": None, "session_id": sid}
    assert _run(_sql("SELECT count(*) AS n FROM workout_sessions"))[0].n == 0


def _expect_error_then_close(ws, code):
    msg = ws.receive_json()
    assert msg["type"] == "error" and msg["code"] == code
    with pytest.raises(WebSocketDisconnect):
        ws.receive_json()


def test_invalid_client_id_returns_error_and_closes(clean):
    with make_client([GOOD]) as c, c.websocket_connect("/ws/workout") as ws:
        ws.send_json({"type": "start", "client_id": "not-a-uuid", "exercise": "squat"})
        _expect_error_then_close(ws, "INVALID_CLIENT_ID")


def test_unsupported_exercise_returns_error(clean):
    with make_client([GOOD]) as c, c.websocket_connect("/ws/workout") as ws:
        ws.send_json({"type": "start", "client_id": str(uuid.uuid4()), "exercise": "pushup"})
        _expect_error_then_close(ws, "UNSUPPORTED_EXERCISE")


def test_non_json_text_message_returns_invalid_message(clean):
    with make_client([GOOD]) as c, c.websocket_connect("/ws/workout") as ws:
        ws.send_text("{not json")
        _expect_error_then_close(ws, "INVALID_MESSAGE")


def test_binary_before_start_is_invalid_message(clean):
    with make_client([GOOD]) as c, c.websocket_connect("/ws/workout") as ws:
        ws.send_bytes(b"0")
        _expect_error_then_close(ws, "INVALID_MESSAGE")


def test_garbage_binary_frame_is_skipped_then_errors_after_30(clean):
    with make_client([GOOD]) as c, c.websocket_connect("/ws/workout") as ws:
        start(ws)
        for _ in range(29):
            send_and_wait_processed(ws, b"garbage")
        assert play(ws, [0])[0]["type"] == "result"  # still alive; counter resets
        for _ in range(30):
            send_and_wait_processed(ws, b"garbage")
        _expect_error_then_close(ws, "FRAME_DECODE_FAILED")


def test_redis_down_at_start_returns_redis_unavailable(clean):
    with make_client([GOOD], redis_url="redis://localhost:6399/0") as c, \
            c.websocket_connect("/ws/workout") as ws:
        ws.send_json({"type": "start", "client_id": str(uuid.uuid4()), "exercise": "squat"})
        _expect_error_then_close(ws, "REDIS_UNAVAILABLE")


def test_two_concurrent_connections_have_independent_counts(clean):
    frames = squats(1)
    cid = str(uuid.uuid4())
    with make_client(frames) as c, c.websocket_connect("/ws/workout") as a, \
            c.websocket_connect("/ws/workout") as b:
        start(a, client_id=cid)
        start(b, client_id=cid)
        ra, rb = [], []
        for i in range(len(frames)):  # interleave; b only sees standing frames
            ra += play(a, [i])
            rb += play(b, [len(frames) - 1])
        assert ra[-1]["rep_count"] == 1 and rb[-1]["rep_count"] == 0


async def test_slow_processing_drops_stale_frames():
    slot = LatestFrameSlot()
    for i in range(5):
        slot.put(bytes([i]))
    assert await slot.take() == bytes([4])
    waiter = asyncio.create_task(slot.take())
    await asyncio.sleep(0.01)
    assert not waiter.done()
    slot.put(b"x")
    assert await asyncio.wait_for(waiter, 1) == b"x"


async def test_closed_slot_returns_none():
    slot = LatestFrameSlot()
    slot.put(b"pending")
    slot.close()
    assert await slot.take() is None
