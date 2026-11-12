#!/usr/bin/env python3
"""loudnorm.py · 给已有成片补响度：先限幅，再两遍 loudnorm（视频流直接复制，不重编码），最后复测。

新片子不用单独跑它：render.py 已经做了「规范混音 → 限幅 → 两遍 loudnorm → 封装」。这个脚本给已经封好的文件补响度用；
它不修音画对齐——Remotion 直出的音轨晚约 40 ms 的问题要用 render.py 重新封装。

用法：
  python3 scripts/loudnorm.py <输入.mp4> <输出.mp4> [--I -14] [--TP -1.5] [--limit 0.74 | --no-limit]
  例：python3 scripts/loudnorm.py out/raw.mp4 out/final.mp4

做法：
  1. 限幅 `alimiter=limit=0.74:latency=1`（默认开；latency=1 补偿前视延迟，不然整条声音晚 5 ms）。
     直接 loudnorm 常常真峰值超标（实测 −1.4 dBTP）。
  2. 量「限幅后真峰值 − 响度」：超过 (TP − I) − 0.2 dB 时 loudnorm 只能走动态模式（会改动态），就按超出量把 limit 往下压再量，
     直到能纯线性增益（最低 0.35）。--no-limit 时不限幅、不调。
  3. 第二遍 loudnorm（linear=true）→ AAC 320k / 48 kHz，视频 -c:v copy，+faststart。
  4. 复测输出：真峰值还超（AAC 编码会抬一点峰值）就把 limit 再压低重来，最多 3 次。
抖音 / 视频号成片用 −14 LUFS、真峰值 −1.5 dBTP。
检查边界：只管整段积分响度和真峰值，不管段落间的响度起伏、不管混音比例。依赖：ffmpeg（alimiter、loudnorm、aac）。
"""
import argparse
import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render as R  # noqa: E402  共用 loud_measure / run


def main():
    ap = argparse.ArgumentParser(description="限幅 + 两遍 loudnorm（视频流复制）")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--I", type=float, default=-14)
    ap.add_argument("--TP", type=float, default=-1.5)
    ap.add_argument("--LRA", type=float, default=11)
    ap.add_argument("--limit", type=float, default=0.74, help="alimiter 的 limit 起点（默认 0.74）")
    ap.add_argument("--no-limit", action="store_true", help="不限幅（不推荐）")
    a = ap.parse_args()
    if os.path.abspath(a.src) == os.path.abspath(a.dst):
        sys.exit("✗ 输入和输出不能是同一个文件")
    plr_max = (a.TP - a.I) - 0.2
    limit = a.limit
    m0 = R.loud_measure(a.src, I=a.I, TP=a.TP, LRA=a.LRA)
    print("原始：%.2f LUFS，真峰值 %.2f dBTP，LRA %.1f" % (m0["input_i"], m0["input_tp"], m0["input_lra"]))
    for attempt in range(3):
        pre = None
        for _ in range(8):
            pre = None if a.no_limit else R.lim_filter(limit)
            p1 = R.loud_measure(a.src, pre=pre, I=a.I, TP=a.TP, LRA=a.LRA)
            plr = p1["input_tp"] - p1["input_i"]
            if a.no_limit or plr <= plr_max or limit <= R.MIN_LIMIT:
                break
            limit = max(R.MIN_LIMIT, limit * 10 ** (-(plr - plr_max + 0.05) / 20.0))
        lra = a.LRA if p1["input_lra"] <= a.LRA else min(50.0, math.ceil(p1["input_lra"] + 1))
        ln = ("loudnorm=I=%g:TP=%g:LRA=%g:measured_I=%s:measured_TP=%s:measured_LRA=%s:measured_thresh=%s:offset=%s:linear=true:"
              "print_format=json" % (a.I, a.TP, lra, p1["input_i"], p1["input_tp"], p1["input_lra"], p1["input_thresh"], p1["target_offset"]))
        af = (pre + "," if pre else "") + ln + ",aresample=48000"
        err = R.run([R.ff(), "-hide_banner", "-nostdin", "-nostats", "-y", "-i", a.src, "-map", "0:v:0?", "-map", "0:a:0", "-c:v", "copy",
                     "-af", af, "-ar", "48000", "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", a.dst], "响度标准化")
        mt = re.findall(r"\"normalization_type\"\s*:\s*\"(\w+)\"", err)
        m2 = R.loud_measure(a.dst, I=a.I, TP=a.TP, LRA=a.LRA)
        print("%s：%.2f LUFS，真峰值 %.2f dBTP（限幅 %s，loudnorm %s 模式）" % (
            a.dst, m2["input_i"], m2["input_tp"], "关" if a.no_limit else "%.3f" % limit, mt[-1] if mt else "?"))
        excess = m2["input_tp"] - a.TP
        if excess <= 0 or a.no_limit or attempt == 2:
            break
        limit = limit * 10 ** (-(excess + 0.2) / 20.0)
        print("  真峰值超了 %.2f dB：限幅压到 %.3f 重来" % (excess, limit))
    ok = abs(m2["input_i"] - a.I) <= 0.5 and m2["input_tp"] <= a.TP
    print("✓ 达标" if ok else "✗ 没达标（%g ±0.5 LUFS、≤ %g dBTP）" % (a.I, a.TP))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
