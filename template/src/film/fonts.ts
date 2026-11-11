import { useEffect, useRef, useState } from "react";
import { continueRender, delayRender } from "remotion";
import type { FontSpec, Timeline } from "./types";
import { pub } from "./util";

// 字体按「角色」注册成 ftv-<角色>：timeline.fonts 里写了的用它的文件，没写的用模板自带的默认文件（public/fonts/LICENSES.md）。
// 🔴 delayRender 之后无论加载成功、失败还是超时都要 continueRender：句柄不清除，整片渲染会在 --timeout 到点时整个退出。

// 开源仓库只带 SIL OFL 授权、允许随项目再分发的字体（见 public/fonts/LICENSES.md）。
// 想换成别的字体（比如演示夏行楷、阿里巴巴普惠体），自己从官方渠道下载放进 public/fonts/，在 project.json 的 fonts 里按角色指定。
export const DEFAULT_FONTS: Record<string, FontSpec> = {
  brush: "fonts/MaShanZheng-Regular.ttf", // 马善政毛笔楷书：倒计时、章名、关键词、落版（清楚好认，访谈示例片的倒计时用过）
  kai: "fonts/MaShanZheng-Regular.ttf", // 同上，保留旧角色名
  sans: "fonts/NotoSansSC-VF.ttf", // 思源黑体可变字重：字幕、说明
  title: { src: "fonts/NotoSansSC-VF.ttf", weight: "900" }, // 思源黑体 Black：问题卡、人名条、参数大字（锁定 900 字重）
  num: "fonts/D-DINCondensed-Bold.ttf", // D-DIN Condensed：数字、英文小标
};

const FALLBACK = '"PingFang SC", "Hiragino Sans GB", "Noto Sans SC", "Microsoft YaHei", sans-serif';

/** 角色名 → CSS font-family；不是已知角色就当成 CSS 字体名原样用 */
export const fam = (tl: Timeline, role: unknown, dflt = "sans"): string => {
  const r = typeof role === "string" && role ? role : dflt;
  const known = r in DEFAULT_FONTS || (tl.fonts && r in tl.fonts);
  const main = known ? `"ftv-${r}"` : JSON.stringify(r);
  return r === "sans" ? `${main}, ${FALLBACK}` : `${main}, "ftv-sans", ${FALLBACK}`;
};

const specOf = (s: FontSpec): { src: string; weight?: string; style?: string } => (typeof s === "string" ? { src: s } : s);
const isVariable = (p: string) => /(VF|\[wght\]|Variable)/i.test(p);

const loaded = new Map<string, Promise<void>>();
const loadOne = (family: string, s: FontSpec): Promise<void> => {
  const { src, weight, style } = specOf(s);
  const key = `${family}|${src}`;
  const hit = loaded.get(key);
  if (hit) return hit;
  const desc: FontFaceDescriptors = {};
  if (weight) desc.weight = weight;
  else if (isVariable(src)) desc.weight = "100 900";
  if (style) desc.style = style;
  const p = new FontFace(family, `url(${pub(src)})`, desc)
    .load()
    .then((f) => {
      document.fonts.add(f);
    })
    .catch((e) => {
      console.warn(`[ftv] 字体加载失败 ${family} ← ${src}:`, e);
    });
  loaded.set(key, p);
  return p;
};

export const fontTable = (tl: Timeline): Record<string, FontSpec> => ({ ...DEFAULT_FONTS, ...(tl.fonts ?? {}) });

/** 加载 timeline 用到的全部字体角色；成功、失败、20 秒超时都会放行渲染 */
export const useTimelineFonts = (tl: Timeline) => {
  const [handle] = useState(() => delayRender("ftv 字体", { timeoutInMilliseconds: 60000 }));
  const cleared = useRef(false);
  useEffect(() => {
    const finish = () => {
      if (cleared.current) return;
      cleared.current = true;
      continueRender(handle);
    };
    const table = fontTable(tl);
    const all = Promise.all(Object.entries(table).map(([role, s]) => loadOne(`ftv-${role}`, s)));
    const timer = setTimeout(() => {
      console.warn("[ftv] 字体 20 秒没加载完，先放行渲染");
      finish();
    }, 20000);
    all.finally(() => {
      clearTimeout(timer);
      finish();
    });
    return () => {
      clearTimeout(timer);
      finish();
    };
  }, [handle, tl]);
};
