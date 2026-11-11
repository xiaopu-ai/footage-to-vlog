#!/usr/bin/env python3
"""measure.py · 逐镜量亮度和冷暖,给出让全片统一的 fix 建议(写进 edit.json 镜头的 "fix")。

用法:
  python3 scripts/measure.py <proj>                    # 读 <proj>/src/timeline.json 的 clips,按成片色彩链每镜抽 3 帧量
  python3 scripts/measure.py <proj>/src/timeline.json  # 同上
  python3 scripts/measure.py <a.jpg> <b.png> …         # 直接量图片(比如 grab.py 抽的风格帧)
  选项:--only S01,S07   --target-rb X   --target-p50 Y   --refine(按建议再量一遍,修正后再给)   --json 输出路径   --no-queue
  timeline 模式会自动钻进 scripts/queue.sh 排队。

量什么(每镜 3 帧取平均;帧在去掉 handles 的可见区间 25% / 50% / 75% 处):
  p5 / p50 / p95   Rec.709 亮度百分位,0–1
  R−B              中间调(亮度 60–190 / 255)像素的平均 R−B,0–255 刻度;正 = 偏暖
建议(目标默认取全片中位,也可以用 --target-* 指定):
  fix.warm      += (R−B − 目标) / 150     (k>0 往冷;150 是每单位 k 对 R−B 的影响,和色表有关:
                                          实测 B / A2 色表 145–170,A 色表约 290;--refine 会按本镜实测斜率修正)
  fix.exposure  += 2.2 × log2(目标 p50 / p50)   (档;只在差 > 0.15 档时建议)
  p5 / p95 偏离全片中位超过 0.03(3 个百分点)→ 建议 fix.levels = "auto";p5 < 0.04 提示暗部压死,p95 > 0.95 提示高光削顶。
  建议是一步估算(色表是非线性的),改完再跑一次核对;--refine 会自动多量一轮。

产物:终端表格 + JSON(timeline 模式默认 <proj>/work/measure.json)
"""
import argparse
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ftvlib as L  # noqa: E402

RB_PER_K = 150.0
IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


def median(xs):
    xs = sorted(x for x in xs if x == x)
    if not xs:
        return float("nan")
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def avg_stats(ss):
    keys = ("p5", "p50", "p95", "rb_mid")
    return {k: round(sum(s[k] for s in ss) / len(ss), 4) for k in keys}


def measure_clip(proj, project, c, footage, handles, fix_override=None, levels_cache=None):
    e = L.source_entry(proj, project, c["src"], footage=footage, profile=c.get("profile"))
    lut = L.resolve_lut(proj, project, e["profile"])
    look_rel = c["look"] if "look" in c else project.get("look")
    look = str((Path(look_rel) if os.path.isabs(look_rel) else proj / look_rel).resolve()) if look_rel else None
    fix = dict(c.get("fix") or {}) if fix_override is None else fix_override
    nfps = float(c.get("native_fps") or e.get("fps") or 30)
    c0 = float(c["from_s"])
    c1 = c0 + int(c["frames"]) / nfps if c.get("frames") else float(c["to_s"])  # 和 make_clips 同一区间
    a, b = c0, c1
    if b - a > 2 * handles + 0.1:
        a, b = a + handles, b - handles
    hw = L.hw_candidate(e) and L.use_hwaccel(e["_abs"], e, lut)
    target = tuple(project.get("levels_target") or L.DEFAULT_LEVELS_TARGET)
    levels = None
    if fix.get("levels") == "auto":
        key = (c["id"], json.dumps({k: v for k, v in fix.items() if k != "levels"}, sort_keys=True))
        if levels_cache is not None and key in levels_cache:
            levels = levels_cache[key]
        else:
            levels = L.measure_levels(e, max(0.0, c0), c1, lut, fix, hw)
            if levels_cache is not None:
                levels_cache[key] = levels
    ss = []
    for k in (0.25, 0.5, 0.75):
        rgb = L.rgb_frame(e, a + (b - a) * k, 480, 270, lut, fix, levels, target, look, hw, depth=16)
        ss.append(L.frame_stats(rgb))
    return avg_stats(ss), fix


