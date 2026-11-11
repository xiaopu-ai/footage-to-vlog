// 十种文字 / 包装，全部从四条定版片移植（参数保持用户审过的原值）。
// 每个组件拿到 f = 文字内的第几帧（0 起）、dur = 时长，不用 useCurrentFrame，所以 Film 和 Frame 两个合成都能直接用。
import React from "react";
import { AbsoluteFill, Img, interpolate, spring } from "remotion";
import { slamShake } from "./camera";
import { fam } from "./fonts";
import type { Model } from "./normalize";
import type { Text } from "./types";
import { band, ez, ezIn, hasCJK, inOutCubic, lin, maskStyle, num, outCubic, pub, str } from "./util";

type TP = { m: Model; t: Text; f: number; dur: number };
const FPS = 30;
const W_ = "#fdfdfb";
const titleShadow = "0 2px 8px rgba(0,0,0,.30), 0 0 30px rgba(0,0,0,.12)";
const logoOf = (m: Model, v: unknown): string | null => {
  if (v === false || v === null) return null;
  if (typeof v === "string" && v) return v;
  const w = m.tl.brand?.logo_white;
  return typeof w === "string" && w ? w : null;
};

// ── slam：毛笔字 2 帧内从 2.6 倍砸到 1 倍 + 衰减震动（访谈示例片倒计时）。画面推近 / 震动在 camera.ts ──
const Slam: React.FC<TP> = ({ m, t, f }) => {
  const text = str(t.text);
  const size = num(t.size, 420);
  const color = str(t.color, "#fff");
  const nudge = Array.isArray(t.nudge) ? t.nudge : [0, 0];
  const { x, y } = slamShake(t, t.from + f, num(t.shake, 14));
  const sc = f < 2 ? lin(f, [0, 2], [2.6, 0.95], ezIn) : lin(f, [2, 5], [0.95, 1], ez);
  const blur = lin(f, [0, 2], [10, 0]);
  // 单个字（数字）加一圈同色描边加粗；多字（「走！」）不加
  const stroke = typeof t.stroke === "number" ? t.stroke : Array.from(text).length === 1 ? (7 * size) / 420 : 0;
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", transform: `translate(${(x + num(nudge[0], 0)).toFixed(2)}px, ${(y + num(nudge[1], 0)).toFixed(2)}px)` }}>
      <div
        style={{
          fontFamily: fam(m.tl, t.font, "brush"),
          fontSize: size,
          lineHeight: 1,
          color,
          whiteSpace: "nowrap",
          transform: `scale(${sc.toFixed(4)})`,
          opacity: lin(f, [0, 1], [0.4, 1]),
          filter: blur > 0.05 ? `blur(${blur.toFixed(2)}px)` : undefined,
          textShadow: "0 10px 34px rgba(0,0,0,.5), 0 2px 8px rgba(0,0,0,.4)",
          WebkitTextStroke: stroke > 0 ? `${stroke.toFixed(2)}px ${color}` : undefined,
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};

/** 倒计时一组（首尾相接的 slam）的四周压暗：0.2 → 0.75 */
export const SlamShade: React.FC<{ from: number; to: number; g: number }> = ({ from, to, g }) => {
  const a = lin(g, [from, from + 3], [0, 1]) * lin(g, [from - 6, to - 2], [0.2, 0.75]);
  return <AbsoluteFill style={{ background: `radial-gradient(ellipse at 50% 50%, rgba(0,0,0,0) 35%, rgba(0,0,0,${a.toFixed(3)}) 100%)` }} />;
};

// ── brushword：关键词毛笔字，裁切揭示 + 弹簧，带投影（访谈示例片 Brush） ──
const BrushWord: React.FC<TP> = ({ m, t, f, dur }) => {
  const o = Math.min(lin(f, [0, 6], [0, 1], ez), lin(f, [dur - 7, dur], [1, 0]));
  const s = spring({ frame: f, fps: FPS, config: { damping: 12, stiffness: 200, mass: 0.6 } });
  const align = t.align === "left" || t.align === "right" ? t.align : "center";
  const tr = align === "center" ? "-50%" : align === "right" ? "-100%" : "0";
  const size = num(t.size, 200);
  const rev = 100 - lin(f, [0, 8], [0, 100]);
  return (
    <div
      style={{
        position: "absolute",
        left: num(t.x, 960),
        top: num(t.y, 470),
        transform: `translate(${tr},-50%) scale(${interpolate(s, [0, 1], [1.5, 1]).toFixed(4)})`,
        transformOrigin: "center",
        opacity: o,
        fontFamily: fam(m.tl, t.font, "brush"),
        fontSize: size,
        lineHeight: 1.05,
        color: str(t.color, "#fff"),
        whiteSpace: "pre",
        textAlign: align,
        writingMode: t.vertical ? "vertical-rl" : undefined,
        textOrientation: t.vertical ? "upright" : undefined,
        textShadow: "0 8px 30px rgba(0,0,0,.45), 0 2px 6px rgba(0,0,0,.4)",
        letterSpacing: size * 0.04,
        clipPath: t.vertical ? `inset(0 0 ${rev}% 0)` : `inset(0 ${rev}% 0 0)`,
      }}
    >
      {str(t.text)}
    </div>
  );
};

// ── chapter：竖排章名，硬切进出，配天空压暗（口号片 ChapterTitle + SkyND） ──
const SkyND: React.FC<{ strength: number }> = ({ strength }) => (
  <AbsoluteFill
    style={{ background: `linear-gradient(180deg, rgba(20,30,40,${0.38 * strength}), rgba(20,30,40,${0.12 * strength}) 45%, rgba(0,0,0,0) 70%)` }}
  />
);
const Chapter: React.FC<TP> = ({ m, t }) => {
  const side = t.side === "right" ? "right" : "left";
  const nd = num(t.nd, 1);
  return (
    <>
      {nd > 0 && <SkyND strength={nd} />}
      <div
        style={{
          position: "absolute",
          left: num(t.cx, side === "left" ? 330 : 1590),
          top: num(t.cy, 540),
          transform: "translate(-50%, -50%)",
          writingMode: "vertical-rl",
          textOrientation: "upright",
          fontFamily: fam(m.tl, t.font, "brush"),
          fontSize: num(t.size, 150),
          lineHeight: 1,
          letterSpacing: "-0.12em",
          color: str(t.color, W_),
          textShadow: titleShadow,
          whiteSpace: "nowrap",
        }}
      >
        {str(t.text)}
      </div>
    </>
  );
};

// ── qcard：问题卡（访谈示例片 v7：蒙版从左擦入、序号翻牌、题目逐字浮起、光扫、序号穿屏退场；去掉了橙色细线） ──
const QCard: React.FC<TP> = ({ m, t, f, dur }) => {
  const A = m.accent;
  const no = num(t.no, 1), of = num(t.of, 1);
  const lines = Array.isArray(t.lines) ? (t.lines as unknown[]).map((x) => str(x)) : [str(t.text)];
  const hi = str(t.hi);
  const logo = logoOf(m, t.logo);
  const wipe = lin(f, [0, 12], [0, 1], ez);
  const a = lin(f, [4, 16], [0, 1], ez);
  const exit = lin(f, [dur - 16, dur], [0, 1], ezIn);
  const pad = (n: number) => String(n).padStart(2, "0");
  const flip = f < 12 ? pad(Math.floor((f * 7) % 99)) : pad(no);
  const prog = lin(f, [12, 30], [(no - 1) / of, no / of], ez);
  const sweep = lin(f, [30, 70], [-40, 140]);
  const fs = num(t.size, 84);
  let ci = 0;
  return (
    <AbsoluteFill>
      <AbsoluteFill
        style={{
          opacity: 1 - exit,
          clipPath: `inset(0 ${((1 - wipe) * 100).toFixed(3)}% 0 0)`,
          background: "linear-gradient(90deg, rgba(5,14,24,.9) 0%, rgba(5,14,24,.6) 44%, rgba(5,14,24,0) 74%)",
        }}
      />
      <div style={{ position: "absolute", left: 110, top: 180, opacity: 1 - exit * 0.9 }}>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 24 }}>
          <div
            style={{
              fontFamily: fam(m.tl, "num"),
              fontSize: 300,
              lineHeight: 0.8,
              color: "transparent",
              WebkitTextStroke: "3px rgba(255,255,255,.95)",
              opacity: a,
              transform: `scale(${(1 + exit * 9).toFixed(4)})`,
              transformOrigin: "40% 50%",
            }}
          >
            {flip}
          </div>
          <div style={{ paddingBottom: 18, opacity: a * (1 - exit) }}>
            <div style={{ fontFamily: fam(m.tl, "num"), fontSize: 34, color: A, letterSpacing: 6 }}>
              {str(t.label, "QUESTION")} · {pad(no)} / {pad(of)}
            </div>
            <div style={{ width: 300, height: 3, background: "rgba(255,255,255,.4)", marginTop: 12 }}>
              <div style={{ width: `${(prog * 100).toFixed(2)}%`, height: 3, background: A }} />
            </div>
          </div>
        </div>
        <div style={{ marginTop: 56, position: "relative", opacity: 1 - exit, transform: `translateY(${(-60 * exit).toFixed(2)}px)` }}>
          {lines.map((line, li) => {
            const hs = hi ? line.indexOf(hi) : -1;
            return (
              <div key={li} style={{ fontFamily: fam(m.tl, t.font, "title"), fontSize: fs, lineHeight: 1.22, color: "#fff", whiteSpace: "nowrap", textShadow: "0 4px 18px rgba(0,0,0,.35)" }}>
                {Array.from(line).map((ch, k) => {
                  const idx = ci++;
                  const tt = lin(f, [12 + idx * 1.2, 20 + idx * 1.2], [0, 1], ez);
                  const on = hs >= 0 && k >= hs && k < hs + Array.from(hi).length;
                  return (
                    <span key={k} style={{ display: "inline-block", opacity: tt, transform: `translateY(${((1 - tt) * 40).toFixed(2)}px)`, color: on ? A : "#fff" }}>
                      {ch === " " ? " " : ch}
                    </span>
                  );
                })}
              </div>
            );
          })}
          <div
            style={{
              position: "absolute",
              inset: -20,
              background: `linear-gradient(100deg, rgba(255,255,255,0) ${sweep - 15}%, rgba(255,255,255,.35) ${sweep}%, rgba(255,255,255,0) ${sweep + 15}%)`,
              mixBlendMode: "overlay",
              pointerEvents: "none",
              // 上下边羽化，免得亮水面上看出光扫的矩形边
              ...maskStyle("linear-gradient(180deg, rgba(0,0,0,0), #000 18%, #000 82%, rgba(0,0,0,0))"),
            }}
          />
        </div>
      </div>
      {logo && <Img src={pub(logo)} style={{ position: "absolute", right: 64, top: 40, width: 120, opacity: a * (1 - exit) }} />}
    </AbsoluteFill>
  );
};

