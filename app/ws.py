"""Real-time workout stream: binary JPEG frames in, rep count and feedback out."""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.analyzer.squat import FEEDBACK_MESSAGES, SquatAnalyzer
from app.analyzer.types import JOINTS, FrameResult, Landmarks
from app.finalize import finalize_session
from app.pose import FrameDecodeError, LandmarkSource
from app.redis_store import SessionStore
from app.repository import upsert_client

log = logging.getLogger(__name__)
router = APIRouter()

SUPPORTED_EXERCISES = {"squat"}
_background_tasks: set[asyncio.Task] = set()


def _background(coro) -> asyncio.Task:
    """Run work that must survive the handler being cancelled (e.g. abrupt disconnect)."""
    task = asyncio.get_running_loop().create_task(coro)
    _background_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.error("background task failed: %r", t.exception())

    task.add_done_callback(_done)
    return task
MAX_CONSECUTIVE_DECODE_FAILURES = 30


class ProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class LatestFrameSlot:
    """Holds only the newest unprocessed frame; older ones are dropped."""

    def __init__(self) -> None:
        self._frame: bytes | None = None
        self._closed = False
        self._event = asyncio.Event()

    def put(self, data: bytes) -> None:
        self._frame = data
        self._event.set()

    def close(self) -> None:
        self._closed = True
        self._event.set()

    async def take(self) -> bytes | None:
        while True:
            if self._closed:
                return None
            if self._frame is not None:
                data, self._frame = self._frame, None
                self._event.clear()
                return data
            await self._event.wait()


def _parse_text(raw: str) -> dict:
    try:
        msg = json.loads(raw)
    except (ValueError, RecursionError) as exc:  # JSONDecodeError is a ValueError
        raise ProtocolError("INVALID_MESSAGE", "메시지를 해석할 수 없습니다.") from exc
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ProtocolError("INVALID_MESSAGE", "메시지에 type이 없습니다.")
    return msg


def _result_message(res: FrameResult, landmarks: Landmarks) -> dict:
    return {
        "type": "result",
        "phase": res.phase.value,
        "rep_count": res.rep_count,
        "knee_angle": None if res.knee_angle is None else round(res.knee_angle, 1),
        "feedback": [{"code": c, "message": FEEDBACK_MESSAGES[c]} for c in res.feedback],
        "landmarks": [
            {"name": j, "x": landmarks[f"{res.side}_{j}"].x, "y": landmarks[f"{res.side}_{j}"].y}
            for j in JOINTS
        ],
    }


async def _send_error(ws: WebSocket, code: str, message: str) -> None:
    try:
        await ws.send_json({"type": "error", "code": code, "message": message})
        await ws.close()
    except Exception:  # noqa: BLE001 - client may already be gone
        pass


async def _read_start(ws: WebSocket) -> dict:
    msg = await ws.receive()
    if msg["type"] == "websocket.disconnect":
        raise WebSocketDisconnect()
    if msg.get("text") is None:
        raise ProtocolError("INVALID_MESSAGE", "먼저 start 메시지를 보내야 합니다.")
    start = _parse_text(msg["text"])
    if start["type"] != "start":
        raise ProtocolError("INVALID_MESSAGE", "먼저 start 메시지를 보내야 합니다.")
    try:
        start["client_id"] = str(uuid.UUID(str(start.get("client_id"))))
    except ValueError as exc:
        raise ProtocolError("INVALID_CLIENT_ID", "client_id가 올바른 UUID가 아닙니다.") from exc
    if start.get("exercise") not in SUPPORTED_EXERCISES:
        raise ProtocolError("UNSUPPORTED_EXERCISE", "지원하지 않는 운동입니다.")
    return start


