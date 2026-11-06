# 스쿼트 코치 (Squat Coach) 설계 문서

작성일: 2026-11-06
경로: 아키텍처 (새 프로젝트)

## 1. 목적과 범위

### 목적
여러 직무(백엔드, 비전, 풀스택)에 두루 쓰는 **취업용 포트폴리오**. 면접에서 실제로 돌아가는 데모를 보여주고, 기술 선택의 이유를 설명할 수 있어야 한다.

### 제품 개요
웹캠으로 스쿼트 자세를 인식하고, 횟수를 세고, 실시간으로 자세 피드백을 주는 웹 서비스. 운동이 끝나면 기록이 쌓이고 통계를 조회할 수 있다.

### 제약 조건
- 언어: Python (백엔드), 바닐라 JS (프론트엔드). React는 일정 리스크 때문에 제외했다.
- 기술: OpenCV, MediaPipe Pose, FastAPI, PostgreSQL, Redis
- 개발 기간: 1주일
- 배포: 기본 범위에서 제외. README, 시연 영상, 아키텍처 다이어그램으로 대체

### 성공 기준
- 브라우저에서 웹캠을 켜면 스쿼트 횟수와 자세 피드백이 실시간으로 표시된다.
- 샘플 영상 기준으로 횟수 집계가 기대값과 일치함을 테스트로 보여줄 수 있다.
- 운동 종료 후 기록과 통계(총 횟수, 평균 점수, 주간 추이, 자주 하는 실수)를 조회할 수 있다.
- Redis와 PostgreSQL의 역할 분리를 설명할 수 있다.

### 범위 제외
- 회원가입, 로그인 (브라우저가 만든 UUID를 식별자로 사용)
- 스쿼트 외 운동 (푸시업은 일정 여유 시 확장)
- 세션 재접속 후 이어하기
- Redis pub/sub (적용할 마땅한 지점이 없어 보류)
- 클라이언트 UI 자동화 테스트, 부하 테스트 (선택)

## 2. 전체 구조

```
[브라우저] --WebSocket(프레임, JPEG)--> [FastAPI 서버]
    ^                                      |
    |<---- 횟수/자세 피드백(JSON) ---------+
    |                                      +- OpenCV: 디코딩, 리사이즈, 전처리
    |                                      +- MediaPipe: 관절 좌표 추정
    |                                      +- 분석기: 각도 계산, 동작 단계 판정
    |                                      +--> [Redis] 세션 상태, 최근 각도 윈도우
    |                                      +--> [PostgreSQL] 운동 종료 시 요약 저장
    +---- REST: 기록/통계 조회 <-------------+
```

### 구성 요소
- **웹 클라이언트:** 바닐라 JS 단일 페이지. 웹캠 캡처(초당 약 10프레임, 낮은 해상도), 결과 표시, 서버가 보낸 좌표를 canvas에 오버레이. 화면은 운동 화면과 기록/통계 화면 두 개.
- **스트리밍 API (FastAPI WebSocket):** 프레임 수신, 분석기 호출, 결과 반환.
- **분석기 (순수 Python 모듈):** 서버와 DB를 모른다. 관절 좌표를 입력받아 각도, 동작 단계, 횟수, 피드백을 반환한다. 단독으로 단위 테스트가 가능하다.
- **저장/조회 API (REST):** 세션 요약 저장과 기록/통계 조회.

### 저장소 역할 분리
- Redis: 진행 중인 세션의 휘발성 상태만. TTL로 자동 정리.
- PostgreSQL: 종료된 세션의 영구 기록만.

## 3. 분석기 (스쿼트 판정)

### 입력과 전처리
- MediaPipe Pose의 랜드마크 중 엉덩이, 무릎, 발목, 어깨만 사용한다. 좌우 중 visibility가 높은 쪽을 자동 선택한다.
- 좌표 떨림은 최근 5프레임 이동 평균으로 줄인다. 이 윈도우가 Redis에 저장된다.

### 핵심 지표
- 무릎 각도 (엉덩이-무릎-발목): 앉은 깊이 판단
- 상체 기울기 (어깨-엉덩이 선과 수직선의 각도): 허리가 과하게 굽는지 판단

