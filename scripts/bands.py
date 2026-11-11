#!/usr/bin/env python3
"""音乐结构体检:每个时间步的分频段能量(dB)+ 高频起音密度。看贝斯 / 鼓什么时候进出、哪里蓄力(高频一路抬)、
哪里砸下、哪里断音、哪里淡出。生成的配乐常常不按作曲计划走,按小节重剪(bar_edit.py)之前先跑这个。

用法:
  python3 bands.py <音乐> [步长秒,默认 1.0] [--from S] [--to S] [--beats <proj>/beats.json]
  --beats 给了拍网格时,改成按小节一行(步长 = 一小节),行首标「小节号」,方便直接对 edit.json 的 {"bar": n}。

输出列:sub<90 / low90-250 / mid250-2k / hi2k-6k / air>6k 五个频段的平均能量(dB,只看相对变化),
onsets/s = 2 kHz 以上频谱通量的峰值个数 ÷ 秒(鼓组越密越高;0 附近 = 没鼓)。
依赖:numpy;ffmpeg(PATH 里,或环境变量 FFMPEG)。只解码音频,是轻任务。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

import numpy as np

SR = 22050
BANDS = {"sub<90": (20, 90), "low90-250": (90, 250), "mid250-2k": (250, 2000), "hi2k-6k": (2000, 6000), "air>6k": (6000, 11000)}


def main():
    ap = argparse.ArgumentParser(description="音乐分频段能量体检")
    ap.add_argument("audio")
    ap.add_argument("step", nargs="?", type=float, default=1.0)
    ap.add_argument("--from", dest="a", type=float, default=0.0)
    ap.add_argument("--to", dest="b", type=float, default=None)
    ap.add_argument("--beats", default=None, help="beats.json:按小节一行")
    o = ap.parse_args()
    ff = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not ff:
        sys.exit("找不到 ffmpeg:装好放进 PATH,或设环境变量 FFMPEG")
    raw = subprocess.run([ff, "-v", "error", "-i", o.audio, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32)
    n, hop = 1024, 256
    nf = 1 + (len(x) - n) // hop
    win = np.hanning(n).astype(np.float32)
    f = np.fft.rfftfreq(n, 1 / SR)
    E = {k: np.empty(nf) for k in BANDS}
    hi_sum = np.empty(nf)
    for s in range(0, nf, 4096):
        e = min(nf, s + 4096)
        idx = np.arange(n)[None, :] + hop * np.arange(s, e)[:, None]
        S = np.abs(np.fft.rfft(x[idx] * win, axis=1))
        for k, (a, b) in BANDS.items():
            E[k][s:e] = 10 * np.log10((S[:, (f >= a) & (f < b)] ** 2).sum(1) + 1e-12)
        hi_sum[s:e] = S[:, f >= 2000].sum(1)
    flux = np.maximum(0, np.diff(np.log(hi_sum + 1e-9), prepend=0))
    t = np.arange(nf) * hop / SR
    end = o.b if o.b is not None else t[-1]
    thr = np.median(flux) * 4

    rows = []
    if o.beats:
        bj = json.load(open(o.beats, encoding="utf-8"))
        bar = bj["beat_s"] * bj.get("beats_per_bar", 4)
        off = bj["offset_s"] + bj.get("trim_s", 0)  # 原曲秒
        k0 = int(np.floor((o.a - off) / bar))
        k = k0
        while off + k * bar < end:
            a = off + k * bar
            if min(a + bar, end) - max(a, o.a) >= 0.25 * bar:
                rows.append((f"m{k:<3d}{max(a, 0):6.1f}", max(a, 0), a + bar))
            k += 1
    else:
        for a in np.arange(o.a, end, o.step):
            rows.append((f"{a:6.1f}", a, a + o.step))
    print("   t    " + "  ".join(f"{k:>9}" for k in BANDS) + "   onsets/s")
    for label, a, b in rows:
        m = (t >= a) & (t < b)
        if not m.any():
            continue
        fl = flux[m]
        ons = int(((fl[1:-1] > fl[:-2]) & (fl[1:-1] >= fl[2:]) & (fl[1:-1] > thr)).sum()) if len(fl) > 2 else 0
        print(f"{label:>9} " + "  ".join(f"{E[k][m].mean():9.1f}" for k in BANDS) + f"   {ons / (b - a):5.1f}")


if __name__ == "__main__":
    main()
