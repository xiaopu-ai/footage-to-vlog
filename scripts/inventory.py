#!/usr/bin/env python3
"""inventory.py · 素材盘点:扫一批实拍素材,判机型曲线,出 footage.json、联系表和镜头日志模板。

用法:
  python3 scripts/inventory.py <footage_root> --out <proj>/work [--frames 6] [--per-sheet 10]
                               [--exclude 'glob' …] [--force] [--no-contact] [--no-queue]
  会自动钻进 scripts/queue.sh 排队(抽帧是重活);以 . 或 _ 开头的目录(比如 _proxy)不扫,.LRV 代理不扫。

产物(都在 --out 下):
  footage.json        数组,每条素材一项,字段见 references/edit-schema.md 第 3 节;另有:
                      fps_rational、rotation、matrix(解码用的 YCbCr 矩阵)、profile_confidence(high/medium/low)、
                      codes(抽帧量的原始码值 p1/p5/p50/p95/p99,0–1 全码值归一)、sony_meta、contact、size / mtime(缓存用)
  contact/<文件夹>_<n>.jpg   联系表:每条素材一行 6 帧(已套技术 LUT,不带风格),行头写文件名 / 时长 / 帧率 / 曲线,
                      每帧标源时间码(m:ss.s);每张最多 --per-sheet 条;n 从 1 开始
  contact/.frames/    每条素材那一行的缓存(素材没变就不重抽)
  shotlog.md          镜头日志模板,每条素材一节;已存在时只追加新素材的小节,不动已填的内容

判机型曲线(profile):
  1. Sony:读文件内嵌的 NonRealTimeMeta XML(CaptureGammaEquation),s-log3 → sony-slog3,709 / S-Cinetone → rec709;
  2. 大疆:encoder 元数据认机型;10-bit 且码值像 Log(黑位抬高、高光压缩、p1–p99 跨度小)→ dji-dlogm,否则 rec709;
     元数据分不出 D-Log / D-Log M,按 D-Log M(Mavic 4 Pro 实测);
  3. 其他:码值像 Log 的标 unknown-log(low),要在 project.json 的 profiles / luts 里指定;否则 rec709。
  Sony 有内嵌元数据只抽 1 帧,其余抽 3 帧(25% / 50% / 75%)量码值。抽帧都用 -ss 放在 -i 前快速定位。
  认错了就在 project.json 的 profiles 里改,make_clips / grab 都以那里为准。
"""
import argparse
import fnmatch
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ftvlib as L  # noqa: E402

TW, TH, HEAD = 320, 180, 30


def walk(root, exclude, skip_dir):
    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if not d.startswith((".", "_"))
                             and (dp / d).resolve() != skip_dir)
        for fn in sorted(filenames):
            if fn.startswith(".") or Path(fn).suffix.lower() not in L.VIDEO_EXT:
                continue
            rel = (dp / fn).relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel, pat) for pat in exclude):
                continue
            yield rel


def group_of(rel, root):
    parent = Path(rel).parent.as_posix()
    return Path(root).name if parent in ("", ".") else parent.replace("/", "_")


def strip_for(e, root, out, nframes, cache_ok):
    """一条素材的 6 帧缩略图(已套技术 LUT)拼成一行,缓存在 contact/.frames/。返回 (PIL.Image, [时间])。"""
    from PIL import Image, ImageDraw
    d = e.get("duration") or 0
    ts = [d * (0.04 + 0.92 * k / max(1, nframes - 1)) for k in range(nframes)] if nframes > 1 else [d / 2]
    lut = None
    try:
        lut = L.resolve_lut(None, {}, e["profile"])
    except RuntimeError:
        lut = None  # 没有内置 LUT 的曲线(unknown-log / hlg …)先看原始画面
    # 缓存键用缩略图实际用的滤镜链:链没变(LUT、范围、矩阵、缩放参数都没变)就不重抽
    key = L.sha1_obj([e["path"], e.get("size"), e.get("mtime"), L.tech_chain(e, TW, TH, lut), nframes])[:10]
    safe = re.sub(r"[^\w.-]+", "_", e["path"])[-80:]
    cf = out / "contact" / ".frames" / ("%s_%s.jpg" % (safe, key))
    if cache_ok and cf.exists():
        return Image.open(cf).convert("RGB"), ts
    ent = dict(e, _abs=str(Path(root) / e["path"]))
    hw = L.hw_candidate(ent) and L.use_hwaccel(ent["_abs"], ent, lut)
    row = Image.new("RGB", (TW * nframes, TH), (20, 20, 20))
    font, cjk = L.find_font(15)
    for k, t in enumerate(ts):
        t = max(0.0, min(t, d - 0.05)) if d else 0.0
        try:
            a = L.rgb_frame(ent, t, TW, TH, lut, hw=hw)
            im = Image.fromarray((a * 255 + 0.5).clip(0, 255).astype("uint8"))
        except RuntimeError as ex:
            L.warn(str(ex))
            im = Image.new("RGB", (TW, TH), (60, 0, 0))
        dr = ImageDraw.Draw(im)
        label = L.tc(t)
        bw = dr.textlength(label, font=font) if hasattr(dr, "textlength") else 60
        dr.rectangle([0, TH - 21, bw + 10, TH], fill=(0, 0, 0))
        dr.text((5, TH - 19), label, fill=(255, 220, 0), font=font)
        row.paste(im, (TW * k, 0))
    cf.parent.mkdir(parents=True, exist_ok=True)
    row.save(cf, quality=88)
    return row, ts