### 동작 단계 상태 머신
```
STANDING --(무릎 각도 < 하강 기준)--> DESCENDING
DESCENDING --(최저점 도달)--> BOTTOM
BOTTOM --(각도 증가 시작)--> ASCENDING
ASCENDING --(무릎 각도 > 기립 기준)--> STANDING  (1회 카운트)
```
- 단계 전환에 히스테리시스(진입/이탈 기준값을 다르게)를 적용해 경계에서 중복 집계를 막는다.
- 반복이 끝날 때 해당 반복의 최저 무릎 각도와 최대 상체 기울기를 기록한다.

### 피드백 규칙 (규칙 기반)
- 깊이 부족 (`DEPTH_SHALLOW`): 최저 무릎 각도가 기준(초기값 약 100도)보다 크면 "더 깊이 앉으세요"
- 상체 과기울기 (`TRUNK_LEAN`): 상체 기울기가 기준(초기값 약 45도)을 넘으면 "상체를 세우세요"
- 기준값은 설정 파일로 분리한다. 초기값은 일반적인 값이며, 구현 중 실제 영상으로 조정한다.
- 반복 하나당 점수(0~100)를 매긴다.

### 품질 방어
- visibility가 낮거나 몸이 화면 밖이면 분석을 건너뛰고 `NOT_FULL_BODY` 경고를 보낸다.

## 4. 데이터 모델

### Redis 키

| 키 | 타입 | 내용 | TTL |
|---|---|---|---|
| `session:{id}` | Hash | client_id, exercise, phase, rep_count, started_at, current_min_angle | 1시간, 프레임마다 갱신 |
| `session:{id}:angles` | List | 최근 각도 5개 (LPUSH 후 LTRIM) | 동일 |
| `session:{id}:reps` | List | 완료된 반복 JSON (최저 무릎 각도, 상체 기울기, 점수, 피드백 코드, 시각) | 동일 |

반복이 끝날 때마다 `reps`에 쌓고, 종료 시 PostgreSQL로 한 번에 옮긴다. 프레임마다 DB를 쓰지 않는다.

### PostgreSQL 스키마

```sql
clients (
  id          uuid PRIMARY KEY,
  nickname    text,
  created_at  timestamptz NOT NULL DEFAULT now()
)

workout_sessions (
  id            uuid PRIMARY KEY,
  client_id     uuid NOT NULL REFERENCES clients(id),
  exercise_type text NOT NULL CHECK (exercise_type IN ('squat')),
  started_at    timestamptz NOT NULL,
  ended_at      timestamptz,
  total_reps    int NOT NULL DEFAULT 0,
  avg_score     numeric(5,1),
  status        text NOT NULL CHECK (status IN ('completed','abandoned'))
)

reps (
  id              bigserial PRIMARY KEY,
  session_id      uuid NOT NULL REFERENCES workout_sessions(id) ON DELETE CASCADE,
  rep_no          int NOT NULL,
  min_knee_angle  real NOT NULL,
  max_trunk_tilt  real NOT NULL,
  score           smallint NOT NULL CHECK (score BETWEEN 0 AND 100),
  feedback_codes  text[] NOT NULL DEFAULT '{}',
  performed_at    timestamptz NOT NULL,
  UNIQUE (session_id, rep_no)
)

-- 인덱스
CREATE INDEX ON workout_sessions (client_id, started_at DESC);
CREATE INDEX ON reps (session_id);
```

- 통계(주간 추이, 실수 순위, 세션별 점수 변화)는 SQL 집계와 윈도 함수로 계산한다.
- 마이그레이션은 SQLAlchemy 2.0 + Alembic. 일정이 빠듯하면 순수 SQL 파일로 단순화한다.

### 저장 흐름
1. 운동 시작: Redis 세션 생성, `clients` upsert.
2. 운동 중: Redis만 갱신.
3. 운동 종료(종료 버튼 또는 연결 끊김): Redis의 `reps`를 읽어 한 트랜잭션으로 `workout_sessions`와 `reps`에 저장하고, 성공하면 Redis 키를 삭제한다.
4. 비정상 종료는 `abandoned`로 저장한다. 반복이 0회이면 저장하지 않는다.

## 5. API 규격

### WebSocket `/ws/workout`