/** 问题卡之后的章节角标（访谈示例片 ChapterTag），从卡片结束停到下一张问题卡 */
const QTag: React.FC<TP> = ({ m, t, f, dur }) => {
  const o = Math.min(lin(f, [0, 10], [0, 1], ez), lin(f, [dur - 8, dur], [1, 0]));
  const logo = logoOf(m, t.logo);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    <div style={{ position: "absolute", left: 56, top: 44, display: "flex", alignItems: "center", gap: 16, opacity: o }}>
      {logo && <Img src={pub(logo)} style={{ width: 70 }} />}
      {logo && <div style={{ width: 2, height: 46, background: "rgba(255,255,255,.65)" }} />}
      <div style={{ textShadow: "0 2px 10px rgba(0,0,0,.5)" }}>
        <div style={{ fontFamily: fam(m.tl, "num"), fontSize: 22, color: m.accent, letterSpacing: 4 }}>
          Q{num(t.no, 1)} / {pad(num(t.of, 1))}
        </div>
        <div style={{ fontFamily: fam(m.tl, "title"), fontSize: 30, color: "#fff" }}>{str(t.tag)}</div>
      </div>
    </div>
  );
};

// ── lower：人名条（取访谈示例片人物卡的字体层级：英文小标 / 毛笔名字 / 橙条 + 头衔），左下，避开字幕 ──
const Lower: React.FC<TP> = ({ m, t, f, dur }) => {
  const inK = lin(f, [0, 10], [0, 1], ez);
  const o = Math.min(lin(f, [2, 12], [0, 1]), lin(f, [dur - 8, dur], [1, 0]));
  const kicker = str(t.kicker);
  const title = str(t.title);
  const sub = str(t.sub);
  const right = t.side === "right";
  const shade = num(t.shade, 0.55);
  return (
    <>
    {shade > 0 && (
      // 角落压暗衬底：亮背景（沙滩、天空）上白字也读得清（同发布片参数卡的做法）
      <AbsoluteFill
        style={{
          opacity: o,
          background: `radial-gradient(ellipse 60% 62% at ${right ? "100%" : "0%"} 100%, rgba(0,0,0,${shade}) 0%, rgba(0,0,0,${(shade * 0.5).toFixed(3)}) 45%, rgba(0,0,0,0) 100%)`,
        }}
      />
    )}
    <div
      style={{
        position: "absolute",
        [right ? "right" : "left"]: num(t.x, 110),
        bottom: num(t.bottom, 200),
        opacity: o,
        transform: `translateX(${((1 - inK) * (right ? 40 : -40)).toFixed(2)}px)`,
        textAlign: right ? "right" : "left",
      }}
    >
      {kicker && <div style={{ fontFamily: fam(m.tl, "num"), fontSize: 30, color: m.accent, letterSpacing: 6, textShadow: "0 2px 8px rgba(0,0,0,.5)" }}>{kicker}</div>}
      <div style={{ fontFamily: fam(m.tl, t.font, "brush"), fontSize: num(t.size, 120), lineHeight: 1.05, color: "#fff", marginTop: kicker ? 6 : 0, textShadow: "0 8px 30px rgba(0,0,0,.45)" }}>
        {str(t.name)}
      </div>
      {title && (
        <div style={{ marginTop: 18, display: "flex", flexDirection: right ? "row-reverse" : "row", alignItems: "stretch", gap: 14 }}>
          <div style={{ width: 6, background: m.accent, flex: "none" }} />
          <div style={{ fontFamily: fam(m.tl, "title"), fontSize: 36, lineHeight: 1.25, color: "#fff", whiteSpace: "pre", textShadow: "0 3px 12px rgba(0,0,0,.5)" }}>{title}</div>
        </div>
      )}
      {sub && (
        <div style={{ marginTop: 14, fontFamily: fam(m.tl, "sans"), fontWeight: 600, fontSize: 26, letterSpacing: 4, color: "rgba(255,255,255,.9)", textShadow: "0 2px 10px rgba(0,0,0,.6)" }}>
          {sub}
        </div>
      )}
    </div>
    </>
  );
};

