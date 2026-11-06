// History & statistics screen: summary tiles, weekly reps, session list, session detail, mistakes.
import { getClientId } from "/app.js";

const CODE_LABEL = { DEPTH_SHALLOW: "깊이 부족", TRUNK_LEAN: "상체 숙임" };
const STATUS_LABEL = { completed: "완료", abandoned: "중단" };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (iso) => new Date(iso).toLocaleString("ko-KR",
  { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
const fmtScore = (v) => (v == null ? "-" : Number(v).toFixed(1));

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export function initHistory(root) {
  root.innerHTML = `<section class="history"><div class="status">불러오는 중…</div></section>`;
  const box = root.querySelector(".history");
  const cid = encodeURIComponent(getClientId());
  let alive = true;

  async function showList() {
    try {
      const [summary, sessions, mistakes] = await Promise.all([
        getJSON(`/api/stats/summary?client_id=${cid}`),
        getJSON(`/api/sessions?client_id=${cid}&limit=50`),
        getJSON(`/api/stats/mistakes?client_id=${cid}`),
      ]);
      if (!alive) return;
      if (!summary.total_sessions) {
        box.innerHTML = `<div class="empty">아직 기록이 없습니다.<br><a href="#/workout">첫 운동을 시작해 보세요</a></div>`;
        return;
      }
      const maxReps = Math.max(...summary.weekly.map((w) => w.reps), 1);
      box.innerHTML = `
        <div class="tiles">
          <div class="tile"><small>총 세션</small><b>${summary.total_sessions}</b></div>
          <div class="tile"><small>총 반복</small><b>${summary.total_reps}</b></div>
          <div class="tile"><small>평균 점수</small><b>${fmtScore(summary.avg_score)}</b></div>
        </div>
        <div class="grid2">
          <div class="card">
            <h3>주간 반복 수</h3>
            <div class="bars">${summary.weekly.map((w) => `
              <div class="bar-row"><span>${esc(w.week.slice(5))} 주</span>
                <div class="bar"><i style="width:${(w.reps / maxReps) * 100}%"></i></div>
                <em>${w.reps}회 · ${fmtScore(w.avg_score)}점</em></div>`).join("")}
            </div>
          </div>
          <div class="card">
            <h3>자주 하는 실수</h3>
            ${mistakes.length ? `<ol class="mistakes">${mistakes.map((m) =>
              `<li><span>${esc(CODE_LABEL[m.code] || m.code)}</span><b>${m.count}회</b></li>`).join("")}</ol>`
              : `<p class="muted">감지된 실수가 없습니다. 좋습니다!</p>`}
          </div>
        </div>
        <div class="card">
          <h3>세션 기록</h3>
          <table class="sessions">
            <thead><tr><th>날짜</th><th>반복</th><th>평균 점수</th><th>변화</th><th>상태</th></tr></thead>
            <tbody>${sessions.items.map((s) => `
              <tr data-id="${esc(s.id)}">
                <td>${fmtDate(s.started_at)}</td><td>${s.total_reps}</td><td>${fmtScore(s.avg_score)}</td>
                <td class="${s.score_delta > 0 ? "up" : s.score_delta < 0 ? "down" : ""}">${
                  s.score_delta == null ? "-" : (s.score_delta > 0 ? "+" : "") + s.score_delta.toFixed(1)}</td>
                <td>${STATUS_LABEL[s.status] || esc(s.status)}</td></tr>`).join("")}
            </tbody>
          </table>
        </div>`;
      box.querySelectorAll("tr[data-id]").forEach((tr) =>
        tr.addEventListener("click", () => showDetail(tr.dataset.id)));
    } catch (e) {
      if (alive) box.innerHTML = `<div class="status error">기록을 불러오지 못했습니다 (${esc(e.message)}).</div>`;
    }
  }

  async function showDetail(id) {
    try {
      const s = await getJSON(`/api/sessions/${encodeURIComponent(id)}`);
      if (!alive) return;
      box.innerHTML = `
        <button class="back" id="back">← 목록</button>
        <div class="card">
          <h3>${fmtDate(s.started_at)} · ${s.total_reps}회 · 평균 ${fmtScore(s.avg_score)}점</h3>
          <table class="sessions">
            <thead><tr><th>#</th><th>최저 무릎 각도</th><th>최대 상체 기울기</th><th>점수</th><th>피드백</th></tr></thead>
            <tbody>${s.reps.map((r) => `
              <tr><td>${r.rep_no}</td><td>${r.min_knee_angle.toFixed(0)}°</td><td>${r.max_trunk_tilt.toFixed(0)}°</td>
                <td>${r.score}</td><td>${r.feedback_codes.map((c) => esc(CODE_LABEL[c] || c)).join(", ") || "-"}</td></tr>`).join("")}
            </tbody>
          </table>
        </div>`;
      box.querySelector("#back").addEventListener("click", showList);
    } catch (e) {
      if (alive) box.innerHTML = `<div class="status error">세션을 불러오지 못했습니다 (${esc(e.message)}).</div>`;
    }
  }

  showList();
  return () => { alive = false; };
}