| 방향 | type | 내용 |
|---|---|---|
| C->S | `start` | client_id, nickname, exercise |
| C->S | 프레임 | 바이너리 JPEG (약 320x240, 초당 약 10장) |
| C->S | `stop` | 없음 |
| S->C | `started` | session_id |
| S->C | `result` | phase, rep_count, knee_angle, feedback(코드+문구), landmarks |
| S->C | `warning` | NOT_FULL_BODY 등 |
| S->C | `summary` | total_reps, avg_score, session_id |
| S->C | `error` | code, message |

- 프레임은 base64가 아닌 바이너리로 전송한다.
- 서버는 좌표만 돌려주고 클라이언트가 canvas에 스켈레톤을 그린다.
- 백프레셔: 이전 프레임 처리 중 새 프레임이 오면 오래된 프레임을 버리고 최신 프레임만 처리한다.

### REST

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/sessions?client_id=` | 세션 목록 (최신순, 페이지네이션) |
| GET | `/api/sessions/{id}` | 세션 상세와 반복별 기록 |
| GET | `/api/stats/summary?client_id=` | 총 횟수, 평균 점수, 주간 추이 |
| GET | `/api/stats/mistakes?client_id=` | 자주 하는 실수 순위 |
| GET | `/health` | DB, Redis 연결 확인 |

- 인증이 없으므로 `client_id`는 보안 수단이 아니다. README에 한계로 명시한다.
- 입력 검증과 응답 스키마는 Pydantic, OpenAPI 문서는 FastAPI 자동 생성을 사용한다.

## 6. 에러 처리

- 깨진 프레임: 해당 프레임만 건너뛰고, 연속 실패가 일정 횟수를 넘으면 `error` 전송.
- Redis 장애: 세션 시작 거부. 진행 중 끊기면 세션 종료.
- PostgreSQL 저장 실패: Redis `reps`를 유지한 채 재시도, 계속 실패하면 세션 ID와 함께 로그 기록.
- 동시 접속: 세션별 Redis 키가 분리되어 서로 간섭하지 않는다.

## 7. 테스트 전략

- 분석기 단위 테스트 (최우선): 합성 각도 시퀀스로 상태 머신 검증, 샘플 영상으로 기대 횟수 비교.
- 저장 계층 통합 테스트: Docker Compose로 PostgreSQL과 Redis를 띄워 종료 시 Redis에서 DB로 이전되는 흐름 검증.
- WebSocket 테스트: FastAPI 테스트 클라이언트로 start, 프레임, stop, summary 흐름 확인.
- 클라이언트 UI는 수동 확인.

## 8. 7일 일정

| 일차 | 작업 | 완료 기준 |
|---|---|---|
| 1 | 프로젝트 골격, Docker Compose, DB 스키마, `/health` | 서버 기동, DB와 Redis 연결 확인 |
| 2 | MediaPipe + OpenCV 분석기, 각도 계산 | 샘플 영상에서 각도 산출 |
| 3 | 상태 머신, 횟수, 피드백 규칙, 분석기 테스트 | 샘플 영상 횟수 일치 |
| 4 | WebSocket 스트리밍 + Redis 세션 상태 | 테스트 클라이언트로 전 과정 동작 |
| 5 | 바닐라 JS 운동 화면 (웹캠, 오버레이, 피드백) | 브라우저에서 실시간 분석 |
| 6 | 저장 흐름, REST API, 기록/통계 화면 | 운동 후 기록 조회 가능 |
| 7 | 버그 수정, README, 시연 영상, 아키텍처 다이어그램 | 면접 시연 가능 상태 |

### 리스크와 대응
- 2~3일차(각도 기준 튜닝)가 가장 위험하다. 길어지면 푸시업 확장을 제외하고, 통계 화면은 목록과 합계 수준으로 축소한다.
- 배포는 기본 범위 밖이다. 원할 경우 CPU 서버 비용과 WebSocket 지연을 별도로 검토한다.

## 9. 확정된 결정 요약

- 접근안 A: 실시간 서버 분석 (브라우저 -> WebSocket -> FastAPI)
- 클라이언트: 바닐라 JS (React 제외)
- 운동: 스쿼트 단독
- 식별: 로그인 없이 브라우저 UUID
- Redis: 진행 중 세션 상태 / PostgreSQL: 종료된 세션 기록
