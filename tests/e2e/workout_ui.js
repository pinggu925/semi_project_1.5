// Browser checks for the workout screen. Needs a running server:
//   uvicorn app.main:create_app --factory   then   node tests/e2e/workout_ui.js http://localhost:8000
// The WebSocket is mocked, so no database traffic happens.
const { chromium } = require("playwright");
const assert = require("assert");

const BASE = process.argv[2] || "http://localhost:8000";
const ARGS = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"];

async function newPage(browser) {
  const ctx = await browser.newContext({ permissions: ["camera"] });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log("PAGEERROR", e.message));
  return page;
}

async function serverErrorIsShown(browser) {
  const page = await newPage(browser);
  await page.routeWebSocket("**/ws/workout", (ws) => {
    ws.onMessage((m) => {
      if (typeof m === "string" && JSON.parse(m).type === "start") {
        ws.send(JSON.stringify({ type: "started", session_id: "s" }));
        ws.send(JSON.stringify({ type: "error", code: "REDIS_UNAVAILABLE", message: "세션 저장소 연결이 끊겼습니다." }));
        ws.close();
      }
    });
  });
  await page.goto(BASE + "/#/workout");
  await page.click("#start");
  await page.waitForTimeout(1500);
  const status = await page.textContent("#status");
  assert.ok(status.includes("세션 저장소 연결이 끊겼습니다"), `status was: ${status}`);
  assert.strictEqual(await page.isDisabled("#start"), false, "start button usable again");
  await page.context().close();
}

async function leavingDuringPermissionPromptReleasesCamera(browser) {
  const page = await newPage(browser);
  let sockets = 0;
  page.on("websocket", () => sockets++);
  await page.addInitScript(() => {
    const md = navigator.mediaDevices;
    const orig = md.getUserMedia.bind(md);
    window.__streams = [];
    md.getUserMedia = (c) => new Promise((resolve) => {
      window.__grant = async () => {
        const s = await orig(c);
        window.__streams.push(s);
        resolve(s);
      };
    });
  });
  await page.goto(BASE + "/#/workout");
  await page.click("#start");              // permission "prompt" is pending
  await page.evaluate(() => { location.hash = "#/history"; });
  await page.waitForTimeout(300);
  await page.evaluate(() => window.__grant());  // user allows the camera after leaving
  await page.waitForTimeout(800);
  const live = await page.evaluate(() =>
    window.__streams.flatMap((s) => s.getTracks()).filter((t) => t.readyState === "live").length);
  assert.strictEqual(live, 0, "camera tracks must be stopped");
  assert.strictEqual(sockets, 0, "no websocket may be opened by a detached screen");
  await page.context().close();
}

(async () => {
  const browser = await chromium.launch({ args: ARGS });
  let failed = 0;
  for (const t of [serverErrorIsShown, leavingDuringPermissionPromptReleasesCamera]) {
    try { await t(browser); console.log("PASS", t.name); }
    catch (e) { failed++; console.log("FAIL", t.name, "-", e.message); }
  }
  await browser.close();
  process.exit(failed ? 1 : 0);
})();
