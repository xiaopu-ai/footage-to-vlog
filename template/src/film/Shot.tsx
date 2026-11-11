// 一个镜头的画面：片段逐帧取画（Freeze + OffthreadVideo）→ 运镜 → 转场（透明度 / 蒙版 / 位移 / 拖影 / 提亮）→ 天空压暗、暗角。
import React from "react";
import { AbsoluteFill, Freeze, OffthreadVideo } from "remotion";
import { camCss, shotCam } from "./camera";
import type { Model, NShot } from "./normalize";
import { shotFx, type LightPass, type Trail } from "./transitions";
import { band, inOutCubic, lin, maskStyle, num, pub } from "./util";

// SVG 滤镜：color-interpolation-filters 必须是 sRGB，否则 Chrome 按线性光算、整体发灰
export const GradeDefs: React.FC = () => (
  <svg width={0} height={0} style={{ position: "absolute" }}>
    <defs>
      {/* 阳光色温：红略升、蓝压 12% */}
      <filter id="ftv-warm" colorInterpolationFilters="sRGB">
        <feColorMatrix type="matrix" values="1.05 0 0 0 0.01  0 1 0 0 0  0 0 0.88 0 0  0 0 0 1 0" />
      </filter>
      {/* 高光：按亮度只留最亮的约 30%（字标、漆面反光、金属边），偏暖，加一圈小泛光（模糊 7） */}
      <filter id="ftv-glint" colorInterpolationFilters="sRGB" x="-5%" y="-5%" width="110%" height="110%">
        <feColorMatrix type="matrix" values="0.3 0.59 0.11 0 0  0.3 0.59 0.11 0 0  0.3 0.59 0.11 0 0  0 0 0 1 0" result="lum" />
        <feComponentTransfer in="lum" result="hi">
          <feFuncR type="table" tableValues="0 0 0 0 0 0 0.08 0.35 0.8 1" />
          <feFuncG type="table" tableValues="0 0 0 0 0 0 0.08 0.35 0.8 1" />
          <feFuncB type="table" tableValues="0 0 0 0 0 0 0.08 0.35 0.8 1" />
        </feComponentTransfer>
        <feColorMatrix in="hi" type="matrix" values="1 0 0 0 0  0 0.9 0 0 0  0 0 0.72 0 0  0 0 0 1 0" result="warm" />
        <feGaussianBlur in="warm" stdDeviation="7" result="bloom" />
        <feMerge>
          <feMergeNode in="bloom" />
          <feMergeNode in="warm" />
        </feMerge>
      </filter>
    </defs>
  </svg>
);

/** 片段的第 frame 帧（片段按 30 fps 播放、保留全部原生帧；变速和定格已经算进 clip_frames）。
 *  🔴 写成 <Freeze frame={0}> + trimBefore={frame}（燃剪 ShotView 的写法），不要写 <Freeze frame={frame}>：
 *  Remotion 会把冻结的帧号夹到「合成时长 − 1」以内，片段帧号一旦超过整片帧数（短片里的高帧率素材很常见）就会卡住；
 *  1 帧的 Frame 合成里更是永远取到第 0 帧。trimBefore 走的是 Sequence 偏移，不受这个限制。 */
const Clip: React.FC<{ src: string; frame: number; filter?: string }> = ({ src, frame, filter }) => (
  <Freeze frame={0}>
    <OffthreadVideo
      src={src}
      trimBefore={frame}
      muted
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", maxWidth: "none", filter }}
    />
  </Freeze>
);

// 光扫三层（发布片 Plate）：宽而软的补光带（更亮、偏暖）+ 窄一些的高光带（只取最亮约 30%，screen 0.95）。不画任何线。
const LitLayers: React.FC<{ src: string; frame: number; l: LightPass; passing: boolean }> = ({ src, frame, l, passing }) => {
  const lit = `url(#ftv-warm) brightness(${l.lift.toFixed(3)})`;
  const relight = band(l.ang, l.p, l.w, 1, l.stay);
  const glint = band(l.ang, l.p + l.w * 0.08, l.w * 0.42, 1);
  return (
    <>
      <AbsoluteFill style={maskStyle(relight)}>
        <Clip src={src} frame={frame} filter={lit} />
      </AbsoluteFill>
      {passing && (
        <AbsoluteFill style={{ ...maskStyle(glint), mixBlendMode: "screen", opacity: 0.95 }}>
          <Clip src={src} frame={frame} filter={`${lit} url(#ftv-glint)`} />
        </AbsoluteFill>
      )}
    </>
  );
};

/** 7% 暖色空气光：让光有体积感，而不是贴在表面 */
export const AirLight: React.FC<{ l: LightPass }> = ({ l }) => {
  const m = band(l.ang, l.p, l.w * 1.2, 1);
  return <AbsoluteFill style={{ ...maskStyle(m), background: "rgba(255,214,160,.07)", mixBlendMode: "screen" }} />;
};

