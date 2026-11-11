#!/usr/bin/env python3
"""look.py · 调色色表(HALD CLUT level 8):把一条 ffmpeg 滤镜链烤成 512×512 的 HALD png,再拿抽好的帧并排比较。

用法:
  python3 scripts/look.py build <名> "<ffmpeg 滤镜链>" [--out work/looks/] [--note 说明]
  python3 scripts/look.py build A|B|A2 [--out work/looks/]       # 内置起点,不用写滤镜链
  python3 scripts/look.py build --builtin [--out work/looks/]    # 三套内置全出
  python3 scripts/look.py list                                   # 看内置三套的滤镜链和说明
  python3 scripts/look.py preview <色表.png …> --frames <帧.jpg …> [--out work/looks/preview.jpg] [--cell 480] [--no-base]

  滤镜链只能是逐像素的颜色操作(curves / colorbalance / eq / colorchannelmixer / lutrgb / lut3d …);
  暗角、天空压暗、锐化、颗粒这类空间效果烤不进色表,由模板按 timeline 的 vignette / sky 画。
  --frames 应该是 grab.py --tech 抽的技术还原帧(套了 LUT、没套 look);想看 fix.levels=auto 的效果,就抽带 --fix 的帧。

产物:
  <out>/<名>.png      HALD level 8(套用:ffmpeg -i 视频 -i <名>.png -filter_complex "[0][1]haldclut")
  <out>/looks.md      每个色表一节:滤镜链、说明、生成时间、文件 sha1(同名重建会更新那一节)
  preview.jpg         对比拼图:每行一帧,第一列技术还原,后面每列一个色表(预览在 numpy 里做四面体插值:同一张 PNG 输入和 ffmpeg haldclut 差 ≤1 级;
                      JPG 输入时 Pillow 和 ffmpeg 的 JPEG 解码不同,个别像素差到 5 级)

内置起点:
  A  温暖人文(口号片 / 访谈 A 案,manifesto looks/README.md 原样)
  B  冷峻诗意(口号片定稿,manifesto looks/README.md 原样)
  A2 冷峻 · 提亮暗部(燃剪定稿的近似):燃剪原版是 Remotion 里的 SVG 滤镜——每镜按 p5 / p95 拉开 → S 曲线 0.45 →
     黑位 0.05 / 白位 0.88 → gamma 1.0 → 饱和 0.62 → 暗部偏青、高光微冷,外加暗角 0.25、天空压暗 0.45。
     这里把「拉开之后」的那段曲线和偏色写成 ffmpeg 链(饱和度矩阵 + 三通道 curves,33 个采样点),假定输入已经被
     fix.levels="auto" 拉到 levels_target(默认 p5→0.05、p95→0.90);所以 **A2 要和 fix.levels="auto" 一起用**。
     暗角和天空压暗是空间效果,不在色表里。曲线用样条拟合,和原公式最大差 0.012(约 3 级)。
"""
import argparse
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ftvlib as L  # noqa: E402

LEVEL = 8
N = LEVEL * LEVEL       # 每通道 64 级
SIZE = LEVEL ** 3       # 512 × 512


def a2_chain(lo=0.05, hi=0.88, s=0.45, gamma=1.0, sat=0.62, shadow=(-0.05, 0.012, 0.045), high=(-0.03, 0.0, 0.02),
             abs_in=L.DEFAULT_LEVELS_TARGET, ceiling=1.0, npts=33):
    """燃剪 A2(燃剪示例片 grade 的 curveTables)→ ffmpeg 链。abs_in = 假定的输入黑白点(= levels_target)。"""
    a, b = abs_in
    k = 1 + s * 9

    def f(v):
        return 1 / (1 + math.exp(-k * (v - 0.5)))

    def scurve(x):
        return x if s <= 0 else (f(x) - f(0)) / (f(1) - f(0))

    def ch(c, x):
        t = min(1.0, max(0.0, scurve(min(1.0, max(0.0, (x - a) / max(1e-3, b - a)))) ** gamma))
        y = lo + t * (hi - lo)
        if x > b:
            y = min(ceiling, hi + (x - b) * 0.6)
        if x < a:
            y = max(0.0, lo - (a - x) * (0.5 if lo > 0.01 else 0))
        y += shadow[c] * (1 - y) ** 2 + high[c] * y ** 2
        return min(1.0, max(0.0, min(y, ceiling)))

    xs = sorted(set([i / (npts - 1) for i in range(npts)] + [a, b]))
    pts = [" ".join("%.4f/%.4f" % (x, ch(c, x)) for x in xs) for c in range(3)]
    # SVG feColorMatrix type=saturate 的系数(sRGB 编码值上做)
    m = [[0.213 + 0.787 * sat, 0.715 - 0.715 * sat, 0.072 - 0.072 * sat],
         [0.213 - 0.213 * sat, 0.715 + 0.285 * sat, 0.072 - 0.072 * sat],
         [0.213 - 0.213 * sat, 0.715 - 0.715 * sat, 0.072 + 0.928 * sat]]
    mix = "colorchannelmixer=" + ":".join("%s%s=%.4f" % (o, i, m[oi][ii]) for oi, o in enumerate("rgb")
                                           for ii, i in enumerate("rgb"))
    return "%s,curves=r='%s':g='%s':b='%s'" % (mix, pts[0], pts[1], pts[2])


