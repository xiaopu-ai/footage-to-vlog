// 批量静帧：只打包一次（关 webpack 缓存）、只开一个浏览器，连续渲多帧整片画面。
//
// 用法（在项目根目录）：
//   node tools/stills.mjs [timeline.json] <输出目录> <帧…> [--scale=1] [--comp=Film|Frame] [--skip-existing]
//   例：node tools/stills.mjs /tmp/check 0 60 120
//       node tools/stills.mjs work/sf.timeline.json work/review/sf/_png 15 240 --scale=0.5
// - 帧号是整片帧（30 fps）；输出 <输出目录>/f0120.png。
// - 第一个参数是 .json 时，用它代替 src/timeline.json（作为 props.timeline 传进去，不改文件）。
// - 默认直接用 renderStill 渲 Film 合成的那一帧；--comp=Frame 改走 1 帧的 Frame 合成（每帧重新选一次合成，慢一点）。
//
// 🔴 本机同一时间只跑一个重任务：静帧批量也算。设了环境变量 RENDER_QUEUE（排队脚本的路径，一般是 skill 的 scripts/queue.sh）
//    而当前不在队列里（没有 FTV_QUEUE_HELD / RENDER_QUEUE_HELD）时，本脚本会把自己套进那个队列重跑。
//    也可以手动：<skill>/scripts/queue.sh node tools/stills.mjs …
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const QUEUE = process.env.RENDER_QUEUE || process.env.FTV_QUEUE;
if (QUEUE && !process.env.FTV_QUEUE_HELD && !process.env.RENDER_QUEUE_HELD) {
  const r = spawnSync(QUEUE, [process.execPath, ...process.argv.slice(1)], { stdio: "inherit" });
  process.exit(r.status ?? 1);
}

const { bundle } = await import("@remotion/bundler");
const { openBrowser, renderStill, selectComposition } = await import("@remotion/renderer");

const args = process.argv.slice(2);
const opt = (k, d) => {
  const a = args.find((x) => x.startsWith(`--${k}=`));
  return a ? a.slice(k.length + 3) : d;
};
const flag = (k) => args.includes(`--${k}`);
const pos = args.filter((a) => !a.startsWith("--"));
let tlPath = null;
if (pos[0] && pos[0].endsWith(".json")) tlPath = pos.shift();
const [outDir, ...frameArgs] = pos;
if (!outDir || !frameArgs.length) {
  console.error("用法: node tools/stills.mjs [timeline.json] <输出目录> <帧…> [--scale=1] [--comp=Film|Frame] [--skip-existing]");
  process.exit(1);
}
const frames = [...new Set(frameArgs.map((x) => Math.round(Number(x))).filter((x) => Number.isFinite(x) && x >= 0))].sort((a, b) => a - b);
const scale = Number(opt("scale", "1"));
const compId = opt("comp", "Film");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
fs.mkdirSync(outDir, { recursive: true });

const inputProps = tlPath ? { timeline: JSON.parse(fs.readFileSync(path.resolve(tlPath), "utf8")) } : {};
const todo = frames.filter((f) => !(flag("skip-existing") && fs.existsSync(path.join(outDir, `f${String(f).padStart(4, "0")}.png`))));
if (!todo.length) {
  console.log("全部已存在，跳过");
  process.exit(0);
}

const t0 = Date.now();
const serveUrl = await bundle({
  entryPoint: path.join(root, "src/index.ts"),
  publicDir: path.join(root, "public"),
  // 几个项目 / worktree 共用 node_modules 时，共享的 webpack 缓存会串（报 wasm-hash … reading 'length'），一律关掉
  enableCaching: false,
});
console.log(`打包 ${((Date.now() - t0) / 1000).toFixed(1)} s`);

const chromiumOptions = { gl: "angle" };
const browser = await openBrowser("chrome", { chromiumOptions });
let failed = 0;
try {
  const film = await selectComposition({ serveUrl, id: "Film", inputProps, puppeteerInstance: browser, chromiumOptions });
  const total = film.durationInFrames;
  for (const fr of todo) {
    const out = path.join(outDir, `f${String(fr).padStart(4, "0")}.png`);
    if (fr >= total) {
      console.warn(`⚠ 第 ${fr} 帧超出片长 ${total}，跳过`);
      continue;
    }
    try {
      if (compId === "Frame") {
        const props = { ...inputProps, frame: fr };
        const comp = await selectComposition({ serveUrl, id: "Frame", inputProps: props, puppeteerInstance: browser, chromiumOptions });
        await renderStill({ serveUrl, composition: comp, frame: 0, output: out, scale, inputProps: props, puppeteerInstance: browser, chromiumOptions, imageFormat: "png", timeoutInMilliseconds: 120000 });
      } else {
        await renderStill({ serveUrl, composition: film, frame: fr, output: out, scale, inputProps, puppeteerInstance: browser, chromiumOptions, imageFormat: "png", timeoutInMilliseconds: 120000 });
      }
      console.log("✓", out);
    } catch (e) {
      failed++;
      console.error(`✗ 第 ${fr} 帧:`, e?.message ?? e);
    }
  }
} finally {
  await browser.close({ silent: true }).catch(() => {});
}
console.log(`完成 ${todo.length - failed} 帧，用时 ${((Date.now() - t0) / 1000).toFixed(1)} s`);
process.exit(failed ? 1 : 0);
