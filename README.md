# Squat Coach

## Manual checks

Run with `uvicorn app.main:create_app --factory`, open http://localhost:8000 (the camera only works on `localhost` or HTTPS).

### Workout screen
- [ ] The camera permission prompt appears when you press "운동 시작".
- [ ] The count goes up exactly once per squat.
- [ ] A shallow squat shows "더 깊이 앉으세요".
- [ ] Stepping out of frame shows "전신이 보이게 서주세요".
- [ ] "종료" shows the summary (reps, average score) and the camera light turns off.
- [ ] Reloading the page keeps the same `client_id` (localStorage `squatcoach.client_id`).
- [ ] Stopping the server mid-workout shows a visible error and the camera is released.
