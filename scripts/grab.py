#!/usr/bin/env python3
"""grab.py · 按「素材 + 秒数」抽单帧,出 1080p JPG / PNG。风格帧、分镜、逐镜核对素材都用它:
色彩链和 make_clips.py 完全一样(技术 LUT → 逐镜 fix → HALD 色表),审过的颜色就是成片的颜色。

用法:
  python3 scripts/grab.py <素材> <秒> [<素材> <秒> …] [--proj P | --root DIR] [--out DIR]
                          [--look PNG|none] [--fix '{"warm":0.05,"exposure":0.2}'] [--profile sony-slog3]
                          [--png] [--width 1920] [--name 名]
  python3 scripts/grab.py --proj P --shots all|S01,S07 [--at start,mid,end]     # 按 timeline.json 逐镜抽首 / 中 / 尾

  <素材> 是相对 footage_root(--proj 的 project.json 或 --root)的路径,也可以是绝对路径;<秒> 是源素材时间。
  --look:默认取 project.json 的 look(没有 --proj 就不套);none = 只做技术还原。
  --fix 的 levels:"auto" 时在 <秒> 前后各 1 秒量 p5 / p95(make_clips 是在整个片段里量,--shots 模式会直接用 clips.json 里量好的值)。
  --tech:只做技术还原(不套 look、不做 fix),看素材本来的样子。
  会自动钻进 scripts/queue.sh 排队(4K HEVC 抽帧也不便宜)。

产物:
  <out>/<素材文件名>_<秒>.jpg(--name 指定单张的名字;--png 出 PNG)
  --shots 模式:<out>/<镜号>_<start|mid|end>.jpg;默认 <out> = <proj>/work/frames
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ftvlib as L  # noqa: E402


def save(arr, dst, png):
    from PIL import Image
    im = Image.fromarray((arr * 255 + 0.5).clip(0, 255).astype("uint8"))
    dst.parent.mkdir(parents=True, exist_ok=True)
    if png:
        im.save(dst)
    else:
        im.save(dst, quality=95, subsampling=0)
    print("✓", dst, flush=True)


def resolve_look(proj, project, arg):
    if arg is not None:
        if arg.lower() == "none":
            return None
        p = Path(arg)
        if not p.is_absolute() and proj and not p.exists():
            p = Path(proj) / arg
        if not p.exists():
            L.die("色表不存在:%s" % arg)
        return str(p.resolve())
    if proj and project.get("look"):
        p = Path(proj) / project["look"]
        if not p.exists():
            L.die("project.json 的 look 不存在:%s" % p)
        return str(p.resolve())
    return None


def main():
    ap = argparse.ArgumentParser(description="按素材 + 秒数抽单帧(和成片同一条色彩链)")
    ap.add_argument("pairs", nargs="*", help="<素材> <秒> 成对给")
    ap.add_argument("--proj")
    ap.add_argument("--root", help="素材根目录(没有 --proj 时用)")
    ap.add_argument("--out")
    ap.add_argument("--look", help="HALD 色表 png;none = 不套")
    ap.add_argument("--fix", help='逐镜校正 JSON,如 {"warm":0.05,"exposure":0.2,"levels":"auto"}')
    ap.add_argument("--profile", help="强制机型曲线(sony-slog3 / dji-dlogm / rec709 …)")
    ap.add_argument("--tech", action="store_true", help="只做技术还原")
    ap.add_argument("--png", action="store_true")
    ap.add_argument("--width", type=int, default=None, help="输出宽度,默认 project.json 的 width(1920)")
    ap.add_argument("--name", help="单张时的输出文件名(不含扩展名)")
    ap.add_argument("--shots", help="按 timeline.json 抽:all 或逗号分隔的镜号")
    ap.add_argument("--at", default="start,mid,end", help="--shots 时抽哪几处:start,mid,end")
    ap.add_argument("--no-queue", action="store_true")
    a = ap.parse_args()
    if not a.shots and (not a.pairs or len(a.pairs) % 2):
        ap.error("要成对给 <素材> <秒>,或用 --proj P --shots all")
    L.ensure_queued()

    proj = Path(a.proj).resolve() if a.proj else None
    project = L.load_project(proj) if proj else {"luts": {}, "profiles": {}, "width": 1920, "height": 1080,
                                                  "levels_target": list(L.DEFAULT_LEVELS_TARGET)}
    W = a.width or project.get("width", 1920)
    H = round(W * project.get("height", 1080) / project.get("width", 1920) / 2) * 2
    target = tuple(project.get("levels_target") or L.DEFAULT_LEVELS_TARGET)
    out = Path(a.out) if a.out else ((proj / "work" / "frames") if proj else Path("frames"))
    ext = ".png" if a.png else ".jpg"
    footage = L.load_footage(proj) if proj else {}
    look_arg = "none" if a.tech else a.look

    jobs = []  # (src, t, dst, fix, look, levels_range, levels_known, profile)
    if a.shots:
        if not proj:
            L.die("--shots 需要 --proj")
        tl = json.loads((proj / "src" / "timeline.json").read_text("utf-8"))
        clips = {c["id"]: c for c in tl.get("clips", [])}
        cj = proj / "public" / "clips" / "clips.json"
        measured = json.loads(cj.read_text("utf-8")).get("clips", {}) if cj.exists() else {}
        want = None if a.shots == "all" else set(a.shots.split(","))
        for sh in tl.get("shots", []):
            if want and sh["id"] not in want:
                continue
            c = clips.get(sh["id"], {})
            fps = sh.get("native_fps") or c.get("native_fps") or 30
            a0, a1 = sh["src_in_s"], max(sh["src_in_s"], sh["src_out_s"] - 1.0 / fps)
            pos = {"start": a0, "mid": (a0 + a1) / 2, "end": a1}
            fix = {} if a.tech else (c.get("fix") or {})
            if look_arg is not None:
                look = resolve_look(proj, project, look_arg)
            elif "look" in c:  # 和 make_clips 一致:clip 里写了 look(null = 不套)就用它,没写才用 project.json 的
                look = resolve_look(proj, project, c["look"] or "none")
            else:
                look = resolve_look(proj, project, None)
            known = (measured.get(sh["id"]) or {}).get("levels")
            c0 = c.get("from_s", a0)
            rng = (c0, c0 + c["frames"] / fps) if c.get("frames") else (c0, c.get("to_s", a1))
            for k in a.at.split(","):
                jobs.append((sh["src"], pos[k], out / ("%s_%s%s" % (sh["id"], k, ext)), fix, look, rng, known, c.get("profile")))
    else:
        fix = {} if a.tech else (json.loads(a.fix) if a.fix else {})
        look = resolve_look(proj, project, look_arg)
        n = len(a.pairs) // 2
        for i in range(n):
            src, t = a.pairs[2 * i], float(a.pairs[2 * i + 1])
            stem = a.name if (a.name and n == 1) else "%s_%s" % (Path(src).stem, ("%.2f" % t).rstrip("0").rstrip("."))
            jobs.append((src, t, out / (stem + ext), fix, look, (t - 1, t + 1), None, a.profile))

    for src, t, dst, fix, look, rng, known, prof in jobs:
        e = L.source_entry(proj, project, src, root=a.root, footage=footage, profile=prof, force_profile=a.profile)
        lut = L.resolve_lut(proj, project, e["profile"])
        hw = L.hw_candidate(e) and L.use_hwaccel(e["_abs"], e, lut)
        levels = None
        if fix.get("levels") == "auto":
            levels = known or L.measure_levels(e, max(0.0, rng[0]), rng[1], lut, fix, hw)
        arr = L.rgb_frame(e, t, W, H, lut, fix, levels, target, look, hw)
        save(arr, dst, a.png)


if __name__ == "__main__":
    main()
