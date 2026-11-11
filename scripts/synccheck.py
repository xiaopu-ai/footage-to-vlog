#!/usr/bin/env python3
"""成片卡点核对:设计切点(timeline.json 主轨每镜的 cut)↔ 成片里实测的画面切点(帧差突变)↔ 成片音轨里最近的起音;
顺带用 ffmpeg blackdetect 查黑场。

用法:
  python3 synccheck.py <成片.mp4> <proj> [--window 4] [--band 1500-4000] [--rise 10] [--floor -40]
                       [--black-th 0.10] [--black-d 0.10] [--json out.json]
  🔴 要解码整片视频,是重活:有排队脚本的机器上套排队跑,别和渲染 / 转码同时跑。

读:<proj>/src/timeline.json(设计切点、转场、拍网格 grid);没有 grid 时读 <proj>/beats.json。
输出(每个切点一行):
  设计帧 = timeline 里的 cut;实测 = 设计帧 ±window 内帧差最大的位置(新镜头第一帧);差 = 实测 − 设计;
  起音 = 成片音轨 1.5–4 kHz(军鼓 / 拍手)最近的起音,「起音−画面」为正 = 画面领先声音(我们要的),单位帧;
  拍位 = 设计帧相对拍网格的提前量(含 lead)。
  最后给:起音−画面的中位数、|差| ≤ 1.5 帧的比例(只统计硬切类:cut / whip / punch;fade / wipe / sweep / dip 和
  src 为 null 的黑场镜头本来就是渐变,帧差找不准;帧差 < --min-jump 的切点是同一素材接续,看不出切;都单列不计)、
  黑场清单(dip 转场、黑场镜头、片头片尾的黑场算预期,其余标 ⚠️,退出码 1)。
经验值(发布片示例 v2):音频起音比画面早约 1 帧 → 切点整体提前约 2 帧后,画面领先 0.3–1.5 帧,84% 在 ±1.5 帧内;
小节头的刀放在小节头那一帧或前 1 帧(底鼓听感起音晚约 60 ms)。
依赖:numpy;ffmpeg(PATH 里,或环境变量 FFMPEG)。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

import numpy as np

W, H = 96, 54
SR = 22050
SHARP = {"cut", "whip", "punch"}


def main():
    ap = argparse.ArgumentParser(description="成片卡点核对 + 黑场检测")
    ap.add_argument("mp4")
    ap.add_argument("proj")
    ap.add_argument("--window", type=int, default=4, help="在设计帧 ±几帧里找实测切点")
    ap.add_argument("--band", default="1500-4000", help="起音频段 Hz")
    ap.add_argument("--rise", type=float, default=10, help="起音判定:比前 10 ms 均值高出几 dB")
    ap.add_argument("--floor", type=float, default=-40, help="起音判定:电平至少几 dBFS")
    ap.add_argument("--black-th", type=float, default=0.10, help="blackdetect pix_th")
    ap.add_argument("--black-d", type=float, default=0.10, help="blackdetect 最短秒数")
    ap.add_argument("--min-jump", type=float, default=10, help="帧差(0–255 灰度均值)低于它的切点看不出切,不计入统计")
    ap.add_argument("--json", default=None, help="逐镜结果另存 json")
    a = ap.parse_args()
    ff = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not ff:
        sys.exit("找不到 ffmpeg:装好放进 PATH,或设环境变量 FFMPEG")
    tl = json.load(open(os.path.join(a.proj, "src", "timeline.json"), encoding="utf-8"))
    fps = tl["fps"]
    grid = tl.get("grid")
    if not grid and os.path.exists(os.path.join(a.proj, "beats.json")):
        b = json.load(open(os.path.join(a.proj, "beats.json"), encoding="utf-8"))
        grid = {"beat_s": b["beat_s"], "offset_s": b["offset_s"], "beats_per_bar": b.get("beats_per_bar", 4),
                "lead_frames": b.get("lead_frames", 1)}
    main_ = sorted([s for s in tl["shots"] if s["layer"] == 0], key=lambda s: s["cut"])
    design = [(s["id"], s["cut"], s["in_tr"]["type"] if s.get("src") else "black", s["in_tr"].get("pre", 0), s["in_tr"].get("post", 0))
              for s in main_ if s["cut"] > 0]

    # ── 画面:一次解码,同时跑 blackdetect ──
    p = subprocess.run([ff, "-hide_banner", "-nostats", "-v", "info", "-i", a.mp4, "-an",
                        "-vf", f"blackdetect=d={a.black_d}:pix_th={a.black_th},scale={W}:{H},format=gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    if p.returncode != 0:
        sys.exit("ffmpeg 解码失败:" + p.stderr.decode("utf-8", "replace")[-400:])
    v = np.frombuffer(p.stdout, np.uint8).reshape(-1, H, W).astype(np.float32)
    n = len(v)
    d = np.abs(np.diff(v, axis=0)).mean(axis=(1, 2))  # d[i] = 帧 i → i+1
    blacks = [(float(x), float(y)) for x, y in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", p.stderr.decode("utf-8", "replace"))]

    # ── 声音:成片音轨的高频起音 ──
    lo, hi = (float(x) for x in a.band.split("-"))
    ra = subprocess.run([ff, "-v", "error", "-i", a.mp4, "-vn", "-ac", "1", "-ar", str(SR),
                         "-af", f"highpass=f={lo},lowpass=f={hi}", "-f", "f32le", "-"], capture_output=True)
    au = np.frombuffer(ra.stdout, np.float32)
    att = np.array([])
    if len(au):
        hop = 22  # ≈ 1 ms
        M = 20 * np.log10(np.sqrt(np.add.reduceat(au.astype(np.float64) ** 2, np.arange(0, len(au) - hop, hop)) / hop) + 1e-7)
        t = np.arange(len(M)) * hop / SR
        cs = np.concatenate([[0], np.cumsum(M)])
        prev = np.full(len(M), np.inf)
        prev[10:] = (cs[10:-1] - cs[:-11]) / 10
        cand = np.where((M - prev > a.rise) & (M > a.floor))[0]
        out = []
        for i in cand:
            if not out or t[i] - out[-1] > 0.08:
                out.append(t[i])
        att = np.array(out)

    beat_f = grid["beat_s"] * fps if grid else None
    max_gap = (beat_f / 2) if beat_f else 8
    rows = []
    for sid, f, tr, pre, post in design:
        lo_, hi_ = max(1, f - a.window), min(n - 1, f + a.window)
        if hi_ <= lo_:
            rows.append({"id": sid, "design": f, "tr": tr, "cut": None})
            continue
        k = lo_ + int(np.argmax(d[lo_ - 1:hi_]))  # d[k-1] 最大 → 第 k 帧是新镜头
        r = {"id": sid, "design": f, "tr": tr, "cut": k, "dv": k - f, "jump": round(float(d[k - 1]), 1)}
        if len(att):
            j = int(np.argmin(np.abs(att - k / fps)))
            da = (att[j] - k / fps) * fps
            if abs(da) <= max_gap:
                r["onset"] = round(float(att[j] * fps), 2)
                r["da"] = round(float(da), 2)
        if grid:
            bp = (f / fps - grid["offset_s"]) / grid["beat_s"]
            near = round(bp * 2) / 2
            r["grid"] = round(f - (grid["offset_s"] + near * grid["beat_s"]) * fps, 2)
            bpb = grid.get("beats_per_bar", 4)
            r["pos"] = f"{int(near // bpb) + 1}.{int(near % bpb) + 1}" + ("+" if near % 1 else "")
        rows.append(r)

    print(f"{os.path.basename(a.mp4)}:{n} 帧(timeline {tl['duration']} 帧)· 设计切点 {len(design)} 个 · 音频起音 {len(att)} 个")
    if n != tl["duration"]:
        print(f"  ⚠️ 成片帧数和 timeline 不一致,差 {n - tl['duration']} 帧")
    print("镜头    转场   设计帧  实测  差   帧差    拍位    设计−拍点  起音帧    起音−画面")
    for r in rows:
        if r.get("cut") is None:
            print(f"{r['id']:7s} {r['tr']:6s} {r['design']:5d}   (超出成片)")
            continue
        soft = "" if r["tr"] in SHARP else " 软"
        if r["tr"] in SHARP and r["jump"] < a.min_jump:
            soft = " ≈同画面(帧差太小,不计)"
        print(f"{r['id']:7s} {r['tr']:6s} {r['design']:5d}  {r['cut']:5d} {r['dv']:+3d}  {r['jump']:6.1f}  "
              f"{r.get('pos', ''):7s} {r.get('grid', float('nan')):+6.2f}    "
              + (f"{r['onset']:8.2f}  {r['da']:+6.2f}" if "da" in r else "     —        —") + soft)
    sharp = [r for r in rows if r.get("cut") is not None and r["tr"] in SHARP and r["jump"] >= a.min_jump]
    das = np.array([r["da"] for r in sharp if "da" in r])
    dvs = [r["dv"] for r in sharp]
    print(f"\n硬切 {len(sharp)} 个:实测切点 = 设计帧的 {sum(1 for x in dvs if x == 0)} 个;偏差分布 "
          + " ".join(f"{k:+d}:{dvs.count(k)}" for k in sorted(set(dvs))))
    if len(das):
        print(f"起音−画面(帧):中位 {np.median(das):+.2f},|差| ≤ 1.5 帧的比例 {(np.abs(das) <= 1.5).mean() * 100:.0f}%"
              f"({len(das)}/{len(sharp)} 个切点附近找到起音;正数 = 画面领先)")
    else:
        print("起音−画面:切点附近没找到高频起音(没有鼓 / 拍手?试试 --band 30-150)")
    soft_n = len([r for r in rows if r.get("cut") is not None and r["tr"] not in SHARP])
    same_n = len([r for r in rows if r.get("cut") is not None and r["tr"] in SHARP and r["jump"] < a.min_jump])
    if soft_n or same_n:
        print(f"不计入统计:渐变转场 / 黑场镜头 {soft_n} 个,帧差 < {a.min_jump:g} 的切点 {same_n} 个(同一素材接续,看不出切)")

    # 黑场:dip 窗口、片头、片尾算预期
    exp = [((f - pre - 2) / fps, (f + post + 2) / fps) for sid, f, tr, pre, post in design if tr == "dip"]
    exp += [((s["cut"] - 2) / fps, (s["end"] + 2) / fps) for s in main_ if not s.get("src")]
    first = main_[0] if main_ else None
    if first and first["in_tr"]["type"] in ("dip", "fade"):
        exp.append((0, (first["in_tr"]["post"] + 2) / fps))
    last = main_[-1] if main_ else None
    if last and last["out_tr"]["type"] in ("dip", "fade"):
        exp.append(((last["end"] - last["out_tr"]["pre"] - 2) / fps, tl["duration"] / fps + 1))
    print(f"\n黑场 {len(blacks)} 段(pix_th {a.black_th},≥ {a.black_d}s):")
    bad = 0
    for s0, s1 in blacks:
        ok = any(s0 < e1 and s1 > e0 for e0, e1 in exp) or s0 < 0.05 or s1 > tl["duration"] / fps - 0.05
        bad += (not ok)
        print(f"  {s0:7.2f}–{s1:7.2f}s(帧 {int(round(s0 * fps))}–{int(round(s1 * fps))}){'' if ok else '  ⚠️ 不在 dip / 片头片尾,检查是不是缺素材'}")
    if not blacks:
        print("  无")
    if a.json:
        json.dump({"rows": rows, "blacks": blacks}, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
