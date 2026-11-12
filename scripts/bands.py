#!/usr/bin/env python3
"""音乐结构体检:每个时间步(或每小节)的分频段能量(dB)+ 起音密度 + 这一段里最强的重音。看贝斯 / 鼓什么时候进出、
哪里蓄力(高频一路抬)、哪里砸下、哪里断音、哪里淡出;再看**每小节的刀该落在哪一下**。
生成的配乐常常不按作曲计划走,按小节重剪(bar_edit.py)之前先跑这个;剪完的配乐再跑一次。

用法:
  python3 bands.py <音乐> [步长秒,默认 1.0] [--from S] [--to S] [--beats <proj>/beats.json]
  --beats 给了拍网格时,改成按小节一行(步长 = 一小节),行首标「小节号」,方便直接对 edit.json 的 {"bar": n}。

输出列:
  sub<90 / low90-250 / mid250-2k / hi2k-6k / air>6k   五个频段的平均能量(dB,只看相对变化)
  onsets/s   2 kHz 以上的起音个数 ÷ 秒(鼓组越密越高;0 = 没鼓或只有铺底)。和 tempo.py 写进 beats.json 的
             onset_density 同一套算法:只数这一频段能量在全曲高位 30 dB 以内的时段,静音 / 铺底段的底噪不算起音。
  最强重音   这一行里强度百分位 s 最高的起音:有 --beats 时写它落在本小节第几拍(「3+」= 第 3 拍后半拍),离 8 分音符
             超过 30 ms 时附偏差;没有 --beats 时写它在这一行里的秒数。再跟 kind(kick / snare / hit)和 s。
             s ≥ 0.7 是强重音(timeline.py 的命中率和 "snap": "accent" 用它)。算法见 tempo.py 文件头。
  末尾汇总:全曲起音密度(有声段平均、1 秒窗最多)、强重音每秒个数。平均 > 15 次 / 秒时,鼓组很密,
  按 4 分音符和强重音切,8 分音符留给动作内部。

产物:只打印,不写文件。
依赖:numpy;ffmpeg(PATH 里,或环境变量 FFMPEG)。只解码音频,是轻任务。起音 / 重音函数 import 自同目录的 tempo.py。
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tempo as TP  # noqa: E402

SR = TP.SR
BANDS = {"sub<90": (20, 90), "low90-250": (90, 250), "mid250-2k": (250, 2000), "hi2k-6k": (2000, 6000), "air>6k": (6000, 11000)}


def main():
    ap = argparse.ArgumentParser(description="音乐分频段能量 + 起音密度 + 每段最强重音")
    ap.add_argument("audio")
    ap.add_argument("step", nargs="?", type=float, default=1.0)
    ap.add_argument("--from", dest="a", type=float, default=0.0)
    ap.add_argument("--to", dest="b", type=float, default=None)
    ap.add_argument("--beats", default=None, help="beats.json:按小节一行")
    o = ap.parse_args()
    x = TP.load(o.audio)
    n, hop = 1024, 256
    nf = 1 + (len(x) - n) // hop
    if nf < 2:
        sys.exit("音频太短")
    win = np.hanning(n).astype(np.float32)
    f = np.fft.rfftfreq(n, 1 / SR)
    E = {k: np.empty(nf) for k in BANDS}
    for s in range(0, nf, 4096):
        e = min(nf, s + 4096)
        idx = np.arange(n)[None, :] + hop * np.arange(s, e)[:, None]
        S = np.abs(np.fft.rfft(x[idx] * win, axis=1))
        for k, (a, b) in BANDS.items():
            E[k][s:e] = 10 * np.log10((S[:, (f >= a) & (f < b)] ** 2).sum(1) + 1e-12)
    t = np.arange(nf) * hop / SR
    end = o.b if o.b is not None else t[-1]

    on, _, _ = TP.onset_times(x)
    Ea, _, _ = TP.analyze(x)
    cands, sv = TP.ranked_accents(Ea)
    ct = np.array([c["t"] for c in cands]) if cands else np.array([])

    bj = None
    rows = []
    if o.beats:
        bj = json.load(open(o.beats, encoding="utf-8"))
        P = bj["beat_s"]
        bpb = int(bj.get("beats_per_bar", 4))
        bar = P * bpb
        off = bj["offset_s"] + bj.get("trim_s", 0)  # 原曲秒
        k0 = int(np.floor((o.a - off) / bar))
        k = k0
        while off + k * bar < end:
            a = off + k * bar
            if min(a + bar, end) - max(a, o.a) >= 0.25 * bar:
                rows.append((f"m{k:<3d}{max(a, 0):6.1f}", max(a, 0), a + bar, a))
            k += 1
    else:
        for a in np.arange(o.a, end, o.step):
            rows.append((f"{a:6.1f}", a, a + o.step, a))

    def acc_cell(a, b, bar_start):
        if not len(ct):
            return "—"
        if bj:  # 按最近的 8 分音符归小节:小节头前几十毫秒的起音算下一小节的第 1 拍
            q = bj["beat_s"] / 4
            a, b = a - q, b - q
        m = np.where((ct >= a) & (ct < b))[0]
        if not len(m):
            return "—"
        j = m[np.argmax(sv[m])]
        c, s_ = cands[j], sv[j]
        if bj:
            half = bj["beat_s"] / 2
            e8 = int(round((c["t"] - bar_start) / half))
            dev = (c["t"] - (bar_start + e8 * half)) * 1000
            pos = f"{e8 // 2 + 1}" + ("+" if e8 % 2 else "") + (f"({dev:+.0f}ms)" if abs(dev) > 30 else "")
        else:
            pos = f"+{c['t'] - a:.2f}s"
        return f"{pos} {c['kind']} {s_:.2f}"

    print("   t    " + "  ".join(f"{k:>9}" for k in BANDS) + "   onsets/s   最强重音")
    for label, a, b, bar_start in rows:
        m = (t >= a) & (t < b)
        if not m.any():
            continue
        cnt = int(((on >= a) & (on < b)).sum())
        print(f"{label:>9} " + "  ".join(f"{E[k][m].mean():9.1f}" for k in BANDS)
              + f"   {cnt / (b - a):6.1f}   {acc_cell(a, b, bar_start)}")
    dens = TP.onset_density(x)
    dur = len(x) / SR
    strong = float((sv >= TP.STRONG_S).sum()) / dur if len(sv) else 0.0
    print(f"全曲起音密度:有声段平均 {dens['mean']}/s(有声段 {dens['active_s']} s),1 秒窗最多 {dens['max']};"
          f"强重音(s ≥ {TP.STRONG_S}){strong:.2f}/s")
    if dens["mean"] > 15:
        print("  ⚠️ 平均 > 15 次 / 秒:鼓组很密,8 分音符网格上处处有起音。按 4 分音符和强重音切,8 分音符留给动作内部的水花峰值。")


if __name__ == "__main__":
    main()
