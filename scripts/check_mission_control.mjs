/**
 * End-to-end check of the mission-control view in a real browser.
 *
 * Typechecking and a production build prove the code compiles. They do not
 * prove that the terrain mesh renders, that clicking the terrain places a
 * target where the operator aimed, or that a 30-second Monte Carlo request
 * comes back before the page gives up. This drives the real thing and fails on
 * any console error, page error, or failed request.
 *
 * Not in CI: it needs a browser download and a running stack, and a CI job that
 * installs Chromium to screenshot a page is a slow way to learn the build
 * works. It is here so the check is repeatable rather than something that
 * happened once on someone's machine.
 *
 *   # 1. a database, the API, and the dev server
 *   docker compose up -d db
 *   (cd backend && uvicorn app.main:app --port 8000)
 *   (cd frontend && npm run dev)
 *
 *   # 2. a mission with terrain analysed - note the id it prints
 *   curl -s -X POST localhost:8000/missions -F name=Check \
 *     -F terrain_image=@data/sample_terrain/synthetic_crater_field_512.png
 *   curl -s -X POST localhost:8000/missions/<id>/analyze-terrain > /dev/null
 *
 *   # 3. the check
 *   npm --prefix frontend install --no-save playwright
 *   npm --prefix frontend exec playwright install chromium
 *   node scripts/check_mission_control.mjs <mission-id>
 *
 * ASTRA_UI overrides the dev-server URL, ASTRA_SHOTS the screenshot directory,
 * and ASTRA_CHROMIUM an already-present browser binary.
 */

/**
 * Playwright is not a dependency of this repo. Adding it to the frontend's
 * package.json would make every `npm ci` - including CI's, which only needs to
 * typecheck and build - download a browser driver it never uses. So it is
 * resolved at runtime from wherever it happens to be installed, and the error
 * says how to install it rather than leaving a bare MODULE_NOT_FOUND.
 */
async function loadPlaywright() {
  const candidates = [
    "playwright",
    new URL("../frontend/node_modules/playwright/index.mjs", import.meta.url).href,
  ];
  for (const specifier of candidates) {
    try {
      return await import(specifier);
    } catch {
      /* try the next one */
    }
  }
  throw new Error(
    "playwright is not installed. Run:\n" +
      "  npm --prefix frontend install --no-save playwright\n" +
      "  npm --prefix frontend exec playwright install chromium",
  );
}

const { chromium } = await loadPlaywright();

const missionId = process.argv[2];
const base = process.env.ASTRA_UI ?? "http://127.0.0.1:5173";
const out = process.env.ASTRA_SHOTS ?? "/tmp";

if (!missionId) {
  console.error("usage: node scripts/check_mission_control.mjs <mission-id>");
  process.exit(2);
}

const problems = [];
const browser = await chromium.launch({
  // Software GL: CI machines and containers rarely have a real one, and a
  // WebGL context that silently fails to create would make every visual check
  // pass against a blank canvas.
  args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"],
  // For sandboxes that ship a Chromium whose build number does not match the
  // installed playwright. Point ASTRA_CHROMIUM at the binary rather than
  // downloading a second copy.
  ...(process.env.ASTRA_CHROMIUM ? { executablePath: process.env.ASTRA_CHROMIUM } : {}),
});
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text()}`));
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
page.on("requestfailed", (r) => problems.push(`requestfailed: ${r.url()}`));

function step(name) {
  console.log(`• ${name}`);
}

try {
  step("load mission control");
  await page.goto(`${base}/missions/${missionId}/control`, { waitUntil: "networkidle" });
  await page.waitForSelector("canvas", { timeout: 20000 });
  await page.waitForTimeout(2000);

  const canvas = await page.evaluate(() => {
    const el = document.querySelector("canvas");
    const gl = el?.getContext("webgl2") ?? el?.getContext("webgl");
    return { width: el?.width ?? 0, height: el?.height ?? 0, hasGL: Boolean(gl) };
  });
  if (!canvas.hasGL || canvas.width === 0) throw new Error(`no WebGL context: ${JSON.stringify(canvas)}`);
  console.log(`  canvas ${canvas.width}x${canvas.height}, WebGL ok`);
  await page.screenshot({ path: `${out}/mission-control-1-terrain.png` });

  step("simulate a traverse and play it back");
  await page.getByRole("button", { name: /simulate traverse/i }).click();
  await page.waitForSelector("text=/D\\* LITE REPAIRS/i", { timeout: 120000 });
  await page.waitForTimeout(6000);
  const replans = await page.evaluate(
    () => document.body.innerText.match(/D\* LITE REPAIRS\s+(\d+)/i)?.[1] ?? "0",
  );
  if (Number(replans) === 0) throw new Error("traverse reported no D* Lite repairs");
  console.log(`  ${replans} repairs recorded, event log populated`);
  await page.screenshot({ path: `${out}/mission-control-2-traverse.png` });

  step("sweep the objective weights");
  await page.getByRole("button", { name: "study", exact: true }).click();
  await page.getByRole("button", { name: /sweep objective weights/i }).click();
  await page.waitForSelector("text=on the front", { timeout: 120000 });
  await page.screenshot({ path: `${out}/mission-control-3-pareto.png` });

  step("run a robustness study");
  await page.getByRole("button", { name: "lab", exact: true }).click();
  await page.getByRole("button", { name: /run study/i }).click();
  await page.waitForSelector("text=/of \\d+ trials/", { timeout: 180000 });
  await page.screenshot({ path: `${out}/mission-control-4-lab.png` });

  step("place science targets and deconflict a fleet");
  await page.getByRole("button", { name: "fleet", exact: true }).click();
  const box = await page.locator("canvas").boundingBox();
  for (const [dx, dy] of [
    [-180, -60],
    [120, 40],
    [-40, 120],
  ]) {
    await page.getByRole("button", { name: /add science target/i }).click();
    await page.mouse.click(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy);
    await page.waitForTimeout(600);
  }
  await page.getByRole("button", { name: /plan science tour/i }).click();
  await page.waitForSelector("text=/Tour visits/", { timeout: 120000 });
  await page.getByRole("button", { name: /deconflict 3 rovers/i }).click();
  await page.waitForSelector("text=/Deconflicted in|was not deconflicted/", { timeout: 120000 });
  await page.screenshot({ path: `${out}/mission-control-5-fleet.png` });

  const summary = await page.evaluate(() => document.body.innerText);
  console.log(`  ${summary.match(/Tour visits[^\n]*/)?.[0] ?? "tour: no summary"}`);
  console.log(`  ${summary.match(/Deconflicted in[^\n]*|was not deconflicted[^\n]*/)?.[0] ?? "fleet: no summary"}`);
} catch (error) {
  problems.push(`step failed: ${error.message}`);
} finally {
  await browser.close();
}

if (problems.length) {
  console.error(`\nFAILED with ${problems.length} problem(s):`);
  problems.forEach((p) => console.error(`  - ${p}`));
  process.exit(1);
}
console.log(`\nAll checks passed. Screenshots in ${out}/mission-control-*.png`);
