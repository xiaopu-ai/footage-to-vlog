// 把 timeline.json 整理成渲染要用的模型：补默认值、按层排序、找相邻镜头、算转场窗口、采访机位运行、派生字卡。
// 只在 timeline 变化时算一次（useMemo）。
import type { Caption, MoveType, Shot, Text, Timeline, TrType } from "./types";
import { num } from "./util";

export const TR_LEN: Record<TrType, number> = { cut: 0, dip: 8, fade: 12, whip: 8, punch: 8, sweep: 10, wipe: 12 };
const OVERLAP: TrType[] = ["fade", "sweep", "wipe"];

/** pre / post：转场在切点前 / 后各占几帧（timeline.py 写好；缺省 pre = len//2、post = len − pre，punch 的 post = len） */
export type NTr = { type: TrType; len: number; pre: number; post: number; dir: "left" | "right"; angle: number };
export type NMove = { type: MoveType; amt: number; run_from: number; run_to: number; tight: boolean; make_room: "right" | "left" | null };

export type NShot = Shot & {
  key: string;
  layer: number;
  idx: number;
  mv: NMove;
  inTr: NTr;
  outTr: NTr;
  prev?: NShot;
  next?: NShot;
  /** 名义出点（下一刀）：timeline 的 end；没有就取下一镜的 cut，再没有取 to */
  outCut: number;
  /** 实际渲染的帧区间 [vFrom, vTo)：转场需要而片段帧不够时，首 / 末帧定格补齐（不露黑） */
  vFrom: number;
  vTo: number;
  /** 入场转场窗口 [cut − pre, cut + post) */
  inWin: [number, number];
  /** 出场转场窗口：有下一镜就是下一镜的 inWin，没有就按本镜自带的 out_tr 以 outCut 为中心算 */
  outWin: [number, number];
  /** 本镜自带出场转场、后面没有接镜（插入层收尾、最后一镜淡出） */
  dangling: boolean;
  /** 黑场镜头（src / clip 为 null） */
  black: boolean;
  fsc: number;
  fx: number;
  fy: number;
  sky: number;
  vig: number;
};

export type Model = {
  tl: Timeline;
  W: number;
  H: number;
  fps: number;
  duration: number;
  accent: string;
  shots: NShot[];
  texts: Text[];
  slamGroups: { from: number; to: number }[];
  fogs: { from: number; to: number; at: number }[];
  captions: Caption[];
  capSize: number;
  capBottom: number;
  capHide: [number, number][];
};

const normTr = (t: unknown): NTr => {
  const o = (t && typeof t === "object" ? t : { type: typeof t === "string" ? t : "cut" }) as Record<string, unknown>;
  const type = (Object.keys(TR_LEN).includes(o.type as string) ? o.type : "cut") as TrType;
  const len = type === "cut" ? 0 : Math.max(0, Math.round(num(o.len, TR_LEN[type])));
  const pre = Math.max(0, Math.round(num(o.pre, Math.floor(len / 2))));
  const post = Math.max(0, Math.round(num(o.post, type === "punch" ? len : len - Math.floor(len / 2))));
  return { type, len, pre, post, dir: o.dir === "right" ? "right" : "left", angle: num(o.angle, 100) };
};

// 编译器没写 vignette 时的风格默认值（和 timeline.py 一致）
const DEFAULT_VIGNETTE: Record<string, number> = { testimonial: 0.15, launch: 0.2, hype: 0.25, manifesto: 0.3 };