// ── 光带扫出（发布片 Reveal）：一条软的暖光带走过，走过的地方字亮出来，光带边缘让字形微微泛光 ──
const RevealBand: React.FC<{ f: number; at: number; dur: number; out?: number; children: React.ReactNode }> = ({ f, at, dur, out, children }) => {
  const t = lin(f, [at, at + dur], [0, 1], inOutCubic);
  const w = 45;
  const p = -w + t * (100 + 2 * w);
  const lit = band(100, p, w, 1, true);
  const sheen = band(100, p + 4, w * 0.35, 1);
  const o = out === undefined ? 1 : lin(f, [out, out + 6], [1, 0]);
  return (
    <div style={{ position: "relative", opacity: o }}>
      <div style={maskStyle(lit)}>{children}</div>
      {t > 0 && t < 1 && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            ...maskStyle(sheen),
            mixBlendMode: "screen",
            filter: "drop-shadow(0 0 6px rgba(255,196,120,.9)) drop-shadow(0 0 16px rgba(255,170,80,.5))",
          }}
        >
          {children}
        </div>
      )}
    </div>
  );
};

// ── spec：参数卡（发布片 C 版式：左上压暗 + 竖线长出 + 光带扫亮字；一张卡 4 拍时出场压到 16 帧内） ──
const Spec: React.FC<TP> = ({ m, t, f, dur }) => {
  const A = str(t.accent, m.accent);
  const fast = dur < 70;
  const out = dur - (fast ? 6 : 7);
  const T = fast ? { line: 10, idx: [0, 8], big: [2, 12], label: [6, 10], tag: [10, 16] } : { line: 14, idx: [1, 10], big: [3, 18], label: [10, 14], tag: [16, 24] };
  const big = str(t.big), unit = str(t.unit), label = str(t.label), sub = str(t.sub);
  const size = num(t.size, 210);
  const no = num(t.no, 1), of = num(t.of, 1);
  const showIdx = t.index === true || (t.index !== false && of > 1);
  const fadeOut = lin(f, [out, out + 6], [1, 0]);
  const cn = hasCJK(big);
  const Big = cn ? (
    <span style={{ fontFamily: fam(m.tl, "title"), fontSize: size * 0.78, lineHeight: 1, color: "#fff", letterSpacing: "0.04em", whiteSpace: "nowrap" }}>{big}</span>
  ) : (
    <span style={{ display: "inline-flex", alignItems: "baseline", gap: size * 0.06, whiteSpace: "nowrap" }}>
      <span style={{ fontFamily: fam(m.tl, "num"), fontSize: size, lineHeight: 0.8, color: "#fff" }}>{big}</span>
      {unit && (
        <span style={{ fontFamily: fam(m.tl, hasCJK(unit) ? "title" : "num"), fontSize: hasCJK(unit) ? size * 0.34 : size * 0.5, lineHeight: 1, color: A }}>{unit}</span>
      )}
    </span>
  );
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    <>
      <AbsoluteFill
        style={{
          opacity: lin(f, [0, 8], [0, 1]) * fadeOut,
          background: "radial-gradient(ellipse 62% 70% at 0% 0%, rgba(0,0,0,.66) 0%, rgba(0,0,0,.38) 45%, rgba(0,0,0,0) 100%)",
        }}
      />
      <div style={{ position: "absolute", left: num(t.x, 128), top: num(t.y, 120), height: 440, display: "flex", gap: 34 }}>
        <div
          style={{
            width: 2,
            background: `linear-gradient(${A}, ${A}00)`,
            transformOrigin: "top",
            transform: `scaleY(${lin(f, [0, T.line], [0, 1], outCubic).toFixed(4)})`,
            opacity: fadeOut,
          }}
        />
        <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", alignItems: "flex-start" }}>
          <RevealBand f={f} at={T.idx[0]} dur={T.idx[1]} out={out}>
            <span style={{ fontFamily: fam(m.tl, "sans"), fontWeight: 500, fontSize: 22, letterSpacing: "0.32em", color: A, whiteSpace: "nowrap", opacity: showIdx ? 1 : 0 }}>
              {pad(no)} <span style={{ opacity: 0.5 }}>/ {pad(of)}</span>
            </span>
          </RevealBand>
          <div style={{ height: 184, display: "flex", alignItems: "flex-end" }}>
            <RevealBand f={f} at={T.big[0]} dur={T.big[1]} out={out}>
              {Big}
            </RevealBand>
          </div>
          <RevealBand f={f} at={T.label[0]} dur={T.label[1]} out={out}>
            <span style={{ fontFamily: fam(m.tl, "sans"), fontWeight: 500, fontSize: 26, letterSpacing: "0.06em", color: "rgba(255,255,255,.86)", whiteSpace: "nowrap" }}>{label}</span>
          </RevealBand>
          <div style={{ opacity: (sub ? 1 : 0) * lin(f, T.tag as [number, number], [0, 1]) * fadeOut }}>
            <span
              style={{
                fontFamily: fam(m.tl, "sans"),
                fontWeight: 500,
                fontSize: 20,
                letterSpacing: "0.18em",
                color: "rgba(255,255,255,.6)",
                border: "1px solid rgba(255,255,255,.28)",
                padding: "5px 12px 5px 14px",
                whiteSpace: "nowrap",
              }}
            >
              {sub || "·"}
            </span>
          </div>
        </div>
      </div>
    </>
  );
};