BUILTIN = {
    "A": ("eq=contrast=1.06:saturation=1.12,colorbalance=rs=0.02:bs=-0.02:rm=0.05:gm=0.01:bm=-0.05:rh=0.01:bh=-0.02,"
          "curves=master='0/0.01 0.22/0.19 0.5/0.50 0.8/0.82 1/0.93'",
          "温暖人文:微提反差和饱和,中间调偏暖,高光封顶 0.93。口号片 A 案,原样沉淀。不需要 levels。"),
    "B": ("curves=master='0/0.05 0.25/0.21 0.55/0.49 1/0.82',"
          "colorbalance=rs=-0.06:bs=0.06:gs=0.01:rm=-0.02:bm=0.02:rh=-0.02:bh=0.02,eq=saturation=0.86",
          "冷峻诗意:黑位抬到 0.05、高光封顶 0.82、暗部偏青、降饱和。口号片定稿,原样沉淀。不需要 levels。"),
    "A2": (a2_chain(),
           "冷峻 · 提亮暗部(燃剪定稿的近似):S 曲线 0.45、黑位 0.05、白位 0.88、gamma 1.0、饱和 0.62、暗部偏青高光微冷。"
           "要和 fix.levels=\"auto\" 一起用(假定每镜 p5/p95 已拉到 0.05/0.90)。燃剪原版的暗角 0.25、天空压暗 0.45 "
           "是空间效果,由模板按 vignette / sky 画,不在色表里。"),
}

SPATIAL = re.compile(r"(^|,)\s*(vignette|gblur|boxblur|unsharp|noise|smartblur|crop|scale|pad|rotate|transpose|"
                     r"drawbox|drawtext|overlay|perspective|lenscorrection|geq|convolution|sobel|deband)\b")

LOOKS_HEAD = """# 调色色表(HALD CLUT level 8)

由 `scripts/look.py build` 生成,每个色表一节(同名重建会覆盖那一节)。
- 套用:`ffmpeg -i 视频 -i <名>.png -filter_complex "[0][1]haldclut"`;本流程里 make_clips.py / grab.py 按 project.json 的 `look` 自动套,
  顺序是 技术 LUT → 逐镜 fix → 色表。
- 色表只管逐像素的颜色;暗角、天空压暗这类空间效果由模板按 timeline 的 `vignette` / `sky` 画。
"""


def build(name, chain, out, note=""):
    if SPATIAL.search(chain):
        L.die("滤镜链里有空间滤镜(%s),烤不进色表;暗角 / 天空压暗交给模板" % SPATIAL.search(chain).group(2))
    # 不在前面强转 rgb48le:eq 这类只吃 8-bit YUV 的滤镜会因此多一次 rgb48→yuv444p 量化(实测噪声更大);
    # 直接从 haldclutsrc 的 rgb24 起步,和口号片当初生成色表的命令逐位一致。
    out.mkdir(parents=True, exist_ok=True)
    dst = out / ("%s.png" % name)
    tmp = out / ("%s.part.png" % name)
    cmd = L.ff_head() + ["-v", "error", "-f", "lavfi", "-i", "haldclutsrc=level=%d" % LEVEL,
                         "-vf", "%s,format=rgb24" % chain, "-frames:v", "1", "-update", "1", str(tmp)]
    L.run(cmd)
    os.replace(tmp, dst)
    sha = L.sha1_file(dst)[:12]
    md = out / "looks.md"
    txt = md.read_text("utf-8") if md.exists() else LOOKS_HEAD
    sec = "\n## %s\n- 文件:`%s.png` · 生成 %s · sha1 %s\n- 说明:%s\n\n```\n%s\n```\n" % (
        name, name, datetime.now().strftime("%Y-%m-%d %H:%M"), sha, note or "—", chain)
    pat = re.compile(r"\n## %s\n.*?(?=\n## |\Z)" % re.escape(name), re.S)
    txt = pat.sub(lambda _: sec.rstrip("\n"), txt) if pat.search(txt) else txt.rstrip("\n") + "\n" + sec
    md.write_text(txt if txt.endswith("\n") else txt + "\n", "utf-8")
    print("✓ %s(sha1 %s)" % (dst, sha))
    return dst


# ───────── 预览:numpy 里套 HALD(四面体插值,和 ffmpeg haldclut 默认一致)─────────

def load_hald(path):
    import numpy as np
    from PIL import Image
    im = np.asarray(Image.open(path).convert("RGB"), dtype=np.float64) / 255.0
    if im.shape[0] != im.shape[1]:
        L.die("%s 不是 HALD 图(要正方形)" % path)
    lv = round(im.shape[0] ** (1 / 3))
    n = lv * lv
    return im.reshape(n, n, n, 3), n  # 索引 [b, g, r]


