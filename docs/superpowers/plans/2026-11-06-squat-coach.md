# Squat Coach Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a webcam squat coach: the browser streams frames over WebSocket, the server counts reps and gives posture feedback in real time, finished sessions are stored and shown as history and statistics.

**Architecture:** A FastAPI app receives binary JPEG frames on `/ws/workout`; OpenCV decodes them and MediaPipe Pose yields landmarks; a pure-Python `SquatAnalyzer` (no server/DB knowledge) turns landmarks into phase, rep count and feedback. Redis holds the live session (state, last 5 angles, completed reps, TTL 1h); on stop or disconnect the reps move to PostgreSQL in one transaction. A vanilla-JS single page (served by FastAPI) shows the workout screen and the history/stats screen.

**Tech Stack:** Python 3.11, FastAPI, uvicorn, SQLAlchemy 2.0 (async, asyncpg), redis-py (asyncio), OpenCV (`opencv-python-headless`), MediaPipe, PostgreSQL 16, Redis 7, Docker Compose (services only), pytest, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-11-06-squat-coach-design.md`

## Global Constraints

- Backend Python, frontend vanilla JS; no React, no Node build step.
- Exercise: `squat` only (`exercise_type` CHECK allows only `'squat'`); no login, `client_id` is a browser-generated UUID kept in localStorage and is not a security mechanism (README must say so).
- Frames: binary JPEG, about 320x240, about 10 frames per second; never base64.
- Server returns landmark coordinates only; the client draws the skeleton on a canvas.
- Angle moving average window: 5. Redis `session:{id}:angles` keeps exactly the last 5.
- Initial thresholds: depth `DEPTH_SHALLOW` when min knee angle > 100 degrees; `TRUNK_LEAN` when trunk tilt > 45 degrees. Both live in a config file and are tuned on real video.
- Feedback codes and texts: `DEPTH_SHALLOW` "더 깊이 앉으세요", `TRUNK_LEAN` "상체를 세우세요", warning `NOT_FULL_BODY` "전신이 보이게 서주세요".
- Redis keys: `session:{id}` (Hash), `session:{id}:angles` (List), `session:{id}:reps` (List); all TTL 1 hour, refreshed on every write.
- PostgreSQL schema exactly as spec section 4 (`clients`, `workout_sessions`, `reps`, the two indexes). `status` is `completed` or `abandoned`; a session with 0 reps is never stored.
- Stale-frame policy: if a frame is still being processed when newer ones arrive, only the newest is processed.
- Out of scope: auth, other exercises, reconnect/resume, Redis pub/sub, deployment, client UI automation tests.
- Dependency choice made in this plan (spec allowed either): schema is a plain SQL file (`sql/schema.sql`) applied by the Postgres container on first start, with SQLAlchemy Core `text()` queries; no Alembic.

## Review Focus

1. Person steps out of frame or landmarks vanish mid-rep: no crash, no phantom rep, `NOT_FULL_BODY` warning, state resumes when the body returns (Task 3).
2. Knee angle jitters around the stand/descend boundary or the user does a half squat that never crosses the descend threshold: no double count, no count (Task 3).
3. Client with no sessions requests history/stats: empty lists and zeros, never a 500 (Task 8).
4. Malformed input: invalid `client_id`, unsupported `exercise`, non-JPEG bytes, text message that is not valid JSON: an `error` message and a clean close, server keeps serving others (Task 7).
5. PostgreSQL unavailable at finalize: reps stay in Redis and the failure is logged with the session id; two simultaneous sessions never mix reps (Tasks 6 and 7).

---

## File Structure

```
docker-compose.yml          postgres + redis for local dev
sql/schema.sql              spec section 4 schema
config/thresholds.toml      tunable analyzer values
requirements.txt / requirements-dev.txt / pytest.ini / .env.example / .gitignore
app/config.py               Settings
app/db.py                   engine + sessionmaker factory
app/main.py                 create_app(), lifespan, /health, static mount
app/analyzer/types.py       Landmark, Phase, RepResult, FrameResult
app/analyzer/geometry.py    joint_angle, trunk_tilt
app/analyzer/thresholds.py  Thresholds, load_thresholds
app/analyzer/squat.py       SquatAnalyzer, score_rep, FEEDBACK_MESSAGES
app/pose.py                 FrameDecodeError, MediaPipeLandmarkSource, iter_video_landmarks
app/redis_store.py          SessionStore
app/repository.py           upsert_client, save_session, queries for REST
app/finalize.py             finalize_session (Redis -> PostgreSQL)
app/ws.py                   /ws/workout router
app/api.py                  REST router + Pydantic schemas
static/index.html, static/app.js, static/workout.js, static/history.js, static/style.css
tests/...                   mirrors app/
tests/data/                 squat_manifest.json (+ user-recorded mp4, gitignored)
README.md
```

---

### Task 1: Project skeleton, Compose, schema, `/health`

**Files:**
- Create: `docker-compose.yml`, `sql/schema.sql`, `requirements.txt`, `requirements-dev.txt`, `pytest.ini`, `.env.example`, `.gitignore`, `app/__init__.py`, `app/config.py`, `app/db.py`, `app/main.py`, `tests/conftest.py`, `tests/test_schema.py`, `tests/test_health.py`
- Test: `tests/test_schema.py`, `tests/test_health.py`

**Interfaces:**
- Produces:
  - `Settings` (pydantic-settings): `database_url: str` (`postgresql+asyncpg://...`), `redis_url: str`, `thresholds_path: str = "config/thresholds.toml"`, `session_ttl_seconds: int = 3600`; `get_settings() -> Settings`.
  - `create_engine_and_sessionmaker(settings: Settings) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]` in `app/db.py`.
  - `create_app(settings: Settings | None = None) -> FastAPI` in `app/main.py`; after startup `app.state.settings`, `app.state.sessionmaker`, `app.state.redis` (`redis.asyncio.Redis`) exist. `GET /health` returns 200 `{"postgres":"ok","redis":"ok"}` or 503 with `"down"` for the failing part.
  - pytest marker `integration` (needs `docker compose up -d`); fixtures `settings`, `sessionmaker`, `redis_client`, `clean_db` (truncates the three tables and flushes the Redis test DB 15) in `tests/conftest.py`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_schema.py  (integration)