/** 镜内光扫（shot.light，模板扩展字段）：镜内帧 from → to 一道光走过，stay = 走过后保持亮 */
export const shotLight = (s: NShot, g: number): { l: LightPass; passing: boolean } | null => {
  const L = s.light;
  if (!L || typeof L !== "object") return null;
  const a = s.cut + num(L.from, 0), b = s.cut + num(L.to, 30);
  if (g < a && !L.stay) return null;
  const w = num(L.width, 0.45) * 100;
  const t = lin(g, [a, b], [0, 1], inOutCubic);
  if (t >= 1 && !L.stay) return null;
  return { l: { p: -w + t * (100 + 2 * w), w, ang: num(L.angle, 90), stay: !!L.stay, lift: num(L.lift, 1.25) }, passing: g >= a && g <= b + 2 };
};

// 拖影：同一画面叠 n 份、等权平均（第 k 层不透明度 1/(k+1)）。不用 blur 滤镜放大画面。
const TrailStack: React.FC<{ trail?: Trail; children: React.ReactNode }> = ({ trail, children }) => {
  const tiny = !trail || (trail.kind === "x" ? Math.abs(trail.span) < 1 : trail.amount < 0.002);
  if (tiny) return <>{children}</>;
  const n = Math.max(2, trail.n);
  return (
    <>
      {Array.from({ length: n }, (_, k) => {
        const transform =
          trail.kind === "x" ? `translateX(${(trail.span * (k / (n - 1) - 0.5)).toFixed(2)}px)` : `scale(${(1 + k * trail.amount).toFixed(5)})`;
        return (
          <AbsoluteFill key={k} style={{ transform, opacity: 1 / (k + 1) }}>
            {children}
          </AbsoluteFill>
        );
      })}
    </>
  );
};

export const ShotLayer: React.FC<{ m: Model; s: NShot; g: number }> = ({ m, s, g }) => {
  const fx = shotFx(s, g);
  // 黑场镜头（src / clip 为 null）：纯黑，照样参与转场
  if (s.black || !s.clip) return <AbsoluteFill style={{ background: "#000", opacity: fx.opacity, clipPath: fx.clip, ...maskStyle(fx.mask) }} />;
  const cam = shotCam(m, s, g);
  const frames = s.clip_frames ?? [];
  const k = Math.max(0, Math.min(frames.length - 1, g - s.from));
  const frame = frames.length ? Math.max(0, Math.round(frames[k])) : Math.max(0, g - s.from);
  const src = pub(s.clip);
  const own = shotLight(s, g);
  const light = fx.light ? { l: fx.light, passing: true } : own;

  const outer: React.CSSProperties = {};
  const tf: string[] = [];
  if (fx.tx) tf.push(`translateX(${fx.tx.toFixed(2)}px)`);
  if (fx.scale !== 1) tf.push(`scale(${fx.scale.toFixed(5)})`);
  if (tf.length) outer.transform = tf.join(" ");
  const filters: string[] = [];
  if (fx.bright !== 1) filters.push(`brightness(${fx.bright.toFixed(3)})`);
  if (fx.blur > 0.3) filters.push(`blur(${fx.blur.toFixed(2)}px)`);
  if (filters.length) outer.filter = filters.join(" ");

  return (
    <AbsoluteFill style={{ opacity: fx.opacity, clipPath: fx.clip, ...maskStyle(fx.mask) }}>
      <AbsoluteFill style={{ overflow: "hidden", background: "#000" }}>
        <AbsoluteFill style={outer}>
          <TrailStack trail={fx.trail}>
            <AbsoluteFill style={camCss(cam)}>
              <Clip src={src} frame={frame} />
              {light && <LitLayers src={src} frame={frame} l={light.l} passing={light.passing} />}
            </AbsoluteFill>
          </TrailStack>
        </AbsoluteFill>
        {own && own.passing && !fx.light && <AirLight l={own.l} />}
        {s.sky > 0 && (
          // 天空压暗渐变（燃剪 grade.tsx）：顶部最暗，到 45% 高度消失；正俯拍别加
          <AbsoluteFill
            style={{
              background: `linear-gradient(180deg, rgba(8,14,20,${s.sky}) 0%, rgba(8,14,20,${(s.sky * 0.45).toFixed(3)}) 22%, rgba(8,14,20,0) 45%)`,
            }}
          />
        )}
        {s.vig > 0 && (
          <AbsoluteFill style={{ background: `radial-gradient(ellipse 75% 70% at 50% 50%, rgba(0,0,0,0) 55%, rgba(0,0,0,${s.vig}) 100%)` }} />
        )}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
