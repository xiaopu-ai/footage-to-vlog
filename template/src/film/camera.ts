// 运镜：静态重构图（frame）+ 运镜预设（move）+ 采访双机位，最后夹住位移，保证画面四边不露黑。
// 变换写成 translate(tx,ty) scale(s)，transform-origin = (ox, oy) 像素。
import type { Model, NShot } from "./normalize";
import { clamp01, ez, lin, num } from "./util";

export type Cam = { s: number; tx: number; ty: number; ox: number; oy: number };

/** 夹住位移：放大 s 倍后，画面边缘不能进到画框里 */
export const cover = (c: Cam, W: number, H: number): Cam => {
  const s = Math.max(1, c.s);
  const tx = Math.min(c.ox * (s - 1), Math.max(-(W - c.ox) * (s - 1), c.tx));
  const ty = Math.min(c.oy * (s - 1), Math.max(-(H - c.oy) * (s - 1), c.ty));
  return { ...c, s, tx, ty };
};

export const camCss = (c: Cam) => ({
  transform: `translate(${c.tx.toFixed(2)}px, ${c.ty.toFixed(2)}px) scale(${c.s.toFixed(5)})`,
  transformOrigin: `${c.ox.toFixed(1)}px ${c.oy.toFixed(1)}px`,
});

export const shotCam = (m: Model, s: NShot, g: number): Cam => {
  const { W, H } = m;
  const mv = s.mv;
  let sc = 1, tx = 0, ty = 0, ox = W / 2, oy = H / 2;
  if (mv.type === "interview") {
    // 访谈示例片 camOf：同一段采访镜头不复位，整段缓推 +0.04；剪气口处全景 1.03 / 近景 1.18 交替；让位时整体放大并左移
    ox = 0.54 * W;
    oy = 0.3 * H;
    const rp = clamp01((g - mv.run_from) / Math.max(1, mv.run_to - mv.run_from));
    const k = mv.make_room ? 1 : 0;
    sc = (mv.tight ? 1.18 + 0.07 * k : 1.03 + 0.09 * k) + 0.04 * rp;
    const side = mv.make_room === "left" ? 1 : -1;
    const room = mv.make_room === "left" ? Math.min(270 * k, ox * (sc - 1) - 2) : Math.min(270 * k, (W - ox) * (sc - 1) - 2);
    tx = side * Math.max(0, room);
    ty = Math.min(20 * k, (H - oy) * (sc - 1));
  } else {
    // 其余运镜在整个可见区间里走完（含转场重叠），线性
    const a = Math.min(s.vFrom, s.cut), b = Math.max(s.vTo, s.to);
    const p = clamp01((g - a) / Math.max(1, b - a));
    const amt = mv.amt;
    if (mv.type === "push") sc = 1 + amt * p;
    else if (mv.type === "pull") sc = 1 + amt * (1 - p);
    else if (mv.type === "drift-left" || mv.type === "drift-right") {
      // 发布片 driftL / driftR：放大 1.07，横移 ±30 px（amt 0.06 时）
      const d = mv.type === "drift-left" ? 1 : -1;
      sc = 1 + amt + 0.01;
      tx = lin(p, [0, 1], [amt * 500 * d, -amt * 500 * d]);
    } else if (mv.type === "rise") {
      // 机位升起：画面内容往下走
      sc = 1 + amt + 0.01;
      ty = lin(p, [0, 1], [-amt * 400, amt * 400]);
    }
  }
  return cover({ s: sc * s.fsc, tx: tx + s.fx, ty: ty + s.fy, ox, oy }, W, H);
};

// ── 倒计时（slam）对整个画面的作用：每一下镜头推近一档 + 衰减震动（访谈示例片 CountPush / shakeAt） ──
export const slamShake = (t: { from: number }, g: number, amp0: number) => {
  const k = g - t.from - 2; // 字落定那一帧开始震
  if (k < 0 || k > 10) return { x: 0, y: 0 };
  const amp = amp0 * Math.exp(-k / 2.6);
  return { x: amp * Math.sin(k * 2.7 + t.from), y: amp * Math.cos(k * 3.3 + t.from) };
};

export const slamCamera = (m: Model, g: number) => {
  let x = 0, y = 0, push = 0, active = false;
  for (const t of m.texts) {
    if (t.type !== "slam") continue;
    if (g < t.from) continue;
    // 推近一档：落在哪一镜，就推到那一镜结束（下一刀复位）
    const host = m.shots.find((s) => s.layer === 0 && t.from >= s.cut && t.from < s.outCut);
    const end = host ? host.outCut : t.to;
    if (g >= end) continue;
    active = true;
    push += num(t.push, 0.06) * lin(g, [t.from + 1, t.from + 4], [0, 1], ez);
    const sh = slamShake(t, g, num(t.shake, 14));
    x += sh.x * 0.6;
    y += sh.y * 0.6;
  }
  if (!active) return null;
  return { x, y, s: 1 + push + 0.02 };
};