async def test_schema_rejects_unsupported_exercise_type(clean_db, sessionmaker):
    # insert a client, then a workout_sessions row with exercise_type='pushup'
    # assert pytest.raises(IntegrityError)
async def test_reps_unique_per_session_and_rep_no(clean_db, sessionmaker):
    # insert same (session_id, rep_no) twice -> IntegrityError
async def test_deleting_session_cascades_to_reps(clean_db, sessionmaker):
    # after DELETE FROM workout_sessions, SELECT count(*) FROM reps == 0

# tests/test_health.py  (integration)
async def test_health_ok(client):  # httpx.AsyncClient on create_app()
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"postgres": "ok", "redis": "ok"}
async def test_health_reports_redis_down():
    # app created with redis_url="redis://localhost:6399/0"
    # assert status 503 and body["redis"] == "down" and body["postgres"] == "ok"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_schema.py tests/test_health.py -v`
Expected: FAIL / import errors (nothing exists yet).

- [ ] **Step 3: Implement the skeleton**
  - `docker-compose.yml`: `postgres:16` (db `squatcoach`, mounts `./sql/schema.sql` into `/docker-entrypoint-initdb.d/`), `redis:7`, ports 5432 / 6379.
  - `sql/schema.sql`: copy the three tables and two indexes verbatim from spec section 4.
  - `requirements.txt`: `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]>=2.0`, `asyncpg`, `redis>=5`, `pydantic-settings`, `opencv-python-headless`, `mediapipe==0.10.14`, `numpy==1.26.4`. `requirements-dev.txt`: `-r requirements.txt`, `pytest`, `pytest-asyncio`, `httpx`.
  - `pytest.ini`: register `integration` marker, `asyncio_mode = auto`.
  - `create_app` uses a lifespan that builds engine/sessionmaker/redis and closes them; `/health` runs `SELECT 1` and `PING`, each guarded by try/except.

- [ ] **Step 4: Run to verify pass**

Run: `docker compose up -d && pytest tests/test_schema.py tests/test_health.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add . && git commit -m "feat: project skeleton, compose, schema, health check"
```