// ── reveal：用光带扫出字标或 Logo（发布片 TitleCard / LogoEnd） ──
const Reveal: React.FC<TP> = ({ m, t, f, dur }) => {
  const image = str(t.image);
  const delay = num(t.delay, 4);
  const sweep = num(t.sweep, image ? 28 : 24);
  const out = dur - 6;
  const x = num(t.x, 960), y = num(t.y, 540);
  let body: React.ReactNode;
  if (image) {
    body = (
      <Img
        src={pub(image)}
        style={{ width: num(t.w, 560), height: typeof t.h === "number" ? t.h : undefined, display: "block", filter: t.invert ? "brightness(0) invert(1)" : undefined }}
      />
    );
  } else {
    const text = str(t.text);
    const size = num(t.size, 120);
    const hi = str(t.hi);
    const font = t.font ?? (hasCJK(text) ? "title" : "num");
    const parts = hi && text.includes(hi) ? text.split(hi) : [text];
    body = (
      <span
        style={{
          fontFamily: fam(m.tl, font),
          fontWeight: font === "sans" ? 600 : undefined,
          fontSize: size,
          lineHeight: 1.1,
          letterSpacing: typeof t.spacing === "string" ? t.spacing : size <= 48 ? "0.55em" : "0.08em",
          color: str(t.color, "rgba(255,255,255,.95)"),
          whiteSpace: "nowrap",
          display: "block",
        }}
      >
        {parts.map((p, i) => (
          <React.Fragment key={i}>
            {p}
            {i < parts.length - 1 && <span style={{ color: m.accent }}>{hi}</span>}
          </React.Fragment>
        ))}
      </span>
    );
  }
  return (
    <div style={{ position: "absolute", left: x, top: y, transform: "translate(-50%, -50%)" }}>
      <RevealBand f={f} at={delay} dur={sweep} out={out}>
        {body}
      </RevealBand>
    </div>
  );
};