def header_text(e, idx):
    spec = "%s · %.1f s · %sp · %d×%d · %s · %s" % (
        e["path"], e.get("duration") or 0, ("%g" % e["fps"]) if e.get("fps") else "?", e.get("width") or 0,
        e.get("height") or 0, e["profile"], "有声" if e.get("has_audio") else "无声")
    if e.get("profile_confidence") == "low":
        spec += " · ⚠️待确认"
    return "%02d  %s" % (idx, spec)


def build_sheets(items, root, out, nframes, per_sheet, cache_ok):
    from PIL import Image, ImageDraw
    cdir = out / "contact"
    cdir.mkdir(parents=True, exist_ok=True)
    for old in cdir.glob("*.jpg"):
        old.unlink()
    font, cjk = L.find_font(17)
    groups = {}
    for e in items:
        groups.setdefault(group_of(e["path"], root), []).append(e)
    made = []
    for g, es in groups.items():
        for si in range(0, len(es), per_sheet):
            part = es[si:si + per_sheet]
            sheet = Image.new("RGB", (TW * nframes, (TH + HEAD) * len(part)), (12, 12, 12))
            dr = ImageDraw.Draw(sheet)
            name = "%s_%d.jpg" % (g, si // per_sheet + 1)
            for j, e in enumerate(part):
                row, _ = strip_for(e, root, out, nframes, cache_ok)
                y = (TH + HEAD) * j
                dr.rectangle([0, y, TW * nframes, y + HEAD - 1], fill=(36, 36, 36) if e.get("profile_confidence") != "low" else (90, 40, 0))
                dr.text((8, y + 5), L.safe_text(header_text(e, j + 1), cjk), fill=(235, 235, 235), font=font)
                sheet.paste(row, (0, y + HEAD))
                e["contact"] = "contact/%s#%d" % (name, j + 1)
            sheet.save(cdir / name, quality=85)
            made.append(name)
            print("  ✓ contact/%s(%d 条)" % (name, len(part)), flush=True)
    return made


SHOTLOG_HEAD = """# 镜头日志

素材根目录:`{root}` · 共 {n} 条 · 模板生成于 {date}(inventory.py)

填写约定:
- **可用时刻**写源素材秒数(和 edit.json 镜头的 `in` 同一口径),一行一段,例:`12.4–15.0 正俯拍 S 形尾迹,无商标`。
- **问题**写在哪一秒、是什么;没有就写「无」。合规红线:二维码、电话、地图 / 地球仪、第三方商标、车牌 / 船号、路人正脸。
- **适合风格**从 testimonial(人物访谈)/ launch(产品发布)/ hype(卡点燃剪)/ manifesto(口号宣言)里挑,可多选。
- 联系表在 `contact/`,`#n` 是第几行。
"""


def shotlog_section(e):
    spec = "%.1f s · %sp · %d×%d · %s(%s)· %s · %s" % (
        e.get("duration") or 0, ("%g" % e["fps"]) if e.get("fps") else "?", e.get("width") or 0, e.get("height") or 0,
        e["profile"], e.get("profile_confidence", "?"), "有声" if e.get("has_audio") else "无声", (e.get("created") or "")[:16])
    return """
## {path}
- 规格:{spec} · 联系表 {contact}
- 内容:
- 可用时刻:
- 问题(商标 / 船号 / 第三方品牌 / 二维码 / 人脸):
- 适合风格(testimonial / launch / hype / manifesto):
""".format(path=e["path"], spec=spec, contact=e.get("contact", "—"))


def write_shotlog(out, items, root):
    f = out / "shotlog.md"
    if f.exists():
        txt = f.read_text("utf-8")
        have = set(re.findall(r"^## (.+?)\s*$", txt, re.M))
        new = [e for e in items if e["path"] not in have]
        if new:
            with open(f, "a", encoding="utf-8") as fh:
                fh.write("\n<!-- %s 追加 %d 条新素材 -->\n" % (datetime.now().strftime("%Y-%m-%d %H:%M"), len(new)))
                for e in new:
                    fh.write(shotlog_section(e))
        return "追加 %d 节" % len(new)
    body = SHOTLOG_HEAD.format(root=root, n=len(items), date=datetime.now().strftime("%Y-%m-%d"))
    body += "".join(shotlog_section(e) for e in items)
    f.write_text(body, "utf-8")
    return "新建 %d 节" % len(items)


def main():
    ap = argparse.ArgumentParser(description="素材盘点:footage.json + 联系表 + 镜头日志模板")
    ap.add_argument("root")
    ap.add_argument("--out", default="work", help="输出目录(一般是 <proj>/work)")
    ap.add_argument("--frames", type=int, default=6, help="联系表每条抽几帧")
    ap.add_argument("--per-sheet", type=int, default=10, help="每张联系表最多几条")
    ap.add_argument("--exclude", action="append", default=[], help="跳过的相对路径 glob,可多次")
    ap.add_argument("--force", action="store_true", help="不用缓存,全部重新分析、重抽缩略图")
    ap.add_argument("--no-contact", action="store_true", help="只出 footage.json 和 shotlog.md")
    ap.add_argument("--no-queue", action="store_true", help="不自动排队")
    a = ap.parse_args()
    L.ensure_queued()

    root = Path(os.path.expanduser(a.root)).resolve()
    out = Path(a.out).resolve()
    if not root.is_dir():
        L.die("素材目录不存在:%s" % root)
    out.mkdir(parents=True, exist_ok=True)
    fj = out / "footage.json"
    old = {}
    if fj.exists() and not a.force:
        data = json.loads(fj.read_text("utf-8"))
        old = {e["path"]: e for e in (data["items"] if isinstance(data, dict) else data)}

    t0 = time.time()
    rels = list(walk(root, a.exclude, out))
    print("扫到 %d 条视频(%s)" % (len(rels), root), flush=True)
    items = []
    for i, rel in enumerate(rels, 1):
        p = root / rel
        st = p.stat()
        c = old.get(rel)
        t1 = time.time()
        if c and c.get("size") == st.st_size and c.get("mtime") == int(st.st_mtime):
            e, how = c, "缓存"
        else:
            try:
                e, how = L.analyze(p, rel=rel), "分析"
            except Exception as ex:  # noqa: BLE001  坏文件不拖垮整批
                L.warn("%s 读不了,跳过:%s" % (rel, ex))
                continue
        items.append(e)
        print("[%3d/%d] %-52s %-11s %-6s %s %.1fs" % (i, len(rels), rel, e["profile"], e.get("profile_confidence", ""),
                                                     how, time.time() - t1), flush=True)
    t_an = time.time() - t0

    if not a.no_contact and items:
        print("出联系表…", flush=True)
        build_sheets(items, root, out, a.frames, a.per_sheet, cache_ok=not a.force)
    t_ct = time.time() - t0 - t_an

    items.sort(key=lambda e: e["path"])
    tmp = fj.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, fj)
    sl = write_shotlog(out, items, root)

    by = {}
    for e in items:
        by.setdefault(e["profile"], []).append(e)
    print("\n✓ footage.json %d 条 · shotlog.md %s · 分析 %.0f s · 联系表 %.0f s" % (len(items), sl, t_an, t_ct))
    for k, es in sorted(by.items()):
        print("  %-12s %3d 条(%s)" % (k, len(es), ", ".join(sorted({e.get('profile_confidence', '?') for e in es}))))
    low = [e for e in items if e.get("profile_confidence") == "low" or e["profile"] not in ("sony-slog3", "dji-dlogm", "rec709")]
    for e in low:
        print("  ⚠️ 需确认 %s → %s:%s" % (e["path"], e["profile"], e["profile_reason"]))
    odd = [e for e in items if abs(e.get("rotation") or 0) % 180 == 90 or
           abs((e.get("width") or 16) / max(1, e.get("height") or 9) / (16 / 9) - 1) > 0.2]
    for e in odd:
        print("  ⚠️ 非横版 16:9:%s(%d×%d,旋转 %d°),切片段时会两边补黑" % (e["path"], e["width"], e["height"], e.get("rotation") or 0))


if __name__ == "__main__":
    main()