def apply_hald(img, clut, n):
    """img: H×W×3 (0–1)。四面体插值。"""
    import numpy as np
    x = img * (n - 1)
    i0 = np.clip(np.floor(x).astype(int), 0, n - 2)
    f = x - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]

    def c(dr, dg, db):
        return clut[b0 + db, g0 + dg, r0 + dr]

    c000, c111 = c(0, 0, 0), c(1, 1, 1)
    out = np.zeros_like(img)
    conds = [
        ((fr >= fg) & (fg >= fb), lambda: (1 - fr) * c000 + (fr - fg) * c(1, 0, 0) + (fg - fb) * c(1, 1, 0) + fb * c111),
        ((fr >= fb) & (fb > fg), lambda: (1 - fr) * c000 + (fr - fb) * c(1, 0, 0) + (fb - fg) * c(1, 0, 1) + fg * c111),
        ((fb > fr) & (fr >= fg), lambda: (1 - fb) * c000 + (fb - fr) * c(0, 0, 1) + (fr - fg) * c(1, 0, 1) + fg * c111),
        ((fg > fr) & (fr >= fb), lambda: (1 - fg) * c000 + (fg - fr) * c(0, 1, 0) + (fr - fb) * c(1, 1, 0) + fb * c111),
        ((fg >= fb) & (fb > fr), lambda: (1 - fg) * c000 + (fg - fb) * c(0, 1, 0) + (fb - fr) * c(0, 1, 1) + fr * c111),
        ((fb > fg) & (fg > fr), lambda: (1 - fb) * c000 + (fb - fg) * c(0, 0, 1) + (fg - fr) * c(0, 1, 1) + fr * c111),
    ]
    done = np.zeros(img.shape[:2] + (1,), bool)
    for m, fn in conds:
        m = m & ~done
        out = np.where(m, fn(), out)
        done |= m
    return np.clip(out, 0, 1)


def preview(luts, frames, dst, cell, base=True):
    import numpy as np
    from PIL import Image, ImageDraw
    cw, chh = cell, round(cell * 9 / 16)
    cols = (["技术还原"] if base else []) + [Path(p).stem for p in luts]
    cluts = [load_hald(p) for p in luts]
    font, cjk = L.find_font(max(14, cell // 24))
    sheet = Image.new("RGB", (cw * len(cols), chh * len(frames)), (10, 10, 10))
    dr = ImageDraw.Draw(sheet)
    for ri, fp in enumerate(frames):
        im = Image.open(fp).convert("RGB")
        im = im.resize((cw, chh), Image.LANCZOS) if im.size != (cw, chh) else im
        a = np.asarray(im, dtype=np.float64) / 255.0
        cells = ([a] if base else []) + [apply_hald(a, cl, n) for cl, n in cluts]
        for ci, arr in enumerate(cells):
            sheet.paste(Image.fromarray((arr * 255 + 0.5).clip(0, 255).astype("uint8")), (cw * ci, chh * ri))
            label = L.safe_text("%s · %s" % (cols[ci], Path(fp).stem) if ci == 0 else cols[ci], cjk)
            tw = dr.textlength(label, font=font) if hasattr(dr, "textlength") else 120
            dr.rectangle([cw * ci, chh * ri, cw * ci + tw + 12, chh * ri + font.size + 10], fill=(0, 0, 0))
            dr.text((cw * ci + 6, chh * ri + 4), label, fill=(255, 220, 0), font=font)
    dst.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dst, quality=90)
    print("✓ %s(%d 帧 × %d 列)" % (dst, len(frames), len(cols)))


def main():
    ap = argparse.ArgumentParser(description="HALD 色表:build / preview / list")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("name", nargs="?")
    b.add_argument("chain", nargs="?")
    b.add_argument("--out", default="work/looks")
    b.add_argument("--note", default="")
    b.add_argument("--builtin", action="store_true", help="三套内置全出")
    p = sub.add_parser("preview")
    p.add_argument("luts", nargs="+")
    p.add_argument("--frames", nargs="+", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--cell", type=int, default=480, help="每格宽度")
    p.add_argument("--no-base", action="store_true", help="不放技术还原那一列")
    sub.add_parser("list")
    a = ap.parse_args()

    if a.cmd == "list":
        for k, (chain, note) in BUILTIN.items():
            print("## %s\n%s\n%s\n" % (k, note, chain))
        return
    if a.cmd == "build":
        out = Path(a.out)
        if a.builtin:
            for k, (chain, note) in BUILTIN.items():
                build(k, chain, out, note)
            return
        if not a.name:
            ap.error("build 要给名字(或 --builtin)")
        if a.chain:
            build(a.name, a.chain, out, a.note)
        elif a.name in BUILTIN:
            chain, note = BUILTIN[a.name]
            build(a.name, chain, out, a.note or note)
        else:
            ap.error("%s 不是内置色表(A / B / A2),要给滤镜链" % a.name)
        return
    if a.cmd == "preview":
        dst = Path(a.out) if a.out else Path(a.luts[0]).parent / "preview.jpg"
        preview(a.luts, a.frames, dst, a.cell, base=not a.no_base)


if __name__ == "__main__":
    main()