// ── lockup：落版。字从雾里浮现（scale 0.78→1、blur 14→0），两行左右错开，Logo 在重击上出现（口号片 Lockup） ──
const Lockup: React.FC<TP> = ({ m, t, f }) => {
  const p = lin(f, [0, 60], [0, 1], outCubic);
  const logoAt = num(t.logo_at, 48);
  const logo = logoOf(m, t.logo);
  const lines = Array.isArray(t.lines) ? (t.lines as unknown[]).map((x) => str(x)) : [str(t.text)];
  const off = num(t.offset, 120);
  const size = num(t.size, 150);
  const s = 0.78 + 0.22 * p;
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column" }}>
      <div style={{ marginTop: logo ? -90 : 0, transform: `scale(${s.toFixed(4)})`, opacity: Math.min(1, p * 1.6), filter: p < 1 ? `blur(${((1 - p) * 14).toFixed(2)}px)` : undefined }}>
        {lines.map((ln, i) => (
          <div
            key={i}
            style={{
              fontFamily: fam(m.tl, t.font, "brush"),
              fontSize: size,
              lineHeight: 1.18,
              letterSpacing: "-0.14em",
              color: str(t.color, W_),
              textShadow: titleShadow,
              transform: lines.length === 2 ? `translateX(${i === 0 ? -off : off}px)` : undefined,
              whiteSpace: "nowrap",
            }}
          >
            {ln}
          </div>
        ))}
      </div>
      {logo && <Img src={pub(logo)} style={{ height: num(t.logo_h, 120), marginTop: 56, opacity: f >= logoAt ? 1 : 0 }} />}
    </AbsoluteFill>
  );
};

