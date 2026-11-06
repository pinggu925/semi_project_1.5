CREATE TABLE clients (
  id          uuid PRIMARY KEY,
  nickname    text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE workout_sessions (
  id            uuid PRIMARY KEY,
  client_id     uuid NOT NULL REFERENCES clients(id),
  exercise_type text NOT NULL CHECK (exercise_type IN ('squat')),
  started_at    timestamptz NOT NULL,
  ended_at      timestamptz,
  total_reps    int NOT NULL DEFAULT 0,
  avg_score     numeric(5,1),
  status        text NOT NULL CHECK (status IN ('completed','abandoned'))
);

CREATE TABLE reps (
  id              bigserial PRIMARY KEY,
  session_id      uuid NOT NULL REFERENCES workout_sessions(id) ON DELETE CASCADE,
  rep_no          int NOT NULL,
  min_knee_angle  real NOT NULL,
  max_trunk_tilt  real NOT NULL,
  score           smallint NOT NULL CHECK (score BETWEEN 0 AND 100),
  feedback_codes  text[] NOT NULL DEFAULT '{}',
  performed_at    timestamptz NOT NULL,
  UNIQUE (session_id, rep_no)
);

CREATE INDEX ON workout_sessions (client_id, started_at DESC);
CREATE INDEX ON reps (session_id);
