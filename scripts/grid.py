#!/usr/bin/env python3
"""测音乐的拍网格:BPM、拍点相位、小节头(无鼓的 pluck / pad 也能测)。

用法:
  python3 scripts/grid.py music.mp3 [--from 16 --to 40] [--trim 0.8553] [--fps 30]

和 beat.py 的区别:
- beat.py 只看 200 Hz 以下的鼓和贝斯,没有底鼓的曲子会测错(实测把 120 BPM 测成 150 / 164)。
- 这里用全频带谱通量做梳状扫描测拍长和相位;再把低 / 中 / 高三个频段的起音按「一小节 16 格」折叠,低频最强的那一格就是小节头。
- 给了 --trim(片子第 0 帧对应原曲第几秒),顺便换算成片子帧号:拍点 = 第几帧 + n × 每拍帧数。
依赖 numpy。mp3 解码会带约 22 ms 编码延迟,测出来的相位里已经包含。
"""
import argparse
import os
import subprocess

import numpy as np

FF = (os.environ.get("FFMPEG") or __import__("shutil").which("ffmpeg") or "ffmpeg")
SR = 22050


def load(p):
    raw = subprocess.run([FF, "-v", "error", "-i", p, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def spec(x, n=1024, hop=110):
    w = np.hanning(n).astype(np.float32)
    nf = 1 + (len(x) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(nf)[:, None]
    return np.abs(np.fft.rfft(x[idx] * w, axis=1)), np.fft.rfftfreq(n, 1 / SR), hop / SR


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--from", dest="a", type=float, default=1.0)
    ap.add_argument("--to", dest="b", type=float, default=None)
    ap.add_argument("--trim", type=float, default=None, help="片子第 0 帧对应原曲的秒数")
    ap.add_argument("--fps", type=float, default=30)
    o = ap.parse_args()
    x = load(o.audio)
    dur = len(x) / SR
    b = o.b or dur - 1
    S, fr, dt = spec(x)
    logS = np.log1p(100 * S)
    flux = np.concatenate([[0], np.maximum(np.diff(logS, axis=0), 0).sum(1)])
    flux -= np.convolve(flux, np.ones(100) / 100, "same")
    flux = np.maximum(flux, 0)
    seg = flux[int(o.a / dt) : int(b / dt)]
    seg = seg - seg.mean()
    # 梳状扫描:拍长 0.3–1.2 s,每个拍长扫 60 个相位,取梳齿上起音均值最大的
    best = []
    for per in np.arange(0.30, 1.20, 0.0025):
        sc = []
        for ph in np.linspace(0, per, 60, endpoint=False):
            ii = (np.arange(ph, b - o.a, per) / dt).astype(int)
            sc.append(seg[ii[ii < len(seg)]].mean())
        j = int(np.argmax(sc))
        best.append((sc[j], per))
    best.sort(reverse=True)
    top = best[0][1]
    # 倍频歧义:0.5 倍、2 倍拍长的得分接近时,取落在 80–160 BPM 的那个
    cands = [p for s, p in best[:8] if 60 / p >= 80 and 60 / p <= 160]
    per = cands[0] if cands else top
    bar = per * 4
    print(f"时长 {dur:.2f}s  测量区间 {o.a:.1f}–{b:.1f}s")
    print(f"拍长 {per:.4f}s = {60 / per:.2f} BPM")
    print("候选:" + "  ".join(f"{60 / p:.1f}BPM({s:.1f})" for s, p in best[:5]))
    # 小节头 + 拍点相位用低频(贝斯 / 底鼓)定:全频带里最强的常是反拍上的踩镲、拨弦,会把相位带偏半拍
    def onsets(lo, hi):
        e = np.log1p(1000 * S[:, (fr >= lo) & (fr < hi)].sum(1))
        on = np.concatenate([[0], np.maximum(np.diff(e), 0)])[int(o.a / dt) : int(b / dt)]
        return on, o.a + np.arange(len(on)) * dt
    low, t = onsets(20, 150)
    offs = np.arange(0, bar, 0.001)
    tol = int(0.012 / dt) + 1
    sm = np.convolve(low, np.ones(tol), "same")
    score = [sm[((np.arange(off, b, bar) - o.a) / dt).astype(int).clip(0, len(sm) - 1)][np.arange(off, b, bar) >= o.a].mean() for off in offs]
    down = offs[int(np.argmax(score))]
    phase = down % per
    print(f"拍点 = {phase:.3f} + n × {per:.4f} s;小节头 = {down:.3f} + n × {bar:.3f} s")
    for name, lo, hi in (("低<150Hz", 20, 150), ("中150–2k", 150, 2000), ("高>5k", 5000, 11000)):
        on, tt = onsets(lo, hi)
        h = np.bincount((((tt - down) % bar) / (bar / 16)).astype(int) % 16, weights=on, minlength=16)
        print(f"  {name:9s}(从小节头起 16 格)" + " ".join(f"{v / h.max():.2f}" for v in h))
    down_t = down
    if o.trim is not None:
        f_beat = (phase - o.trim) * o.fps
        f_bar = (down_t - o.trim) * o.fps
        spb = per * o.fps
        print(f"片子帧号(--trim {o.trim}):拍点 = {f_beat % spb:.2f} + n × {spb:.2f};小节头 = {f_bar % (spb * 4):.2f} + n × {spb * 4:.2f}")


if __name__ == "__main__":
    main()