---

### Task 2: Geometry

**Files:**
- Create: `app/analyzer/__init__.py`, `app/analyzer/geometry.py`
- Test: `tests/analyzer/test_geometry.py`

**Interfaces:**
- Produces: `Point = tuple[float, float]` (image coords, y grows downward); `joint_angle(a: Point, b: Point, c: Point) -> float` (degrees at `b`, 0..180); `trunk_tilt(shoulder: Point, hip: Point) -> float` (degrees between the hip->shoulder line and vertical, 0..180).

- [ ] **Step 1: Write failing tests**

```python
def test_right_angle():      assert joint_angle((0,1),(0,0),(1,0)) == pytest.approx(90)
def test_straight_leg():    assert joint_angle((0,0),(0,1),(0,2)) == pytest.approx(180)
def test_upright_trunk():   assert trunk_tilt((0.5,0.2),(0.5,0.5)) == pytest.approx(0)
def test_horizontal_trunk():assert trunk_tilt((0.8,0.5),(0.5,0.5)) == pytest.approx(90)
def test_degenerate_points_do_not_raise(): # a == b -> returns 0.0 / no ZeroDivisionError
```

- [ ] **Step 2: Run** `pytest tests/analyzer/test_geometry.py -v` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** both functions with `math.atan2`/dot product; clamp the cosine to [-1, 1]; return `0.0` for zero-length vectors.
- [ ] **Step 4: Run** same command — Expected: PASS.
- [ ] **Step 5: Commit** `git add app/analyzer tests/analyzer && git commit -m "feat: angle geometry helpers"`

---

### Task 3: Squat analyzer (state machine, scoring, feedback, thresholds)

**Files:**
- Create: `app/analyzer/types.py`, `app/analyzer/thresholds.py`, `app/analyzer/squat.py`, `config/thresholds.toml`, `tests/analyzer/helpers.py`
- Test: `tests/analyzer/test_squat.py`, `tests/analyzer/test_scoring.py`

**Interfaces:**
- Consumes: `joint_angle`, `trunk_tilt`.
- Produces:
  - `Landmark(x: float, y: float, visibility: float)` frozen dataclass; `Landmarks = dict[str, Landmark]` with keys `{left,right}_{shoulder,hip,knee,ankle}`.
  - `Phase(str, Enum)`: `STANDING, DESCENDING, BOTTOM, ASCENDING`.
  - `RepResult(rep_no: int, min_knee_angle: float, max_trunk_tilt: float, score: int, feedback_codes: list[str])`.
  - `FrameResult(phase: Phase, rep_count: int, knee_angle: float | None, trunk_tilt: float | None, feedback: list[str], warning: str | None, side: str | None, completed_rep: RepResult | None)`; `feedback` holds the codes of the last completed rep until the next rep completes.
  - `Thresholds` dataclass (frozen) and `load_thresholds(path: str) -> Thresholds`. Initial values in `config/thresholds.toml` (starting points, tuned in Task 4): `descend_below=140`, `stand_above=160`, `reversal_delta=5`, `depth_target=100`, `trunk_max=45`, `min_visibility=0.5`, `smoothing_window=5`.
  - `SquatAnalyzer(thresholds: Thresholds)` with `update(landmarks: Landmarks | None) -> FrameResult`, property `angle_window -> list[float]` (current smoothed-window raw angles), `FEEDBACK_MESSAGES: dict[str, str]`, `score_rep(min_knee: float, max_tilt: float, t: Thresholds) -> tuple[int, list[str]]`.
  - Test helper `make_landmarks(knee_angle: float, trunk_tilt: float = 10.0, visibility: float = 0.9) -> Landmarks` and `squat_sequence(min_knee: float, trunk: float = 10.0) -> list[Landmarks]` (170 -> `min_knee` -> 170 in 5-degree steps, each step repeated `smoothing_window` times so smoothing settles).

