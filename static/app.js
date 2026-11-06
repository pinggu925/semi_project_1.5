// Client identity + hash routing. No login: a random UUID kept in localStorage.
import { initWorkout } from "/workout.js";

const KEY = "squatcoach.client_id";
let memoryId = null;

export function getClientId() {
  try {
    let id = localStorage.getItem(KEY);
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    memoryId = memoryId || crypto.randomUUID();
    return memoryId;
  }
}

export function getNickname() {
  try { return localStorage.getItem("squatcoach.nickname") || ""; } catch { return ""; }
}

export function setNickname(name) {
  try { localStorage.setItem("squatcoach.nickname", name); } catch { /* ignore */ }
}

let cleanup = null;

async function route() {
  const view = document.getElementById("view");
  const name = location.hash === "#/history" ? "history" : "workout";
  document.querySelectorAll("nav a").forEach((a) =>
    a.classList.toggle("active", a.dataset.route === name));
  if (cleanup) { cleanup(); cleanup = null; }
  view.innerHTML = "";
  if (name === "history") {
    const { initHistory } = await import("/history.js");
    cleanup = initHistory(view) || null;
  } else {
    cleanup = initWorkout(view) || null;
  }
}

window.addEventListener("hashchange", route);
route();
