// 七种转场在某一帧对某一镜的作用。窗口以切点为中心：入场 [cut − pre, cut + post)，出场 = 下一镜的入场窗口
// （没有下一镜时用本镜自带的 out_tr，以 outCut 为中心）。pre / post 由 timeline.py 写进 in_tr / out_tr。
//   cut   硬切
//   dip   切点前 pre 帧淡到黑、切点后 post 帧从黑淡入（主轨的 dip 作用于整幅画面，见 dipAt）
//   fade  两镜重叠 len 帧交叉叠化
//   whip  上一镜最后 pre 帧横向甩出 + 拖影，本镜前 post 帧从反方向带拖影进（燃剪甩镜；拖影用多层位移叠加，不用 blur）
//   punch 上一镜最后 pre 帧加速推到 ×1.2 + 变焦拖影；本镜前 post 帧从 1.15 回落到 1，同时提亮 16%（发布片冲击推镜）
//   sweep 柔光带扫过，后面露出本镜，换镜藏在光带最亮处（发布片光扫：补光带 + 高光 + 暖色空气光）
//   wipe  本镜用蒙版从左往右擦入，入镜画面轻推（访谈示例片问题卡的小转场）
import type { NShot } from "./normalize";
import { band, ez, inOutCubic, inQuad, lin, outCubic } from "./util";

export type Trail = { kind: "x"; span: number; n: number } | { kind: "zoom"; amount: number; n: number };
export type LightPass = { p: number; w: number; ang: number; stay: boolean; lift: number };

export type ShotFx = {
  opacity: number;
  clip?: string; // wipe：clip-path
  mask?: string; // sweep：揭示遮罩
  scale: number; // 叠在运镜外面的缩放（punch / whip / wipe 轻推）
  tx: number; // whip 位移
  trail?: Trail;
  bright: number;
  blur: number;
  light?: LightPass; // sweep 转场经过本镜的光
};

export const SWEEP_W = 45; // 补光带半宽 45%（用户否掉「一根白线」之后的定稿）

/** sweep 光带在窗口 [a, b) 里第 g 帧的位置（%），inOut cubic，窗口最后一帧刚好走完 */
export const sweepPos = (a: number, b: number, g: number) => -SWEEP_W + lin(g + 1, [a, b], [0, 1], inOutCubic) * (100 + 2 * SWEEP_W);

export const shotFx = (s: NShot, g: number): ShotFx => {
  const fx: ShotFx = { opacity: 1, scale: 1, tx: 0, bright: 1, blur: 0 };

  // ── 入场 ──
  const it = s.inTr;
  const [ia, ib] = s.inWin;
  const c = s.cut;
  if (it.type !== "cut" && it.len > 0) {
    if (it.type === "dip") {
      // 主轨（layer 0）的 dip 是整幅画面淡到黑，在 Film 里统一算（dipAt）；插入层自己淡入
      if (s.layer !== 0 && g < ib) fx.opacity = lin(g, [c, ib], [0, 1]);
    } else if (it.type === "fade") {
      if (g < ib) fx.opacity = lin(g + 1, [ia, ib + 1], [0, 1]);
    } else if (it.type === "whip") {
      if (g >= c && g < ib) {
        const w = (1 - (g - c) / Math.max(1, ib - c)) ** 3;
        const d = it.dir === "right" ? -1 : 1;
        fx.tx = d * 300 * w;
        fx.trail = { kind: "x", span: d * 120 * w, n: 6 };
        fx.scale = 1 + 0.38 * w + 0.01;
      }
    } else if (it.type === "punch") {
      if (g >= c && g < ib) {
        const t = lin(g - c, [0, ib - c], [0, 1], outCubic);
        fx.scale = 1.15 - 0.15 * t;
        fx.trail = { kind: "zoom", amount: 0.03 * (1 - t), n: 4 };
        fx.bright = 1 + 0.16 * lin(g - c, [0, 5], [1, 0]);
      }
    } else if (it.type === "sweep") {
      if (g < ib) {
        const p = sweepPos(ia, ib, g);
        // 光带中心之后是本镜，之前还是上一镜；交界的软边藏在光带最亮处
        fx.mask = band(it.angle, p, SWEEP_W * 0.6, 1, true);
        fx.light = { p, w: SWEEP_W, ang: it.angle, stay: false, lift: 1.25 };
      }
    } else if (it.type === "wipe") {
      if (g < ib) fx.clip = `inset(0 ${((1 - lin(g + 1, [ia, ib], [0, 1], ez)) * 100).toFixed(3)}% 0 0)`;
      const k = lin(g - ia, [0, it.len], [1, 0], ez);
      fx.scale *= 1 + 0.07 * k;
      fx.blur = Math.max(fx.blur, 5 * k);
    }
  }

  // ── 出场（下一镜入场转场里属于上一镜的那一半；没有下一镜时是本镜自带的收尾） ──
  const ot = s.outTr;
  const [oa, ob] = s.outWin;
  const oc = s.outCut;
  if (ot.type !== "cut" && ot.len > 0 && g >= oa) {
    if (ot.type === "dip") {
      if (s.layer !== 0) fx.opacity = Math.min(fx.opacity, g < oc ? lin(g, [oa, oc], [1, 0]) : 0);
    } else if (ot.type === "whip") {
      if (g < oc) {
        const w = ((g - oa + 1) / Math.max(1, oc - oa)) ** 3;
        const d = ot.dir === "right" ? -1 : 1;
        fx.tx = -d * 300 * w;
        fx.trail = { kind: "x", span: -d * 120 * w, n: 6 };
        fx.scale = 1 + 0.38 * w + 0.01;
      } else if (s.dangling) fx.opacity = 0;
    } else if (ot.type === "punch") {
      if (g < oc) {
        const t = lin(g, [oa, oc], [0, 1], inQuad);
        fx.scale *= 1 + 0.2 * t;
        fx.trail = { kind: "zoom", amount: 0.035 * t, n: 4 };
      } else if (s.dangling) fx.opacity = 0;
    } else if (ot.type === "sweep" && g < ob) {
      // 光带同样照在上一镜上（被本镜遮住之前的那一段）
      fx.light = { p: sweepPos(oa, ob, g), w: SWEEP_W, ang: ot.angle, stay: false, lift: 1.25 };
    }
    // 自带收尾、后面没有接镜的叠化 / 擦除 / 光扫：淡出，露出下面一层（主轨就是黑）
    if (s.dangling && (ot.type === "fade" || ot.type === "wipe" || ot.type === "sweep")) fx.opacity = Math.min(fx.opacity, lin(g, [oa, ob], [1, 0]));
  }
  return fx;
};

/** 主轨 dip：整幅画面（含插入层）在切点前 pre 帧淡到黑、切点后 post 帧淡回来；最后一镜自带的 dip 收尾淡到黑 */
export const dipAt = (shots: NShot[], g: number): number => {
  let o = 1;
  for (const s of shots) {
    if (s.layer !== 0) continue;
    if (s.inTr.type === "dip" && s.inTr.len > 0) {
      const [a, b] = s.inWin, c = s.cut;
      if (g >= a && g < b) o = Math.min(o, g < c ? lin(g, [a, c], [1, 0]) : lin(g, [c, b], [0, 1]));
    }
    if (s.dangling && s.outTr.type === "dip") {
      const [a, b] = s.outWin, c = s.outCut;
      if (g >= a && g < b) o = Math.min(o, g < c ? lin(g, [a, c], [1, 0]) : 0);
    }
  }
  return o;
};
