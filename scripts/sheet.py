#!/usr/bin/env python3
"""联系表:把一批静帧(或从视频按 fps 抽帧)拼成一张带帧号的大图,用来一眼审整段动作。

用法:
  python3 scripts/sheet.py <静帧目录>                                   # 目录里的 f*.png(template/tools/stills.mjs 的输出)
  python3 scripts/sheet.py --video out/film.mp4 --fps 2 --out work/qa/sheet.jpg
  python3 scripts/sheet.py --video out/film.mp4 --fps 5 --from 1.6 --to 8 --cols 6 --width 420

整片 QA 用 2fps;看一段动作连不连贯用 5–10fps;细节再用 template/tools/stills.mjs --scale=1 渲全分辨率。
"""
import argparse
import glob
import os
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

FF = (os.environ.get("FFMPEG") or __import__("shutil").which("ffmpeg") or "ffmpeg")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", nargs="?", help="含 f*.png 的目录")
    ap.add_argument("--video")
    ap.add_argument("--fps", type=float, default=2)
    ap.add_argument("--from", dest="a", type=float, default=0)
    ap.add_argument("--to", dest="b", type=float, default=None)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--width", type=int, default=640, help="每格宽度")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    labels = []
    if a.video:
        tmp = tempfile.mkdtemp(prefix="sheet-")
        cmd = [FF, "-v", "error", "-ss", str(a.a), "-i", a.video] + (["-t", str(a.b - a.a)] if a.b else []) + ["-vf", f"fps={a.fps},scale={a.width}:-1", os.path.join(tmp, "f%04d.png")]
        subprocess.run(cmd, check=True)
        files = sorted(glob.glob(os.path.join(tmp, "f*.png")))
        labels = [f"{a.a + i / a.fps:.1f}s" for i in range(len(files))]
        out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.video)), "sheet.jpg")
    else:
        files = sorted(glob.glob(os.path.join(a.dir, "f*.png")))
        labels = [os.path.basename(f)[1:5] for f in files]
        out = a.out or os.path.join(a.dir, "sheet.jpg")
    if not files:
        if a.video:
            shutil.rmtree(tmp, ignore_errors=True)
        raise SystemExit("没有找到帧")

    ims = [Image.open(f).convert("RGB") for f in files]
    if a.video:
        shutil.rmtree(tmp, ignore_errors=True)  # 帧已读进内存,抽帧临时目录用完即删
    tw = a.width
    th = int(tw * ims[0].height / ims[0].width)
    rows = (len(ims) + a.cols - 1) // a.cols
    sheet = Image.new("RGB", (a.cols * tw + (a.cols - 1) * 6, rows * th + (rows - 1) * 6), (40, 40, 40))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 22)
    except Exception:
        font = None
    for i, (im, lab) in enumerate(zip(ims, labels)):
        x, y = (i % a.cols) * (tw + 6), (i // a.cols) * (th + 6)
        sheet.paste(im.resize((tw, th), Image.LANCZOS), (x, y))
        draw.text((x + 8, y + 6), lab, fill=(255, 255, 0), font=font)
    sheet.save(out, quality=88)
    print(f"✓ {out}  {sheet.size[0]}×{sheet.size[1]},{len(ims)} 帧")


if __name__ == "__main__":
    main()
