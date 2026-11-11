// 声音三层：配乐（gain_keys 插值 × 人声闪避）+ 音效 + 片段原声（voice，带淡入淡出）。
// 只在 Film 合成里出现；Frame（静帧）不放声音。
import React from "react";
import { Audio, Sequence } from "remotion";
import type { Model } from "./normalize";
import type { Music, Voice } from "./types";
import { lin, num, pub } from "./util";

/** 配乐在整片第 g 帧的音量：gain_keys 线性插值；duck_ranges 里压低 duck_db（前 attack 帧压下去、后 release 帧回来，按 dB 线性过渡） */
export const musicGain = (mu: Music, g: number): number => {
  const keys = (mu.gain_keys ?? []).filter((k) => Array.isArray(k) && k.length >= 2).sort((a, b) => a[0] - b[0]);
  let gain = num(mu.gain, 1);
  if (keys.length) {
    if (g <= keys[0][0]) gain = keys[0][1];
    else if (g >= keys[keys.length - 1][0]) gain = keys[keys.length - 1][1];
    else
      for (let i = 0; i + 1 < keys.length; i++) {
        const [a, va] = keys[i], [b, vb] = keys[i + 1];
        if (g >= a && g <= b) {
          gain = b > a ? va + ((vb - va) * (g - a)) / (b - a) : vb;
          break;
        }
      }
  }
  const db = num(mu.duck_db, -13);
  const at = Math.max(0, num(mu.attack, 6)), rl = Math.max(0, num(mu.release, 8));
  let d = 0;
  for (const r of mu.duck_ranges ?? []) {
    if (!Array.isArray(r) || r.length < 2) continue;
    const [a, b] = r;
    if (g < a - at || g > b + rl) continue;
    const k = g < a ? lin(g, [a - at, a], [0, 1]) : g > b ? lin(g, [b, b + rl], [1, 0]) : 1;
    d = Math.max(d, k);
  }
  return Math.max(0, gain * Math.pow(10, (db * d) / 20));
};

const voiceEnv = (v: Voice, f: number, dur: number) => {
  const fi = num(v.fade_in, 0), fo = num(v.fade_out, 0);
  const a = fi > 0 ? lin(f, [0, fi], [0, 1]) : 1;
  const b = fo > 0 ? lin(f, [dur - fo, dur], [1, 0]) : 1;
  return Math.min(a, b);
};

export const FilmAudio: React.FC<{ m: Model }> = ({ m }) => {
  const au = m.tl.audio ?? {};
  const mu = au.music;
  const vg = num(au.voice_gain, 1);
  const total = m.duration;
  return (
    <>
      {mu && mu.src && (
        <Sequence from={Math.max(0, Math.round(num(mu.from, 0)))} durationInFrames={Math.max(1, total - Math.max(0, Math.round(num(mu.from, 0))))} name="配乐" layout="none">
          <Audio
            src={pub(mu.src)}
            trimBefore={Math.max(0, Math.round(num(mu.trim_frames, 0))) || undefined}
            volume={(f) => musicGain(mu, f + Math.max(0, Math.round(num(mu.from, 0))))}
          />
        </Sequence>
      )}
      {(au.sfx ?? []).map((s, i) =>
        s && s.src ? (
          <Sequence key={`sfx${i}`} from={Math.round(s.from)} durationInFrames={s.dur && s.dur > 0 ? Math.round(s.dur) : undefined} name={`音效 ${s.id ?? i}`} layout="none">
            <Audio src={pub(s.src)} volume={num(s.gain, 1)} toneFrequency={typeof s.tone === "number" ? s.tone : undefined} />
          </Sequence>
        ) : null,
      )}
      {(au.voice ?? []).map((v, i) => {
        const dur = Math.round(v.to - v.from);
        if (!v.clip || dur <= 0) return null;
        return (
          <Sequence key={`v${i}`} from={Math.round(v.from)} durationInFrames={dur} name={`原声 ${v.shot ?? i}`} layout="none">
            <Audio src={pub(v.clip)} trimBefore={Math.max(0, Math.round(num(v.clip_start_frame, 0))) || undefined} volume={(f) => num(v.gain, 1) * vg * voiceEnv(v, f, dur)} />
          </Sequence>
        );
      })}
    </>
  );
};