Rules the implementation must follow (spec section 3):
- Side = the side whose shoulder/hip/knee/ankle mean visibility is higher. If that mean `< min_visibility` or `landmarks is None`: return `warning="NOT_FULL_BODY"`, leave phase/count/window untouched.
- Angles are smoothed with a moving average over the last `smoothing_window` raw values.
- STANDING -> DESCENDING when smoothed knee `< descend_below`. DESCENDING -> BOTTOM when knee rises `>= reversal_delta` above the rep's minimum. BOTTOM -> ASCENDING when knee exceeds the previous frame's; BOTTOM -> DESCENDING if knee falls below the rep minimum again. ASCENDING -> STANDING when knee `> stand_above`, which increments `rep_count` and emits `completed_rep`. `descend_below < stand_above` is the hysteresis.
- Per rep track minimum knee angle and maximum trunk tilt from DESCENDING start to the count.
- Score: `100 - min(50, 2*max(0, min_knee - depth_target)) - min(50, 2*max(0, max_tilt - trunk_max))`, clamped 0..100; codes `DEPTH_SHALLOW` / `TRUNK_LEAN` per spec thresholds.

- [ ] **Step 1: Write failing tests**

```python
def test_five_good_squats_count_five():
    a = SquatAnalyzer(load_thresholds("config/thresholds.toml"))
    for _ in range(5):
        for lm in squat_sequence(min_knee=85): a.update(lm)
    assert a.update(make_landmarks(170)).rep_count == 5

def test_good_rep_scores_100_without_feedback():     # min_knee=85, trunk=10 -> score 100, codes == []
def test_shallow_rep_flags_depth():                  # min_knee=120 -> "DEPTH_SHALLOW" in codes, score == 60
def test_leaning_rep_flags_trunk():                  # trunk=60 -> "TRUNK_LEAN", score == 70
def test_half_squat_above_descend_threshold_counts_nothing():  # min_knee=145 -> rep_count == 0, phase STANDING
def test_jitter_around_stand_boundary_counts_once():  # knee oscillates 158..162 for 30 frames during ascent -> exactly one count
def test_landmarks_none_mid_rep_freezes_state():      # feed half a rep, then 20x None -> every result has warning NOT_FULL_BODY, rep_count unchanged, no exception; finishing the rep afterwards counts 1
def test_low_visibility_is_not_full_body():           # visibility=0.2 -> warning "NOT_FULL_BODY"
def test_picks_more_visible_side():                   # left vis 0.9, right 0.2 -> FrameResult.side == "left"
def test_angle_window_never_exceeds_five():           # after 50 updates len(a.angle_window) == 5
def test_load_thresholds_reads_toml(tmp_path):        # values round-trip
```

- [ ] **Step 2: Run** `pytest tests/analyzer -v` — Expected: FAIL.
- [ ] **Step 3: Implement** types, thresholds loader (`tomllib`), `squat.py` per the rules above, `config/thresholds.toml`, test helpers (build coordinates so `joint_angle` of hip-knee-ankle equals the requested angle).
- [ ] **Step 4: Run** `pytest tests/analyzer -v` — Expected: all PASS.
- [ ] **Step 5: Commit** `git add app/analyzer config tests/analyzer && git commit -m "feat: squat analyzer state machine, scoring, feedback"`

---

### Task 4: Pose extraction (OpenCV + MediaPipe) and real-video validation

**Files:**
- Create: `app/pose.py`, `tests/test_pose.py`, `tests/data/squat_manifest.json`
- Modify: `config/thresholds.toml` (tuning), `.gitignore` (ignore `tests/data/*.mp4`)

**Interfaces:**
- Consumes: `Landmarks`, `Landmark`, `SquatAnalyzer`.
- Produces: `FrameDecodeError(Exception)`; `class MediaPipeLandmarkSource` with `landmarks_from_jpeg(self, data: bytes) -> Landmarks | None` (raises `FrameDecodeError` when bytes are not a decodable image, returns `None` when no person is found); `iter_video_landmarks(path: Path, source: MediaPipeLandmarkSource | None = None) -> Iterator[Landmarks | None]`. A `Protocol` named `LandmarkSource` with just `landmarks_from_jpeg` is exported so tests can inject fakes.
- `tests/data/squat_manifest.json`: `{"squat_5reps.mp4": {"expected_reps": 5}}` (more entries allowed).

- [ ] **Step 1: Write failing tests**