export const buildModel = (tl: Timeline): Model => {
  const W = num(tl.width, 1920), H = num(tl.height, 1080), fps = num(tl.fps, 30);
  const duration = Math.max(1, Math.round(num(tl.duration, 1)));
  const vigDefault = DEFAULT_VIGNETTE[tl.style ?? ""] ?? 0.2;

  const shots: NShot[] = (tl.shots ?? []).map((s, i) => {
    const mvRaw = (s.move && typeof s.move === "object" ? s.move : { type: s.move ?? "none" }) as Record<string, unknown>;
    const type = (["none", "push", "pull", "drift-left", "drift-right", "rise", "interview"].includes(mvRaw.type as string) ? mvRaw.type : "none") as MoveType;
    const fr = s.frame ?? {};
    const mr = (mvRaw.make_room ?? s.make_room ?? null) as NMove["make_room"];
    return {
      ...s,
      key: `${s.id ?? "s"}-${i}`,
      layer: num(s.layer, 0),
      idx: 0,
      mv: {
        type,
        amt: num(mvRaw.amt, num(s.move_amt, 0.06)),
        run_from: num(mvRaw.run_from, NaN),
        run_to: num(mvRaw.run_to, NaN),
        tight: mvRaw.tight as boolean,
        make_room: mr === "right" || mr === "left" ? mr : null,
      },
      inTr: normTr(s.in_tr),
      outTr: normTr(s.out_tr),
      outCut: s.to,
      vFrom: s.from,
      vTo: s.to,
      inWin: [s.cut, s.cut],
      outWin: [s.to, s.to],
      dangling: false,
      black: !s.clip,
      fsc: Math.max(1, num(fr.scale, 1)),
      fx: num(fr.x, 0),
      fy: num(fr.y, 0),
      sky: num(s.sky, 0),
      vig: num(s.vignette, vigDefault),
    };
  });

  // 按层、按切点排序，找同层相邻镜头（首尾相接或重叠才算相邻）
  const layers = new Map<number, NShot[]>();
  for (const s of shots) {
    if (!layers.has(s.layer)) layers.set(s.layer, []);
    layers.get(s.layer)!.push(s);
  }
  for (const list of layers.values()) {
    list.sort((a, b) => a.cut - b.cut || a.from - b.from);
    list.forEach((s, i) => {
      s.idx = i;
      const p = list[i - 1];
      if (p && p.to >= s.from - 1) {
        s.prev = p;
        p.next = s;
      }
    });
  }
  // 转场窗口以切点为中心：入场 [cut − pre, cut + post)；出场 = 下一镜的入场，没有下一镜就用本镜自带的 out_tr
  for (const b of shots) {
    const t = b.inTr;
    b.inWin = t.type === "cut" ? [b.cut, b.cut] : [b.cut - t.pre, b.cut + t.post];
  }
  for (const s of shots) {
    s.outCut = num(s.end, s.next ? s.next.cut : s.to);
    if (s.next) {
      s.outTr = s.next.inTr;
      s.outWin = s.next.inWin;
    } else {
      s.dangling = s.outTr.type !== "cut";
      s.outWin = s.dangling ? [s.outCut - s.outTr.pre, s.outCut + s.outTr.post] : [s.outCut, s.outCut];
    }
  }
  // 重叠类转场（fade / sweep / wipe）两镜要同时可见：片段帧不够就用首 / 末帧定格补（vFrom / vTo 外扩）
  for (const b of shots) {
    if (!OVERLAP.includes(b.inTr.type)) continue;
    b.vFrom = Math.min(b.vFrom, b.inWin[0]);
    if (b.prev) b.prev.vTo = Math.max(b.prev.vTo, b.inWin[1]);
  }
  for (const s of shots) if (s.dangling && OVERLAP.includes(s.outTr.type)) s.vTo = Math.max(s.vTo, s.outWin[1]);

  // 采访机位运行：timeline.py 没给 run / tight 时自己算（同层首尾相接的 interview 镜头算一段；短于 15 帧的碎片沿用上一档）
  const l0 = layers.get(0) ?? [];
  let runStart = 0;
  let tight = false;
  for (let i = 0; i < l0.length; i++) {
    const s = l0[i];
    if (s.mv.type !== "interview") continue;
    const p = s.prev && s.prev.mv.type === "interview" ? s.prev : undefined;
    if (!p) {
      runStart = s.cut;
      tight = false;
    } else if (s.to - s.cut >= 15) tight = !tight;
    if (typeof s.mv.tight !== "boolean") s.mv.tight = tight;
    if (!Number.isFinite(s.mv.run_from)) s.mv.run_from = runStart;
  }
  for (let i = l0.length - 1; i >= 0; i--) {
    const s = l0[i];
    if (s.mv.type !== "interview" || Number.isFinite(s.mv.run_to)) continue;
    const n = s.next && s.next.mv.type === "interview" ? s.next : undefined;
    s.mv.run_to = n && Number.isFinite(n.mv.run_to) ? n.mv.run_to : s.to;
  }
  for (const s of shots) {
    if (!Number.isFinite(s.mv.run_from)) s.mv.run_from = s.cut;
    if (!Number.isFinite(s.mv.run_to)) s.mv.run_to = s.to;
    if (typeof s.mv.tight !== "boolean") s.mv.tight = false;
  }

  // 文字：补 from/to 取整，问题卡派生章节角标
  const raw = (tl.texts ?? []).filter((t) => t && typeof t.type === "string" && t.to > t.from);
  const texts: Text[] = raw.map((t, i) => ({ ...t, id: t.id ?? `${t.type}${i}`, from: Math.round(t.from), to: Math.round(t.to) }));
  const qcards = texts.filter((t) => t.type === "qcard").sort((a, b) => a.from - b.from);
  const enders = texts.filter((t) => t.type === "lockup" || t.type === "endcard").map((t) => t.from);
  qcards.forEach((q, i) => {
    if (typeof q.of !== "number") q.of = qcards.length;
    if (typeof q.no !== "number") q.no = i + 1;
    if (typeof q.tag !== "string" || !q.tag) return;
    const nextQ = qcards[i + 1]?.from ?? duration;
    const end = Math.min(num(q.tag_to, nextQ), nextQ, ...enders.filter((e) => e > q.to), duration);
    if (end - q.to >= 20) texts.push({ id: `${q.id}-tag`, type: "qtag", from: q.to, to: end, z: num(q.z, 10), no: q.no, of: q.of, tag: q.tag, logo: q.logo });
  });
  const specs = texts.filter((t) => t.type === "spec").sort((a, b) => a.from - b.from);
  specs.forEach((t, i) => {
    if (typeof t.of !== "number") t.of = specs.length;
    if (typeof t.no !== "number") t.no = i + 1;
  });
  texts.sort((a, b) => num(a.z, 10) - num(b.z, 10) || a.from - b.from);

  // 倒计时一组（首尾相接的 slam）：一起压暗四周
  const slams = texts.filter((t) => t.type === "slam").sort((a, b) => a.from - b.from);
  const slamGroups: { from: number; to: number }[] = [];
  for (const t of slams) {
    const g = slamGroups[slamGroups.length - 1];
    if (g && t.from <= g.to + 1) g.to = Math.max(g.to, t.to);
    else slamGroups.push({ from: t.from, to: t.to });
  }

  // 落版成雾（口号片 Ending）：从定格那一帧（hold_from，绝对帧号）开始、60 帧内画面模糊压暗；
  // 落版字下面没有定格时，从字出现前 12 帧开始
  const fogs = texts
    .filter((t) => t.type === "lockup")
    .map((t) => {
      const host = shots.find((s) => s.layer === 0 && t.from >= s.vFrom && t.from < s.vTo);
      const hold = host && typeof host.hold_from === "number" ? host.hold_from : null;
      const from = hold !== null && hold <= t.from && hold >= t.from - 60 ? hold : t.from - 12;
      return { from, to: t.to, at: t.from };
    });

  const capObj = Array.isArray(tl.captions) ? null : tl.captions ?? null;
  const captions = (Array.isArray(tl.captions) ? tl.captions : capObj?.items ?? []).filter((c) => c && c.text && c.to > c.from);
  const capSize = num(tl.caption_style?.size, num(capObj?.size, 46));
  const capBottom = num(tl.caption_style?.bottom, num(capObj?.bottom, 90));
  const capHide: [number, number][] = texts.filter((t) => ["qcard", "lockup", "endcard", "slam"].includes(t.type)).map((t) => [t.from, t.to]);

  return {
    tl,
    W,
    H,
    fps,
    duration,
    accent: typeof tl.brand?.accent === "string" ? tl.brand.accent : "#FF7A1A",
    shots,
    texts,
    slamGroups,
    fogs,
    captions,
    capSize,
    capBottom,
    capHide,
  };
};