def suggest(st, fix, t_rb, t_p50, med):
    out = dict(fix or {})
    notes = []
    d_rb = st["rb_mid"] - t_rb
    if abs(d_rb) > 2.5 and d_rb == d_rb:
        out["warm"] = round(float(fix.get("warm") or 0) + d_rb / RB_PER_K, 3)
        notes.append("偏%s %.1f" % ("暖" if d_rb > 0 else "冷", abs(d_rb)))
    if (abs(st["p5"] - med["p5"]) > 0.03 or abs(st["p95"] - med["p95"]) > 0.03) and fix.get("levels") != "auto":
        out["levels"] = "auto"
        notes.append("p5/p95 偏离全片中位 >0.03")
    if out.get("levels") == "auto":
        if "exposure" in out and "exposure" not in (fix or {}):
            out.pop("exposure")
        if out.get("levels") != fix.get("levels"):
            notes.append("levels auto 会统一亮度,不另调曝光")
    elif st["p50"] > 0.01:
        ev = 2.2 * math.log2(t_p50 / st["p50"])
        if abs(ev) > 0.15:
            out["exposure"] = round(max(-1.5, min(1.5, float(fix.get("exposure") or 0) + ev)), 2)
            notes.append("%s %.2f 档" % ("欠曝" if ev > 0 else "过曝", abs(ev)))
    if st["p5"] < 0.04:
        notes.append("暗部压死 p5 %.3f" % st["p5"])
    if st["p95"] > 0.95:
        notes.append("高光削顶 p95 %.3f" % st["p95"])
    return out, notes


def refine(r, measure, t_rb, t_p50):
    """按实测斜率修正一步估算:先只改 levels / exposure 量一次(a),曝光用 原始→a 的斜率修正;
    再加上建议的 warm 量一次(b),冷暖用 a→b 的斜率修正。"""
    fix0, sug, s0 = r["fix"], dict(r["suggest"]), r["stats"]
    fa = {k: v for k, v in sug.items() if k != "warm"}
    if "warm" in fix0:
        fa["warm"] = fix0["warm"]
    sa = measure(fa) if fa != fix0 else s0
    e0, e1 = float(fix0.get("exposure") or 0), float(fa.get("exposure") or 0)
    if "exposure" in sug and fa.get("levels") != "auto" and abs(e1 - e0) > 1e-3 and sa["p50"] > 0.01:
        slope = (math.log2(sa["p50"]) - math.log2(s0["p50"])) / (e1 - e0)  # 每档 p50 变化(log2)
        if slope > 0.05:
            sug["exposure"] = fa["exposure"] = round(max(-1.5, min(1.5, e1 + (math.log2(t_p50) - math.log2(sa["p50"])) / slope)), 2)
            sa = measure(fa)
    note = "改完 R−B %.1f p50 %.3f" % (sa["rb_mid"], sa["p50"])
    k0 = float(fa.get("warm") or 0)
    d = sa["rb_mid"] - t_rb
    if abs(d) > 2.5:
        k1 = k0 + d / RB_PER_K
        fb = dict(fa, warm=round(k1, 3))
        sb = measure(fb)
        slope = (sb["rb_mid"] - sa["rb_mid"]) / (k1 - k0)  # 每单位 k 的 R−B 变化,应为负
        if slope < -20:
            sug["warm"] = round(k1 + (sb["rb_mid"] - t_rb) / -slope, 3)
            note = "实测斜率 %.0f/k,改完约 R−B %.1f p50 %.3f" % (slope, t_rb, sa["p50"])
    elif "warm" in sug and "warm" not in fix0:
        sug.pop("warm")
    elif "warm" in fix0:
        sug["warm"] = fix0["warm"]
    r["suggest"] = sug
    r["notes"].append(note)