@router.websocket("/ws/workout")
async def workout(ws: WebSocket) -> None:
    app = ws.app
    await ws.accept()
    try:
        start = await _read_start(ws)
    except WebSocketDisconnect:
        return
    except ProtocolError as err:
        await _send_error(ws, err.code, err.message)
        return

    store = SessionStore(app.state.redis, app.state.settings.session_ttl_seconds)
    session_id = str(uuid.uuid4())
    try:
        await store.create(session_id, start["client_id"], start["exercise"], datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001
        log.exception("redis unavailable at session start")
        await _send_error(ws, "REDIS_UNAVAILABLE", "세션 저장소에 연결할 수 없습니다.")
        return
    try:
        async with app.state.sessionmaker() as db, db.begin():
            await upsert_client(db, start["client_id"], start.get("nickname"))
    except Exception:  # noqa: BLE001 - finalize upserts again; not fatal for a live session
        log.warning("client upsert failed at start for %s", start["client_id"])

    await ws.send_json({"type": "started", "session_id": session_id})

    analyzer = SquatAnalyzer(app.state.thresholds)
    source: LandmarkSource = app.state.landmark_source_factory()
    slot = LatestFrameSlot()
    loop = asyncio.get_running_loop()

    async def receiver() -> str:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return "disconnect"
            if msg.get("bytes") is not None:
                slot.put(msg["bytes"])
                continue
            if _parse_text(msg.get("text") or "")["type"] == "stop":
                return "stop"
            raise ProtocolError("INVALID_MESSAGE", "알 수 없는 메시지입니다.")

    async def processor() -> None:
        failures = 0
        while (data := await slot.take()) is not None:
            try:
                landmarks = await loop.run_in_executor(None, source.landmarks_from_jpeg, data)
            except FrameDecodeError:
                failures += 1
                if failures >= MAX_CONSECUTIVE_DECODE_FAILURES:
                    raise ProtocolError("FRAME_DECODE_FAILED", "프레임을 해석할 수 없습니다.")
                continue
            failures = 0
            res = analyzer.update(landmarks)
            try:
                await store.update_state(session_id, res.phase.value, res.rep_count, analyzer.current_min_angle)
                if res.knee_angle is not None:
                    await store.push_angle(session_id, res.knee_angle)
                if res.completed_rep is not None:
                    await store.append_rep(session_id, res.completed_rep, datetime.now(timezone.utc))
            except Exception as exc:  # noqa: BLE001
                raise ProtocolError("REDIS_UNAVAILABLE", "세션 저장소 연결이 끊겼습니다.") from exc
            if res.warning:
                await ws.send_json({"type": "warning", "code": res.warning,
                                    "message": FEEDBACK_MESSAGES[res.warning]})
            else:
                await ws.send_json(_result_message(res, landmarks))

    recv_task = asyncio.create_task(receiver())
    proc_task = asyncio.create_task(processor())
    finalized = False
    try:
        try:
            outcome, error = await _drive(recv_task, proc_task, slot)
        finally:
            for t in (recv_task, proc_task):
                if not t.done():
                    t.cancel()
            if hasattr(source, "close"):
                source.close()

        status = "completed" if outcome == "stop" and error is None else "abandoned"
        fin = _background(finalize_session(store, app.state.sessionmaker, session_id, status))
        finalized = True
        try:
            total, avg = await asyncio.shield(fin)
        except Exception:  # noqa: BLE001 - logged inside finalize; reps stay in Redis
            total, avg = None, None
    except BaseException:
        # Handler cancelled (abrupt disconnect, shutdown) or crashed: still save what we have.
        if not finalized:
            _background(finalize_session(store, app.state.sessionmaker, session_id, "abandoned"))
        raise

    if isinstance(error, ProtocolError):
        await _send_error(ws, error.code, error.message)
    elif error is not None:
        log.error("workout session %s failed: %r", session_id, error)
        await _send_error(ws, "INTERNAL_ERROR", "서버 오류가 발생했습니다.")
    elif outcome == "stop":
        if total is None:
            await _send_error(ws, "SAVE_FAILED", "기록 저장에 실패했습니다.")
        else:
            await ws.send_json({"type": "summary", "total_reps": total, "avg_score": avg,
                                "session_id": session_id})
            await ws.close()


async def _drive(
    recv_task: asyncio.Task, proc_task: asyncio.Task, slot: LatestFrameSlot
) -> tuple[str, BaseException | None]:
    """Wait until the client stops/disconnects or processing fails. Returns (outcome, error)."""
    outcome: str = "disconnect"
    error: BaseException | None = None
    await asyncio.wait({recv_task, proc_task}, return_when=asyncio.FIRST_COMPLETED)
    if proc_task.done():
        error = None if proc_task.cancelled() else proc_task.exception()
        recv_task.cancel()
        return outcome, error
    slot.close()  # let an in-flight frame finish, drop pending ones
    try:
        outcome = recv_task.result()
    except ProtocolError as err:
        error = err
    except WebSocketDisconnect:
        outcome = "disconnect"
    except Exception as exc:  # noqa: BLE001 - any receiver failure still ends in finalize
        error = exc
    try:
        await proc_task
    except Exception as exc:  # noqa: BLE001
        error = error or exc
    return outcome, error
