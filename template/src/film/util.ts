import type { CSSProperties } from "react";
import { Easing, interpolate, staticFile } from "remotion";

export const CL = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

// 访谈示例片定稿用的两条缓动
export const ez = Easing.bezier(0.16, 1, 0.3, 1);
export const ezIn = Easing.bezier(0.7, 0, 0.84, 0);
export const inOutCubic = Easing.inOut(Easing.cubic);
export const outCubic = Easing.out(Easing.cubic);
export const inQuad = Easing.in(Easing.quad);

/** 夹住的线性插值；a == b 时按阶跃处理（interpolate 要求严格递增） */
export const lin = (x: number, [a, b]: [number, number], [c, d]: [number, number], easing?: (t: number) => number): number => {
  if (b <= a) return x < a ? c : d;
  return interpolate(x, [a, b], [c, d], easing ? { ...CL, easing } : CL);
};

export const clamp01 = (x: number) => Math.min(1, Math.max(0, x));

export const num = (v: unknown, d: number): number => (typeof v === "number" && Number.isFinite(v) ? v : d);
export const str = (v: unknown, d = ""): string => (typeof v === "string" ? v : d);

/** public/ 下的相对路径 → staticFile；已经是 http(s) / data: 地址就原样返回 */
export const pub = (p: string) => (/^(https?:|data:|blob:)/.test(p) ? p : staticFile(p.replace(/^\/+/, "").replace(/^public\//, "")));

// 近似高斯的光带遮罩（发布片 grade.tsx 原样）：p = 光带中心（%），w = 半宽（%），k = 峰值不透明度；stay = 光带走过的地方保持亮
export const band = (ang: number, p: number, w: number, k = 1, stay = false) => {
  const st: [number, number][] = [[-1, 0], [-0.72, 0.06], [-0.48, 0.24], [-0.24, 0.62], [0, 1], [0.24, 0.62], [0.48, 0.24], [0.72, 0.06], [1, 0]];
  const parts = st.map(([o, a]) => `rgba(0,0,0,${((stay && o <= 0 ? 1 : a) * k).toFixed(3)}) ${(p + o * w).toFixed(2)}%`);
  return `linear-gradient(${ang}deg, ${parts.join(", ")})`;
};

export const maskStyle = (m?: string): CSSProperties => (m ? { WebkitMaskImage: m, maskImage: m } : {});

export const hasCJK = (s: string) => /[㐀-鿿豈-﫿　-〿＀-￯]/.test(s);