/** 落版成雾的进度（0–1），给 Film 用 */
export const fogAt = (m: Model, g: number) => {
  let v = 0;
  for (const z of m.fogs) if (g >= z.from && g < z.to) v = Math.max(v, lin(g, [z.from, z.from + 60], [0, 1], inOutCubic));
  return v;
};

// ── endcard：深色面板从右擦入 12 帧 → 官方 Logo → 尾部淡出（燃剪 EndCard）。
//    燃剪原版是整张卡淡出、露出下面的黑场；这里面板保持不透明、只淡出 Logo，下面还有画面时也是淡到深色 ──
type LogoSpec = { src: string; w?: number; h?: number; invert?: boolean };
const EndCard: React.FC<TP> = ({ m, t, f, dur }) => {
  const wipe = lin(f, [0, 12], [100, 0], outCubic);
  const logo = lin(f, [8, 22], [0, 1]);
  const out = lin(f, [dur - 18, dur], [1, 0]);
  const raw = Array.isArray(t.logos) ? (t.logos as unknown[]) : [];
  const logos: LogoSpec[] = raw
    .map((x) => (typeof x === "string" ? { src: x } : (x as LogoSpec)))
    .filter((x) => x && typeof x.src === "string");
  if (!logos.length) {
    const w = logoOf(m, true);
    if (w) logos.push({ src: w, h: 300 });
  }
  const sub = str(t.sub);
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{ background: str(t.bg, "#0b1014"), clipPath: `inset(0 0 0 ${wipe.toFixed(3)}%)` }} />
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", opacity: logo * out, transform: `scale(${(1.04 - 0.04 * logo).toFixed(4)})` }}>
        {logos.map((l, i) => (
          <Img
            key={i}
            src={pub(l.src)}
            style={{
              width: l.w ?? (l.h ? undefined : 300),
              height: l.h,
              marginTop: i ? 8 : 0,
              filter: l.invert ? "brightness(0) invert(1)" : undefined,
              opacity: i ? 0.85 : 1,
            }}
          />
        ))}
        {sub && <div style={{ marginTop: 28, fontFamily: fam(m.tl, "sans"), fontWeight: 600, fontSize: 40, letterSpacing: "0.3em", color: "rgba(255,255,255,.9)" }}>{sub}</div>}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// ── image：静态图（官方 Logo 等），淡入淡出。x / y 是图片中心 ──