def table(rows, t_rb, t_p50):
    print("\n%-14s %6s %6s %6s %7s   %-36s %s" % ("镜", "p5", "p50", "p95", "R−B", "建议 fix", "说明"))
    for r in rows:
        sug = r["suggest"] if r["suggest"] != r["fix"] else {}
        print("%-14s %6.3f %6.3f %6.3f %7.1f   %-36s %s" % (
            r["id"][:14], r["stats"]["p5"], r["stats"]["p50"], r["stats"]["p95"], r["stats"]["rb_mid"],
            json.dumps(sug, ensure_ascii=False) if sug else "—", ";".join(r["notes"]) or "ok"))
    print("目标:R−B %.1f · p50 %.3f(全片中位,或 --target-*)" % (t_rb, t_p50))


def main():
    ap = argparse.ArgumentParser(description="逐镜量亮度 / 冷暖,给统一全片的 fix 建议")
    ap.add_argument("inputs", nargs="+", help="<proj> 或 timeline.json 或图片")
    ap.add_argument("--only")
    ap.add_argument("--target-rb", type=float)
    ap.add_argument("--target-p50", type=float)
    ap.add_argument("--refine", action="store_true", help="按建议再量一轮,用实测斜率修正")
    ap.add_argument("--json")
    ap.add_argument("--no-queue", action="store_true")
    a = ap.parse_args()

    imgs = [p for p in a.inputs if Path(p).suffix.lower() in IMG_EXT]
    rows = []
    if imgs:
        import numpy as np
        from PIL import Image
        for p in imgs:
            arr = np.asarray(Image.open(p).convert("RGB"), dtype=np.float64) / 255.0
            rows.append({"id": Path(p).stem, "file": p, "stats": avg_stats([L.frame_stats(arr)]), "fix": {}})
        out_json = Path(a.json) if a.json else None
    else:
        L.ensure_queued()
        x = Path(a.inputs[0]).resolve()
        proj = x.parent.parent if x.suffix == ".json" else x
        tlf = x if x.suffix == ".json" else proj / "src" / "timeline.json"
        tl = json.loads(tlf.read_text("utf-8"))
        project = L.load_project(proj)
        footage = L.load_footage(proj)
        handles = float(project.get("handles_s") or 0)
        want = set(a.only.split(",")) if a.only else None
        cache = {}
        for c in tl.get("clips", []):
            if want and c["id"] not in want:
                continue
            st, fix = measure_clip(proj, project, c, footage, handles, levels_cache=cache)
            rows.append({"id": c["id"], "src": c["src"], "stats": st, "fix": fix, "_clip": c})
            print("  量完 %-10s p50 %.3f R−B %.1f" % (c["id"], st["p50"], st["rb_mid"]), flush=True)
        out_json = Path(a.json) if a.json else proj / "work" / "measure.json"

    if not rows:
        L.die("没有可量的镜头")
    med = {k: median([r["stats"][k] for r in rows]) for k in ("p5", "p50", "p95", "rb_mid")}
    t_rb = a.target_rb if a.target_rb is not None else med["rb_mid"]
    t_p50 = a.target_p50 if a.target_p50 is not None else med["p50"]
    for r in rows:
        r["suggest"], r["notes"] = suggest(r["stats"], r["fix"], t_rb, t_p50, med)

    if a.refine and not imgs:
        print("\n--refine:按建议实测修正…", flush=True)
        for r in rows:
            if r["suggest"] != r["fix"]:
                refine(r, lambda f, r=r: measure_clip(proj, project, r["_clip"], footage, handles, fix_override=f,
                                                      levels_cache=cache)[0], t_rb, t_p50)

    table(rows, t_rb, t_p50)
    for r in rows:
        r.pop("_clip", None)
    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps({"target": {"rb_mid": t_rb, "p50": t_p50}, "median": med, "shots": rows},
                                       ensure_ascii=False, indent=1), "utf-8")
        print("✓", out_json)


if __name__ == "__main__":
    main()