```python
def test_garbage_bytes_raise_decode_error():
    with pytest.raises(FrameDecodeError): MediaPipeLandmarkSource().landmarks_from_jpeg(b"not a jpeg")
def test_blank_image_returns_none():   # cv2.imencode('.jpg', np.zeros((240,320,3),np.uint8)) -> None
def test_landmark_keys_cover_both_sides():  # skipped unless a sample video exists; keys == the 8 expected names
@pytest.mark.skipif(not VIDEO.exists(), reason="record tests/data/squat_5reps.mp4")
def test_sample_video_rep_count_matches_manifest():
    a = SquatAnalyzer(load_thresholds(...)); last = None
    for lm in iter_video_landmarks(VIDEO): last = a.update(lm)
    assert last.rep_count == expected_reps
```

- [ ] **Step 2: Run** `pytest tests/test_pose.py -v` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `pose.py`: `cv2.imdecode` then `cv2.resize` to 320x240 when larger, run `mediapipe.solutions.pose.Pose(model_complexity=0)` created once per source (not per frame), map the 8 needed landmarks to `Landmark`; `iter_video_landmarks` uses `cv2.VideoCapture`, subsampling to about 10 fps.
- [ ] **Step 4: Run** same command — Expected: decode/blank tests PASS; the video test is skipped until the video exists.
- [ ] **Step 5: Record the sample video (needs the user)**: ask the user to film a side-view video of exactly 5 squats, save as `tests/data/squat_5reps.mp4`, then run `pytest tests/test_pose.py -v`. If the count is wrong, adjust only `config/thresholds.toml` values and re-run until it passes; note the final values in the commit message.
- [ ] **Step 6: Commit** `git add app/pose.py tests config .gitignore && git commit -m "feat: pose extraction and threshold tuning on real video"`

---

### Task 5: Redis session store

**Files:**
- Create: `app/redis_store.py`
- Test: `tests/test_redis_store.py` (integration)

**Interfaces:**
- Consumes: `RepResult`.
- Produces: `SessionStore(redis: Redis, ttl_seconds: int = 3600)` with async methods
  - `create(session_id: str, client_id: str, exercise: str, started_at: datetime) -> None`
  - `update_state(session_id: str, phase: str, rep_count: int, current_min_angle: float | None) -> None`
  - `push_angle(session_id: str, angle: float) -> None` (LPUSH then LTRIM to 5)
  - `append_rep(session_id: str, rep: RepResult, performed_at: datetime) -> None` (JSON into `reps`)
  - `get_state(session_id: str) -> dict[str, str] | None`
  - `get_reps(session_id: str) -> list[dict]` (oldest first; keys `rep_no, min_knee_angle, max_trunk_tilt, score, feedback_codes, performed_at`)
  - `delete(session_id: str) -> None` (all three keys)

- [ ] **Step 1: Write failing tests**

```python
async def test_create_sets_hash_and_ttl():        # get_state has client_id/exercise; ttl of session:{id} in (0, 3600]
async def test_angles_list_keeps_last_five():     # push 8 -> llen == 5, newest first
async def test_every_write_refreshes_ttl():       # set ttl to 5 manually, call update_state -> ttl > 3000
async def test_reps_round_trip_in_order():        # append rep 1,2 -> get_reps rep_no == [1,2]
async def test_sessions_are_isolated():           # two session ids never see each other's reps
async def test_delete_removes_all_three_keys():
async def test_get_state_missing_returns_none():
```

