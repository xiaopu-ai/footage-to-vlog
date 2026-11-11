// 成片：整片第 g 帧的画面 = 当帧可见的镜头（按层、按切点叠）+ 落版成雾 + 文字包装 + 字幕；声音只在 Film 合成里。
// 画面部分不依赖 <Sequence>，只看 g，所以 Frame 合成（1 帧）可以直接渲任意一帧。
import React, { useMemo } from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { slamCamera } from "./camera";
import { FilmAudio } from "./FilmAudio";
import { useTimelineFonts } from "./fonts";
import { buildModel, type Model } from "./normalize";
import { AirLight, GradeDefs, ShotLayer } from "./Shot";
import { dipAt, sweepPos, SWEEP_W } from "./transitions";
import { Captions, fogAt, SlamShade, TextView } from "./texts";
import type { Timeline } from "./types";

/** sweep 转场的暖色空气光：两镜共用一道，画在镜头上面 */
const sweepAir = (m: Model, g: number) =>
  m.shots
    .filter((s) => s.inTr.type === "sweep" && g >= s.inWin[0] && g < s.inWin[1])
    .map((s) => ({ key: s.key, l: { p: sweepPos(s.inWin[0], s.inWin[1], g), w: SWEEP_W, ang: s.inTr.angle, stay: false, lift: 1 } }));

export const FilmView: React.FC<{ tl: Timeline; g: number; audio?: boolean }> = ({ tl, g, audio }) => {
  useTimelineFonts(tl);
  const m = useMemo(() => buildModel(tl), [tl]);
  const vis = m.shots.filter((s) => g >= s.vFrom && g < s.vTo).sort((a, b) => a.layer - b.layer || a.cut - b.cut);
  const cam = slamCamera(m, g);
  const fog = fogAt(m, g);
  const dip = dipAt(m.shots, g);
  const texts = m.texts.filter((t) => g >= t.from && g < t.to);
  return (
    <AbsoluteFill style={{ background: "#000", overflow: "hidden" }}>
      <GradeDefs />
      <AbsoluteFill style={cam ? { transform: `translate(${cam.x.toFixed(2)}px, ${cam.y.toFixed(2)}px) scale(${cam.s.toFixed(5)})` } : undefined}>
        <AbsoluteFill
          style={{
            ...(fog > 0 ? { filter: `blur(${(fog * 14).toFixed(2)}px)`, transform: `scale(${(1 + fog * 0.04).toFixed(5)})` } : {}),
            ...(dip < 1 ? { opacity: dip } : {}),
          }}
        >
          {vis.map((s) => (
            <ShotLayer key={s.key} m={m} s={s} g={g} />
          ))}
          {sweepAir(m, g).map((a) => (
            <AirLight key={`air-${a.key}`} l={a.l} />
          ))}
        </AbsoluteFill>
      </AbsoluteFill>
      {fog > 0 && <AbsoluteFill style={{ background: `rgba(12,18,24,${(0.45 * fog).toFixed(3)})` }} />}
      {m.slamGroups
        .filter((sg) => g >= sg.from && g < sg.to)
        .map((sg) => (
          <SlamShade key={`shade-${sg.from}`} from={sg.from} to={sg.to} g={g} />
        ))}
      {texts.map((t) => (
        <TextView key={t.id} m={m} t={t} g={g} />
      ))}
      <Captions m={m} g={g} />
      {audio && <FilmAudio m={m} />}
    </AbsoluteFill>
  );
};

export type FilmProps = { timeline?: Timeline };
export type FrameProps = { frame: number; timeline?: Timeline };

/** 整片合成：画面 + 声音 */
export const makeFilm = (fallback: Timeline): React.FC<FilmProps> => {
  const Film: React.FC<FilmProps> = ({ timeline }) => {
    const g = useCurrentFrame();
    return <FilmView tl={timeline ?? fallback} g={g} audio />;
  };
  return Film;
};

/** 单帧合成（1 帧、30 fps）：props.frame 指定显示整片第几帧，给风格帧、分镜用。不要用 <Still>（1 fps，startFrom 会被当成秒） */
export const makeFrame = (fallback: Timeline): React.FC<FrameProps> => {
  const Frame: React.FC<FrameProps> = ({ frame, timeline }) => <FilmView tl={timeline ?? fallback} g={Math.max(0, Math.round(frame || 0))} />;
  return Frame;
};
