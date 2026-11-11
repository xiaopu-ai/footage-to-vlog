#!/usr/bin/env python3
"""两遍 loudnorm 把成片响度归一(视频流直接复制,不重编码),最后复测。

用法:
  python3 scripts/loudnorm.py raw.mp4 final.mp4 [--I -14] [--TP -1.5]

抖音 / 视频号成片用 -14 LUFS、真峰值 -1.5 dBTP。
"""
import argparse
import json
import os
import re
import subprocess

FF = (os.environ.get("FFMPEG") or __import__("shutil").which("ffmpeg") or "ffmpeg")


def measure(path, I, TP):
    p = subprocess.run([FF, "-hide_banner", "-i", path, "-af", f"loudnorm=I={I}:TP={TP}:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True)
    return json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, re.S).group(0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--I", type=float, default=-14)
    ap.add_argument("--TP", type=float, default=-1.5)
    a = ap.parse_args()
    m = measure(a.src, a.I, a.TP)
    print(f"原始:{m['input_i']} LUFS,真峰值 {m['input_tp']} dBTP")
    af = f"loudnorm=I={a.I}:TP={a.TP}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true"
    subprocess.run([FF, "-v", "error", "-y", "-i", a.src, "-c:v", "copy", "-af", af, "-ar", "48000", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", a.dst], check=True)
    m2 = measure(a.dst, a.I, a.TP)
    print(f"✓ {a.dst}:{m2['input_i']} LUFS,真峰值 {m2['input_tp']} dBTP")


if __name__ == "__main__":
    main()
