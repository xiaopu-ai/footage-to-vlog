#!/usr/bin/env python3
"""核对音效是否卡在画面事件帧上:检测起音,和期望帧逐个比偏差。

用法:
  python3 scripts/onsets.py sfx-only.wav --frames 98,113,128 [--fps 30] [--band high] [--to 8]

--band low  : 高通 300Hz 以上(弹跳、撞击这类中低频为主的音效)
--band high : 高通 3.5kHz 以上(水晶轻响、点击这类清脆音效;被前一个音效余音盖住时用这个)
建议用"只有音效"的轨(Composition 传 music:false 渲 wav),音乐会淹没起音。
AAC 编码自带约 21ms 延迟,成片里量出 20–40ms 的偏差属正常。
"""
import argparse
import math
import os
import struct
import subprocess

FF = (os.environ.get("FFMPEG") or __import__("shutil").which("ffmpeg") or "ffmpeg")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--frames", required=True, help="期望事件帧,逗号分隔,可带小数")
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--band", choices=["low", "high"], default="low")
    ap.add_argument("--to", type=float, default=None, help="只分析前 N 秒")
    args = ap.parse_args()

    sr = 16000 if args.band == "high" else 8000
    af = "highpass=f=3500,highpass=f=3500" if args.band == "high" else "highpass=f=300"
    cmd = [FF, "-v", "error", "-i", args.audio] + (["-t", str(args.to)] if args.to else []) + ["-ac", "1", "-ar", str(sr), "-af", af, "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    s = struct.unpack("<%dh" % (len(raw) // 2), raw)
    hop = sr // 200  # 5ms
    db = [20 * math.log10(math.sqrt(sum(x * x for x in s[i : i + hop]) / hop) + 1e-9) for i in range(0, len(s) - hop, hop)]
    ons = []
    for i in range(8, len(db) - 2):
        prev = sum(db[i - 8 : i - 2]) / 6
        if db[i] - prev > 6 and db[i] >= max(db[i - 2 : i + 3]):
            t = i / 200
            if not ons or t - ons[-1] > 0.09:
                ons.append(t)
    print(f"检测到 {len(ons)} 个起音")
    for f in [float(x) for x in args.frames.split(",")]:
        t = f / args.fps
        near = min(ons, key=lambda o: abs(o - t)) if ons else None
        dev = f"{round((near - t) * 1000):+d}ms" if near is not None else "—"
        flag = "" if near is not None and abs(near - t) < 0.06 else "  ← 偏差大/没检测到,换 --band 再看"
        print(f"帧 {f:g}({t:.3f}s) → 最近起音 {near if near is None else round(near, 3)}s  偏差 {dev}{flag}")


if __name__ == "__main__":
    main()
