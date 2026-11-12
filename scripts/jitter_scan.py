#!/usr/bin/env python3
"""jitter_scan.py · 成片抖动扫描：第 i 帧和 i+1 帧不同、但和 i+2 帧几乎相同 = 来回跳（A-B-A）。
起因：并发渲染 CSS 3D 场景时，同一帧在不同标签页里栅格化结果差 1–7 px，成片里字来回抖。

用法：
  python3 scripts/jitter_scan.py <成片.mp4> [--range a-b] [--base F] [--json out.json] [--no-queue]
  例：python3 scripts/jitter_scan.py out/film.mp4
      python3 scripts/jitter_scan.py out/film.mp4 --range 400-800        # 只扫视频的第 400–800 帧
      python3 scripts/jitter_scan.py out/preview.mp4 --base 0              # 报告里的帧号加上 base（视频第 0 帧 = 成片第 base 帧）
  范围：`a-b` 闭区间 / `a:b` 半开，指这个视频文件里的帧号。
输出：每秒出现 ≥ 3 次来回跳的时间点 + 变化区域（480×270 坐标）；没有这样的秒 = 干净，退出码 0，否则 1。

判定（和原版一致）：缩到 480×270 灰度，|差| > 30 的像素数 n1 = (i, i+1)，n2 = (i, i+2)；15 < n1 < 4000 且 n2 < n1 × 0.25 记一次。
检查边界：只查「A-B-A 来回跳」。重复帧、整段卡顿、变速发顿、编码马赛克都不在这里查；变化面积 ≥ 4000 像素（大面积运动）的不算。
🔴 要解码整段视频：自动钻进 scripts/queue.sh 排队。依赖：numpy；ffmpeg。兼容 Python 3.9。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

W, H = 480, 270


def parse_range(spec):
    s = str(spec).strip()
    if ":" in s:
        x, y = s.split(":", 1)
        return int(x or 0), (int(y) if y.strip() else None)
    i = s.index("-", 1) if "-" in s[1:] else (0 if s.startswith("-") else -1)
    if i < 0:
        return int(s), int(s) + 1
    x, y = s[:i], s[i + 1:]
    return int(x or 0), (int(y) + 1 if y.strip() else None)


def scan(path, lo=0, hi=None):
    """从头解码（不跳转，帧号绝对准），只分析视频帧 [lo, hi)。返回 (命中列表, 分析帧数)。"""
    ff = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
    stop = ["-frames:v", str(hi)] if hi is not None else []  # 解到 hi 帧就停，不留断管报错
    p = subprocess.Popen([ff, "-hide_banner", "-nostdin", "-v", "error", "-i", path, "-an",
                          "-vf", "scale=%d:%d,format=gray" % (W, H)] + stop + ["-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    buf, hits, n, i = [], [], 0, 0
    while True:
        b = p.stdout.read(W * H)
        if len(b) < W * H or (hi is not None and i >= hi):
            break
        if i >= lo:
            buf.append(np.frombuffer(b, np.uint8).astype(np.int16))
            buf = buf[-3:]
            n += 1
            if len(buf) == 3:
                m1 = np.abs(buf[0] - buf[1]) > 30
                n1 = int(m1.sum())
                n2 = int((np.abs(buf[0] - buf[2]) > 30).sum())
                if 15 < n1 < 4000 and n2 < n1 * 0.25:
                    ys, xs = np.nonzero(m1.reshape(H, W))
                    hits.append((i - 2, n1, [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]))
        i += 1
    p.stdout.close()
    p.kill()
    p.wait()
    return hits, n


def main():
    ap = argparse.ArgumentParser(description="成片 A-B-A 来回跳扫描")
    ap.add_argument("mp4")
    ap.add_argument("legacy", nargs="?", help=argparse.SUPPRESS)  # 旧版的第二个位置参数（没用），兼容一下
    ap.add_argument("--range", help="视频帧范围：a-b 闭区间 / a:b 半开")
    ap.add_argument("--base", type=int, default=0, help="视频第 0 帧对应成片第几帧（只影响报告里的帧号）")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--json", help="结果另存 JSON")
    ap.add_argument("--no-queue", action="store_true", help="不排队")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ftvlib as L  # 只用 ensure_queued
    L.ensure_queued()
    lo, hi = parse_range(a.range) if a.range else (0, None)
    hits, n = scan(a.mp4, lo, hi)
    g = defaultdict(list)
    for f, c, bb in hits:
        g[int((f + a.base) / a.fps)].append((f, c, bb))
    flagged = []
    for s in sorted(g):
        if len(g[s]) >= 3:
            f0 = g[s][0][0] + a.base
            flagged.append({"second": s, "count": len(g[s]), "frames": [x[0] + a.base for x in g[s]], "bbox_480": g[s][0][2]})
            print("%d:%02d（成片帧 %d 起）  %d 次  例 bbox(480 宽) %s" % (s // 60, s % 60, f0, len(g[s]), g[s][0][2]))
    print("扫了 %d 帧（视频帧 %d–%s），单次来回跳 %d 次，≥ 3 次 / 秒的 %d 秒%s" % (
        n, lo, (hi - 1) if hi else "末尾", len(hits), len(flagged), "" if flagged else "：干净"))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"video": a.mp4, "frames_scanned": n, "range": [lo, hi], "base": a.base, "single_hits": len(hits),
                       "flagged_seconds": flagged, "status": "fail" if flagged else "pass",
                       "rule": "15 < n1 < 4000 且 n2 < n1 × 0.25 记一次；同一秒 ≥ 3 次算抖动"}, f, ensure_ascii=False, indent=1)
    sys.exit(1 if flagged else 0)


if __name__ == "__main__":
    main()