- [ ] **Step 2: Run** `pytest tests/test_redis_store.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** with a pipeline per call (write + `EXPIRE` on all touched keys).
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `git add app/redis_store.py tests && git commit -m "feat: redis session store"`

---

### Task 6: Persistence and finalize (Redis -> PostgreSQL)

**Files:**
- Create: `app/repository.py`, `app/finalize.py`
- Test: `tests/test_finalize.py` (integration)

**Interfaces:**
- Consumes: `SessionStore`, `async_sessionmaker`.
- Produces:
  - `upsert_client(db: AsyncSession, client_id: str, nickname: str | None) -> None`
  - `save_session(db: AsyncSession, session_id: str, client_id: str, exercise: str, started_at: datetime, ended_at: datetime, status: str, reps: list[dict]) -> tuple[int, float | None]` (returns `total_reps, avg_score`; single transaction for session and all reps)
  - `finalize_session(store: SessionStore, sessionmaker: async_sessionmaker[AsyncSession], session_id: str, status: str, retries: int = 3, retry_delay: float = 0.5) -> tuple[int, float | None]`: reads state and reps from Redis; `0` reps -> returns `(0, None)`, stores nothing, deletes Redis keys; success -> deletes Redis keys; persistent DB failure -> keeps Redis keys, logs `session_id`, re-raises.

- [ ] **Step 1: Write failing tests**

```python
async def test_finalize_moves_reps_in_one_transaction():   # 3 reps in Redis -> DB has 1 session, 3 reps, avg_score correct, Redis keys gone
async def test_finalize_zero_reps_stores_nothing():        # no rows in workout_sessions; returns (0, None); Redis keys gone
async def test_abandoned_status_is_stored():
async def test_db_failure_keeps_redis_reps_and_logs(caplog):  # sessionmaker pointed at bad URL, retry_delay=0 -> raises; get_reps still returns 3 reps; caplog contains the session id
async def test_failed_transaction_leaves_no_partial_rows():   # one rep dict with score=999 violates CHECK -> no workout_sessions row
async def test_two_sessions_same_client_do_not_mix_reps():
```

- [ ] **Step 2: Run** `pytest tests/test_finalize.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** with `text()` inserts and `INSERT ... ON CONFLICT (id) DO UPDATE` for the client upsert; retry loop only around the DB transaction.
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `git add app tests && git commit -m "feat: finalize sessions from redis into postgres"`

---

### Task 7: WebSocket workout endpoint

**Files:**
- Create: `app/ws.py`
- Modify: `app/main.py` (include router; `app.state.landmark_source` defaults to `MediaPipeLandmarkSource()`, `app.state.thresholds` loaded from settings)
- Test: `tests/test_ws.py` (integration, uses a fake `LandmarkSource`)

**Interfaces:**
- Consumes: `SquatAnalyzer`, `SessionStore`, `finalize_session`, `LandmarkSource`, `FrameDecodeError`, `upsert_client`.
- Produces: WebSocket `/ws/workout` with the spec section 5 protocol. Messages are JSON text except frames (binary):
  - C->S `{"type":"start","client_id":"<uuid>","nickname":"...","exercise":"squat"}`, binary frame, `{"type":"stop"}`
  - S->C `{"type":"started","session_id"}`, `{"type":"result","phase","rep_count","knee_angle","feedback":[{"code","message"}],"landmarks":[{"name","x","y"}]}` (the 4 joints of the chosen side), `{"type":"warning","code":"NOT_FULL_BODY","message"}`, `{"type":"summary","total_reps","avg_score","session_id"}`, `{"type":"error","code","message"}`
  - Error codes: `INVALID_MESSAGE`, `INVALID_CLIENT_ID`, `UNSUPPORTED_EXERCISE`, `REDIS_UNAVAILABLE`, `FRAME_DECODE_FAILED` (after 30 consecutive failed frames).
- Behavior: a receiver loop keeps only the newest unprocessed frame; the processor runs `landmarks_from_jpeg` in a thread executor, feeds the analyzer, mirrors `phase`, `rep_count`, min angle, and angle window into Redis, appends completed reps. On `stop` -> `finalize_session(status="completed")` then `summary`. On disconnect without `stop` -> `finalize_session(status="abandoned")` (swallow and log errors). Each connection owns its own `SquatAnalyzer`.

- [ ] **Step 1: Write failing tests**

```python
def test_full_flow_start_frames_stop_summary(): # fake source replays squat_sequence x3; client sends one binary frame per landmark set, reads results; after stop -> summary.total_reps == 3 and DB has 1 completed session with 3 reps
def test_started_message_contains_session_id_and_redis_state():
def test_not_full_body_frames_produce_warning():           # fake source returns None -> {"type":"warning","code":"NOT_FULL_BODY"}
def test_disconnect_without_stop_stores_abandoned():       # 2 reps then close -> status 'abandoned'
def test_disconnect_with_zero_reps_stores_nothing():
def test_stop_with_zero_reps_returns_summary_zero_not_stored():  # summary.total_reps == 0, avg_score None, row count 0
def test_invalid_client_id_returns_error_and_closes():
def test_unsupported_exercise_returns_error():
def test_non_json_text_message_returns_invalid_message():
def test_garbage_binary_frame_is_skipped_then_errors_after_30():   # first 29 produce no crash; 30th consecutive -> FRAME_DECODE_FAILED
def test_redis_down_at_start_returns_redis_unavailable():
def test_two_concurrent_connections_have_independent_counts():
async def test_slow_processing_drops_stale_frames():       # unit test of the "latest frame" slot class: put 5 frames, take() returns the 5th, then waits
```