const ImageText: React.FC<TP> = ({ t, f, dur }) => {
  const fi = num(t.fade, 8);
  const o = Math.min(lin(f, [0, fi], [0, 1]), lin(f, [dur - fi, dur], [1, 0])) * num(t.opacity, 1);
  return (
    <Img
      src={pub(str(t.src))}
      style={{
        position: "absolute",
        left: num(t.x, 960),
        top: num(t.y, 540),
        width: typeof t.w === "number" ? t.w : undefined,
        height: typeof t.h === "number" ? t.h : undefined,
        transform: "translate(-50%, -50%)",
        opacity: o,
        filter: t.invert ? "brightness(0) invert(1)" : undefined,
        maxWidth: "none",
      }}
    />
  );
};

const MAP: Record<string, React.FC<TP>> = {
  slam: Slam,
  brushword: BrushWord,
  chapter: Chapter,
  qcard: QCard,
  qtag: QTag,
  lower: Lower,
  spec: Spec,
  reveal: Reveal,
  lockup: Lockup,
  endcard: EndCard,
  image: ImageText,
};

export const TextView: React.FC<{ m: Model; t: Text; g: number }> = ({ m, t, g }) => {
  const C = MAP[t.type];
  if (!C) return null;
  return <C m={m} t={t} f={g - t.from} dur={t.to - t.from} />;
};

export const Captions: React.FC<{ m: Model; g: number }> = ({ m, g }) => {
  if (m.capHide.some(([a, b]) => g >= a && g < b)) return null;
  const c = m.captions.find((x) => g >= x.from && g < x.to);
  if (!c) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        bottom: m.capBottom,
        textAlign: "center",
        fontFamily: fam(m.tl, "sans"),
        fontWeight: 800,
        fontSize: m.capSize,
        color: "#fff",
        letterSpacing: 1,
        textShadow: "0 2px 3px rgba(0,0,0,.55), 0 4px 18px rgba(0,0,0,.55)",
        whiteSpace: "nowrap",
      }}
    >
      {c.text}
    </div>
  );
};

