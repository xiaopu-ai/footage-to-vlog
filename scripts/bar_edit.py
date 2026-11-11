#!/usr/bin/env python3
"""按小节重剪音乐:把源音乐的若干区间按顺序拼起来(每段 10ms 淡入淡出防爆音),
可给单段加淡入 / 淡出 / 低通 / 增益,用来把炸点、回落、收尾重拍对齐画面关键帧。

用法:
  python3 scripts/bar_edit.py src.mp3 out.wav edl.json

edl.json 示例(TO-DO Panel 实用过的剪法;区间都取在拍点上,beat.py 测得):
[
  {"from": 8.7733, "to": 16.04, "fade_in": 0.6},          前奏 + 蓄力,炸点落在拼接后的 7.267s
  {"from": 16.04, "to": 40.04},                            主段
  {"from": 32.04, "to": 40.04, "lowpass": 650, "gain_db": -2},  回落:主段过两遍低通自造
  {"from": 12.04, "to": 16.04},                            尾段蓄力(复用上扬段)
  {"from": 16.04, "to": 17.7733, "fade_out": 1.38}        收尾重拍 + 余音
]
"""
import json
import os
import subprocess
import sys

FF = (os.environ.get("FFMPEG") or __import__("shutil").which("ffmpeg") or "ffmpeg")


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    src, out, edl_path = sys.argv[1:]
    edl = json.load(open(edl_path, encoding="utf-8"))
    parts, labels, total = [], [], 0.0
    for i, s in enumerate(edl):
        a, b = float(s["from"]), float(s["to"])
        d = b - a
        chain = [f"atrim=start={a}:end={b}", "asetpts=PTS-STARTPTS", "afade=t=in:d=0.01", f"afade=t=out:st={d - 0.01:.4f}:d=0.01"]
        if s.get("lowpass"):
            chain += [f"lowpass=f={s['lowpass']}"] * 2
        if s.get("gain_db"):
            chain.append(f"volume={s['gain_db']}dB")
        if s.get("fade_in"):
            chain.append(f"afade=t=in:d={s['fade_in']}")
        if s.get("fade_out"):
            chain.append(f"afade=t=out:st={d - s['fade_out']:.4f}:d={s['fade_out']}")
        parts.append(f"[0:a]{','.join(chain)}[s{i}]")
        labels.append(f"[s{i}]")
        print(f"段 {i}: 源 {a:.3f}–{b:.3f}s → 成品 {total:.3f}–{total + d:.3f}s")
        total += d
    graph = ";".join(parts) + f";{''.join(labels)}concat=n={len(edl)}:v=0:a=1[out]"
    subprocess.run([FF, "-v", "error", "-y", "-i", src, "-filter_complex", graph, "-map", "[out]", "-ar", "48000", "-ac", "2", out], check=True)
    print(f"✓ {out}  总长 {total:.3f}s")


if __name__ == "__main__":
    main()