- [ ] **Step 2: Run** `pytest tests/test_ws.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** a small `LatestFrameSlot` class (`put(data: bytes) -> None`, `async take() -> bytes`) inside `ws.py` for the stale-frame policy, plus the endpoint. Validate `client_id` with `uuid.UUID`.
- [ ] **Step 4: Run** `pytest tests/test_ws.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** `git add app tests && git commit -m "feat: websocket workout stream"`

---

### Task 8: REST API (history and statistics)

**Files:**
- Create: `app/api.py`
- Modify: `app/repository.py` (read queries), `app/main.py` (include router)
- Test: `tests/test_api.py` (integration)

**Interfaces:**
- Consumes: `async_sessionmaker`, tables from `sql/schema.sql`.
- Produces (Pydantic response models in `app/api.py`):
  - `GET /api/sessions?client_id=&limit=20&offset=0` -> `{"items":[{"id","started_at","ended_at","total_reps","avg_score","status","score_delta"}],"total":int}`; newest first; `score_delta` = `avg_score - lag(avg_score)` over the client's chronological sessions (SQL window function), `null` for the first.
  - `GET /api/sessions/{id}` -> session fields plus `"reps":[{"rep_no","min_knee_angle","max_trunk_tilt","score","feedback_codes","performed_at"}]`; 404 when missing.
  - `GET /api/stats/summary?client_id=` -> `{"total_sessions","total_reps","avg_score","weekly":[{"week","reps","avg_score"}]}` using `date_trunc('week', started_at)`.
  - `GET /api/stats/mistakes?client_id=` -> `[{"code","count"}]` descending, using `unnest(feedback_codes)`.
  - Invalid `client_id` (not a UUID) -> 422.

- [ ] **Step 1: Write failing tests**

```python
async def test_sessions_list_newest_first_with_pagination():
async def test_score_delta_uses_previous_session():        # sessions 80 then 90 -> newest delta == 10.0, oldest None
async def test_session_detail_includes_reps_in_order():
async def test_session_detail_404():
async def test_summary_totals_and_weekly_buckets():
async def test_mistakes_ranking_counts_codes():            # DEPTH_SHALLOW x3, TRUNK_LEAN x1
async def test_unknown_client_returns_empty_not_error():   # sessions items == [] total 0; summary zeros and weekly == []; mistakes == []
async def test_other_clients_data_is_not_returned():
async def test_invalid_client_id_is_422():
```

- [ ] **Step 2: Run** `pytest tests/test_api.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the queries with `text()`; use `COALESCE` so empty clients yield zeros.
- [ ] **Step 4: Run** same — Expected: PASS.
- [ ] **Step 5: Commit** `git add app tests && git commit -m "feat: rest api for sessions and stats"`

---

### Task 9: Workout screen (vanilla JS)

**Files:**
- Create: `static/index.html`, `static/app.js`, `static/workout.js`, `static/style.css`
- Modify: `app/main.py` (mount `static/` at `/`, after API and WS routes)

**Interfaces:**
- Consumes: `/ws/workout` protocol from Task 7.
- Produces: `static/app.js` exports `getClientId(): string` (localStorage `squatcoach.client_id`, created with `crypto.randomUUID()`, wrapped in try/catch with in-memory fallback) and hash routing `#/workout` (default) and `#/history`; `static/workout.js` exports `initWorkout(rootEl)`.

