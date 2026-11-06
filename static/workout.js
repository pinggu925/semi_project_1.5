// Workout screen: webcam -> JPEG frames over WebSocket -> live count, feedback, skeleton overlay.
import { getClientId, getNickname, setNickname } from "/app.js";

const FRAME_W = 320, FRAME_H = 240, FRAME_MS = 100, MAX_BUFFERED = 64 * 1024;
const PHASE_LABEL = { STANDING: "서 있음", DESCENDING: "내려가는 중", BOTTOM: "최저점", ASCENDING: "올라오는 중" };
const BONES = [["shoulder", "hip"], ["hip", "knee"], ["knee", "ankle"]];

export function initWorkout(root) {
  root.innerHTML = `
    <section class="workout">
      <div class="stage">
        <video id="cam" playsinline muted></video>
        <canvas id="overlay"></canvas>
        <div class="banner hidden" id="warning"></div>
        <div class="placeholder" id="placeholder">카메라가 꺼져 있습니다.<br>옆모습 전신이 보이도록 서 주세요.</div>
      </div>
      <aside class="panel">
        <label class="field">닉네임
          <input id="nickname" maxlength="20" placeholder="선택 입력">
        </label>
        <div class="counter"><span id="reps">0</span><small>회</small></div>
        <div class="phase" id="phase">대기 중</div>
        <div class="angle">무릎 각도 <b id="angle">-</b></div>
        <ul class="feedback" id="feedback"></ul>
        <div class="buttons">
          <button id="start" class="primary">운동 시작</button>
          <button id="stop" disabled>종료</button>
        </div>
        <div class="status" id="status"></div>
        <div class="summary hidden" id="summary"></div>
      </aside>
    </section>`;

  const $ = (id) => root.querySelector("#" + id);
  const video = $("cam"), overlay = $("overlay"), octx = overlay.getContext("2d");
  const grab = document.createElement("canvas");
  grab.width = FRAME_W; grab.height = FRAME_H;
  const gctx = grab.getContext("2d");
  $("nickname").value = getNickname();

  let stream = null, ws = null, timer = null, encoding = false, stopping = false;
  let gotError = false, disposed = false;

  const setStatus = (text, isError = false) => {
    $("status").textContent = text;
    $("status").classList.toggle("error", isError);
  };

  function releaseCamera() {
    clearInterval(timer); timer = null;
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    video.srcObject = null;
    octx.clearRect(0, 0, overlay.width, overlay.height);
    if (disposed) return;
    $("placeholder").classList.remove("hidden");
    $("start").disabled = false; $("stop").disabled = true;
  }

  function draw(landmarks) {
    overlay.width = video.clientWidth; overlay.height = video.clientHeight;
    octx.clearRect(0, 0, overlay.width, overlay.height);
    if (!landmarks || !landmarks.length) return;
    const p = Object.fromEntries(landmarks.map((l) => [l.name, [l.x * overlay.width, l.y * overlay.height]]));
    octx.lineWidth = 4; octx.strokeStyle = "#38d39f"; octx.lineCap = "round";
    for (const [a, b] of BONES) {
      if (!p[a] || !p[b]) continue;
      octx.beginPath(); octx.moveTo(...p[a]); octx.lineTo(...p[b]); octx.stroke();
    }
    octx.fillStyle = "#ffffff";
    for (const [x, y] of Object.values(p)) { octx.beginPath(); octx.arc(x, y, 6, 0, Math.PI * 2); octx.fill(); }
  }

  function sendFrame() {
    if (!ws || ws.readyState !== WebSocket.OPEN || encoding || ws.bufferedAmount > MAX_BUFFERED) return;
    if (video.readyState < 2) return;
    encoding = true;
    gctx.drawImage(video, 0, 0, FRAME_W, FRAME_H);
    grab.toBlob((blob) => {
      encoding = false;
      if (blob && ws && ws.readyState === WebSocket.OPEN) ws.send(blob);
    }, "image/jpeg", 0.6);
  }

  function onMessage(ev) {
    const msg = JSON.parse(ev.data);
    if (msg.type === "started") {
      setStatus("분석 중…");
      timer = setInterval(sendFrame, FRAME_MS);
    } else if (msg.type === "result") {
      $("warning").classList.add("hidden");
      $("reps").textContent = msg.rep_count;
      $("phase").textContent = PHASE_LABEL[msg.phase] || msg.phase;
      $("angle").textContent = msg.knee_angle == null ? "-" : `${Math.round(msg.knee_angle)}°`;
      $("feedback").innerHTML = msg.feedback.length
        ? msg.feedback.map((f) => `<li>${f.message}</li>`).join("")
        : `<li class="good">좋은 자세입니다</li>`;
      draw(msg.landmarks);
    } else if (msg.type === "warning") {
      $("warning").textContent = msg.message;
      $("warning").classList.remove("hidden");
      draw(null);
    } else if (msg.type === "summary") {
      $("summary").innerHTML = msg.total_reps
        ? `<b>${msg.total_reps}회</b> 완료 · 평균 점수 <b>${msg.avg_score}</b><br><a href="#/history">기록 보기</a>`
        : "완료된 반복이 없어 기록하지 않았습니다.";
      $("summary").classList.remove("hidden");
      setStatus("");
    } else if (msg.type === "error") {
      gotError = true;  // the server closes right after; keep this message instead of the generic one
      setStatus(`오류: ${msg.message}`, true);
    }
  }

  async function start() {
    $("summary").classList.add("hidden");
    setStatus("카메라 준비 중…");
    $("start").disabled = true;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false });
      if (disposed) { releaseCamera(); return; }  // the user left this screen while the prompt was open
      video.srcObject = stream;
      await video.play();
      if (disposed) { releaseCamera(); return; }
    } catch (e) {
      if (disposed) { releaseCamera(); return; }
      releaseCamera();
      setStatus("카메라를 사용할 수 없습니다. 브라우저 권한을 확인해 주세요.", true);
      $("start").disabled = false;
      return;
    }
    $("placeholder").classList.add("hidden");
    $("reps").textContent = "0";
    $("feedback").innerHTML = "";

    const nickname = $("nickname").value.trim();
    setNickname(nickname);
    stopping = false;
    gotError = false;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/workout`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => ws.send(JSON.stringify({
      type: "start", client_id: getClientId(), nickname: nickname || null, exercise: "squat",
    }));
    ws.onmessage = onMessage;
    ws.onclose = () => {
      if (!stopping && !gotError && !disposed) setStatus("서버 연결이 끊겼습니다. 다시 시작해 주세요.", true);
      ws = null;
      releaseCamera();
    };
    ws.onerror = () => setStatus("서버에 연결할 수 없습니다.", true);
    $("stop").disabled = false;
  }

  function stop() {
    stopping = true;
    clearInterval(timer); timer = null;
    $("stop").disabled = true;
    if (ws && ws.readyState === WebSocket.OPEN) {
      setStatus("저장 중…");
      ws.send(JSON.stringify({ type: "stop" }));
    } else {
      releaseCamera();
    }
  }

  $("start").addEventListener("click", start);
  $("stop").addEventListener("click", stop);

  return () => {  // leaving the screen: close socket (server stores it as abandoned) and free the camera
    disposed = true;
    stopping = true;
    if (ws) ws.close();
    releaseCamera();
  };
}