Behavior: Start button -> `getUserMedia` (video 320x240), open WebSocket, send `start`; every 100 ms draw the video to an offscreen 320x240 canvas, `toBlob('image/jpeg', 0.6)` and send binary, skipping the tick when `ws.bufferedAmount > 64 KB`; render `rep_count`, `phase`, feedback text; draw the 4 joints and connecting lines from `result.landmarks` (x, y are normalized 0..1) on an overlay canvas; show warning text for `NOT_FULL_BODY`; Stop button sends `stop`, shows the `summary` (reps, avg score) and releases the camera tracks; handle `error` messages and unexpected close with a visible message.

- [ ] **Step 1: Write the manual verification checklist** at the top of `README.md` under "Manual checks" (each line is a pass/fail): camera permission prompt appears; count increments once per squat; shallow squat shows "더 깊이 앉으세요"; stepping out of frame shows "전신이 보이게 서주세요"; Stop shows summary and turns the camera light off; page reload keeps the same `client_id`; stopping the server mid-workout shows a visible error and no stuck camera.
- [ ] **Step 2: Implement** the files per the behavior above.
- [ ] **Step 3: Verify** `docker compose up -d && uvicorn app.main:create_app --factory --reload`, open `http://localhost:8000`, run every checklist line. Expected: all pass (camera needs `localhost` or HTTPS).
- [ ] **Step 4: Commit** `git add static app README.md && git commit -m "feat: workout screen"`

---

### Task 10: History and statistics screen

**Files:**
- Create: `static/history.js`
- Modify: `static/index.html`, `static/style.css`, `README.md` (add manual checks)

**Interfaces:**
- Consumes: `getClientId()`, REST endpoints from Task 8.
- Produces: `initHistory(rootEl)`; shows summary tiles (total sessions, total reps, average score), a weekly reps list or simple bar row drawn with plain divs (no chart library), the session list with `score_delta`, a session detail view with per-rep rows, and the mistakes ranking with Korean labels for the codes.

- [ ] **Step 1: Add manual checks** to the README: new browser shows empty-state text, not an error; after a workout the session appears at the top with correct reps; detail view lists reps in order; mistakes ranking matches the feedback shown during the workout.
- [ ] **Step 2: Implement** `history.js` with `fetch`, treating non-2xx as a visible error message.
- [ ] **Step 3: Verify** run the app, complete two workouts (one deliberately shallow), run each check. Expected: all pass.
- [ ] **Step 4: Commit** `git add static README.md && git commit -m "feat: history and statistics screen"`

---

### Task 11: README, demo assets, final verification

**Files:**
- Create/Modify: `README.md`, `docs/architecture.png` (or Mermaid block in README), `.env.example` (final)

**Interfaces:** none.

- [ ] **Step 1: Write README sections**: what it does, architecture diagram (from spec section 2), why Redis holds live state and PostgreSQL holds finished records, run instructions (`docker compose up -d`, `pip install -r requirements.txt`, `uvicorn app.main:create_app --factory`), how to run tests (unit vs `-m integration`), threshold tuning guide, known limitations (no auth, `client_id` is not a security mechanism, single exercise, CPU-bound analysis, no reconnect), and the OpenAPI docs link `/docs`.
- [ ] **Step 2: Run the whole suite** `pytest -v` with services up. Expected: all PASS (video test passes if the sample exists, otherwise skipped).
- [ ] **Step 3: Record a demo video** (screen capture of a workout and the history page) and link it from the README.
- [ ] **Step 4: Commit** `git add . && git commit -m "docs: readme, architecture, demo"`

---

## Self-review

- **Spec coverage:** architecture (T1, T7), analyzer rules (T2-3), real-video validation (T4), Redis keys and TTL (T5), schema and save flow including abandoned and zero-rep (T1, T6), WebSocket protocol and stale-frame policy (T7), REST endpoints including window function and `unnest` (T8), two screens (T9-10), error handling (T6-7), README/demo/limitations (T11), 7-day schedule is covered by task order (T1 day 1, T2-4 days 2-3, T5-7 day 4, T9 day 5, T6/T8/T10 day 6, T11 day 7). Pub/sub and push-ups are intentionally absent.
- **Type consistency:** `Landmarks`, `RepResult`, `FrameResult`, `SessionStore` method names and `finalize_session` signature are used identically in Tasks 3-7.
- **Review Focus:** each of the five lines has a named test in Tasks 3, 6, 7 and 8.
