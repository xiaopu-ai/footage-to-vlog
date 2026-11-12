#!/usr/bin/env python3
"""inventory.py · 素材盘点:扫一批实拍素材,判机型曲线,出 footage.json、联系表、镜头日志模板和对账报告。

用法:
  python3 scripts/inventory.py <footage_root> --out <proj>/work [--include 子目录 …] [--exclude 'glob' …]
                               [--frames 6] [--per-sheet 10] [--force] [--no-contact] [--no-queue]
  python3 scripts/inventory.py <footage_root> --out <proj>/work --retry-failed     # 只重做上次失败的,沿用上次的 --include / --exclude
  会自动钻进 scripts/queue.sh 排队(抽帧是重活)。

扫哪些(🔴 素材根目录下常混着成片、别的剪辑项目、代理,先用 --include 圈定素材子目录):
  --include 子目录 …   白名单:只扫这些子目录(相对素材根目录,可多个、可嵌套,如 航拍 0304-静态摆拍/A 机)。不给就扫全部。
  --exclude 'glob'     跳过匹配的相对路径(fnmatch,可多次),如 '成片/*'。
  自动跳过:以 . 或 _ 开头的目录(比如 _proxy)、node_modules、--out 目录、像剪辑项目的目录(有 project.json 且有
  edit.json 或 src/timeline.json)、.LRV 代理、以 . 开头的文件(比如 exFAT 上的 ._C0001.MP4)。目录名像成片 / 输出
  (成片、out、output、export、render…)时只提示不跳过。

对账(🔴 不再「部分失败也打印完成」):
  discovered 扫到的视频(在扫描范围内)= analyzed 分析成功(含沿用缓存)+ failed 分析失败;excluded 被规则跳过的单列,不进分母。
  联系表某条素材一帧都没抽出来,记为 contact_failed(素材已分析成功,但联系表上看不到它)。
  有 failed 或 contact_failed → 退出码 2(incomplete),失败清单和原因写进 <out>/inventory-failed.json;修好后加 --retry-failed 重跑。
  全部成功 → 退出码 0。参数错误、--out 写不进 → 退出码 1。
  普通重跑也会重试失败的(它们不在 footage.json 里、联系表失败的不缓存);--retry-failed 另外沿用上次的扫描范围,只重做失败的那几条。

缓存:硬解自测结果默认放 <out>/cache;写不进就依次换用户级缓存、只存内存,只提示,不会让分析失败(见 ftvlib.py 文件头)。

产物(都在 --out 下):
  footage.json        数组,每条分析成功的素材一项,字段见 references/edit-schema.md 第 3 节;另有:
                      fps_rational、rotation、matrix(解码用的 YCbCr 矩阵)、profile_confidence(high/medium/low)、
                      codes(抽帧量的原始码值 p1/p5/p50/p95/p99,0–1 全码值归一)、sony_meta、contact、size / mtime(缓存用)
  inventory-failed.json   对账报告(每次都写,没失败时 failed 为空):
                      {"status": "complete"|"incomplete", "counts": {discovered, analyzed, cached, failed, contact_failed, excluded},
                       "failed": [{path, stage: stat|analyze|contact, reason}], "excluded": [{path, kind: dir|file, reason}],
                       "scan": {root, include, exclude, discovered: [相对路径…]}, "cache": 缓存位置, "generated": 时间}
  contact/<文件夹>_<n>.jpg   联系表:每条素材一行 6 帧(已套技术 LUT,不带风格),行头写文件名 / 时长 / 帧率 / 曲线,
                      每帧标源时间码(m:ss.s);每张最多 --per-sheet 条;n 从 1 开始
  contact/.frames/    每条素材那一行的缓存(素材没变就不重抽;有抽帧失败的行不缓存)
  shotlog.md          镜头日志模板,每条素材一节;已存在时只追加新素材的小节,不动已填的内容

判机型曲线(profile):
  1. Sony:读文件内嵌的 NonRealTimeMeta XML(CaptureGammaEquation),s-log3 → sony-slog3,709 / S-Cinetone → rec709;
  2. 大疆:encoder 元数据认机型;10-bit 且码值像 Log(黑位抬高、高光压缩、p1–p99 跨度小)→ dji-dlogm,否则 rec709;
     元数据分不出 D-Log / D-Log M,按 D-Log M(Mavic 4 Pro 实测);
  3. 其他:码值像 Log 的标 unknown-log(low),要在 project.json 的 profiles / luts 里指定;否则 rec709。
  Sony 有内嵌元数据只抽 1 帧,其余抽 3 帧(25% / 50% / 75%)量码值;要靠码值判断却一帧都没抽到的,算分析失败。
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
REPORT = "inventory-failed.json"
SUSPECT_DIRS = {"成片", "输出", "导出", "交付", "out", "output", "outputs", "export", "exports", "render", "renders", "deliver",
                "delivery"}


def fatal(msg):
    L.die(msg, 1)  # 退出码 2 留给「盘点不完整」


def norm_includes(root, incs):
    out = []
    for x in incs or []:
        r = Path(x.strip().rstrip("/\\"))
        if r.is_absolute():
            try:
                r = Path(os.path.realpath(r)).relative_to(root)
            except ValueError:
                fatal("--include %s 不在素材根目录 %s 里" % (x, root))
        rel = r.as_posix().strip("/")
        if rel in ("", "."):
            fatal("--include 写的是素材根目录本身(%s):不用写 --include 就是扫全部" % x)
        if ".." in Path(rel).parts:
            fatal("--include %s 不能用 ..(只能是素材根目录下的子目录)" % x)
        if not (root / rel).exists():
            fatal("--include %s 不存在(%s)" % (x, root / rel))
        out.append(rel)
    return sorted(set(out))


def looks_like_project(d):
    return (d / "project.json").is_file() and ((d / "edit.json").is_file() or (d / "src" / "timeline.json").is_file())


def walk(root, exclude, skip_dir, include):
    """→ (rels, excluded)。rels:扫描范围内的视频相对路径;excluded:[{path, kind, reason}](被规则跳过的,目录不展开)。"""
    rels, excluded = [], []

    def in_inc(rel):
        return not include or any(rel == i or rel.startswith(i + "/") for i in include)

    def toward_inc(rel):  # rel 是某个 include 的上级目录(要进去才能走到它)
        return any(i.startswith(rel + "/") for i in include)

    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        here = dp.relative_to(root).as_posix()
        here = "" if here == "." else here
        keep = []
        for d in sorted(dirnames):
            rel = (here + "/" + d) if here else d
            full = dp / d
            reason = None
            if d.startswith("."):
                reason = "隐藏目录"
            elif d.startswith("_"):
                reason = "以 _ 开头(代理 / 草稿)"
            elif full.resolve() == skip_dir:
                reason = "盘点输出目录(--out)"
            elif d == "node_modules":
                reason = "node_modules"
            elif looks_like_project(full):
                reason = "像剪辑项目目录(有 project.json + edit.json / timeline.json)"
            elif include and not (in_inc(rel) or toward_inc(rel)):
                reason = "不在 --include 里"
            if reason:
                excluded.append({"path": rel + "/", "kind": "dir", "reason": reason})
                continue
            if d.lower() in SUSPECT_DIRS and not include:
                L.warn("目录 %s/ 名字像成片 / 输出,确认里面是素材;不是就用 --include 圈定素材目录,或 --exclude '%s/*'" % (rel, rel))
            keep.append(d)
        dirnames[:] = keep
        for fn in sorted(filenames):
            suf = Path(fn).suffix.lower()
            if suf not in L.VIDEO_EXT and suf != ".lrv":
                continue
            rel = (here + "/" + fn) if here else fn
            reason = None
            if fn.startswith("."):
                reason = "隐藏文件(比如 exFAT 的 ._ 元数据)"
            elif suf == ".lrv":
                reason = "低码率代理 .LRV"
            elif not in_inc(rel):
                reason = "不在 --include 里"
            elif any(fnmatch.fnmatch(rel, pat) for pat in exclude):
                reason = "匹配 --exclude"
            if reason:
                excluded.append({"path": rel, "kind": "file", "reason": reason})
                continue
            rels.append(rel)
    return rels, excluded


def group_of(rel, root):
    parent = Path(rel).parent.as_posix()
    return Path(root).name if parent in ("", ".") else parent.replace("/", "_")


def strip_for(e, root, out, nframes, cache_ok):
    """一条素材的 6 帧缩略图(已套技术 LUT)拼成一行,缓存在 contact/.frames/。
    返回 (PIL.Image, [时间], [失败原因…])。有帧失败的行不写缓存,下次重抽。"""
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
        try:
            return Image.open(cf).convert("RGB"), ts, []
        except OSError:
            pass
    ent = dict(e, _abs=str(Path(root) / e["path"]))
    hw = L.hw_candidate(ent) and L.use_hwaccel(ent["_abs"], ent, lut)
    row = Image.new("RGB", (TW * nframes, TH), (20, 20, 20))
    font, cjk = L.find_font(15)
    errs = []
    for k, t in enumerate(ts):
        t = max(0.0, min(t, d - 0.05)) if d else 0.0
        try:
            a = L.rgb_frame(ent, t, TW, TH, lut, hw=hw)
            im = Image.fromarray((a * 255 + 0.5).clip(0, 255).astype("uint8"))
        except RuntimeError as ex:
            errs.append(str(ex)[-300:])
            L.warn(str(ex))
            im = Image.new("RGB", (TW, TH), (60, 0, 0))
        dr = ImageDraw.Draw(im)
        label = L.tc(t)
        bw = dr.textlength(label, font=font) if hasattr(dr, "textlength") else 60
        dr.rectangle([0, TH - 21, bw + 10, TH], fill=(0, 0, 0))
        dr.text((5, TH - 19), label, fill=(255, 220, 0), font=font)
        row.paste(im, (TW * k, 0))
    if not errs:
        try:
            cf.parent.mkdir(parents=True, exist_ok=True)
            row.save(cf, quality=88)
        except OSError as ex:
            L.warn("联系表缓存写不进(%s),不影响本次结果" % ex)
    return row, ts, errs


def header_text(e, idx):
    spec = "%s · %.1f s · %sp · %d×%d · %s · %s" % (
        e["path"], e.get("duration") or 0, ("%g" % e["fps"]) if e.get("fps") else "?", e.get("width") or 0,
        e.get("height") or 0, e["profile"], "有声" if e.get("has_audio") else "无声")
    if e.get("profile_confidence") == "low":
        spec += " · ⚠️待确认"
    return "%02d  %s" % (idx, spec)


def build_sheets(items, root, out, nframes, per_sheet, cache_ok, nocache=()):
    """→ (联系表文件名列表, {素材: 失败原因})。某条素材一帧都没抽出来 = 联系表失败;部分失败只提示。"""
    from PIL import Image, ImageDraw
    cdir = out / "contact"
    cdir.mkdir(parents=True, exist_ok=True)
    for old in cdir.glob("*.jpg"):
        old.unlink()
    font, cjk = L.find_font(17)
    groups = {}
    for e in items:
        groups.setdefault(group_of(e["path"], root), []).append(e)
    made, bad = [], {}
    for g, es in groups.items():
        for si in range(0, len(es), per_sheet):
            part = es[si:si + per_sheet]
            sheet = Image.new("RGB", (TW * nframes, (TH + HEAD) * len(part)), (12, 12, 12))
            dr = ImageDraw.Draw(sheet)
            name = "%s_%d.jpg" % (g, si // per_sheet + 1)
            for j, e in enumerate(part):
                try:
                    row, ts, errs = strip_for(e, root, out, nframes, cache_ok and e["path"] not in nocache)
                except Exception as ex:  # noqa: BLE001  一条出错不拖垮整张联系表,但要记账
                    row, ts, errs = Image.new("RGB", (TW * nframes, TH), (60, 0, 0)), [], ["%s: %s" % (type(ex).__name__, ex)]
                if errs and len(errs) >= max(1, len(ts)):
                    bad[e["path"]] = errs[-1]
                elif errs:
                    L.warn("%s 联系表 %d / %d 帧没抽出来(红块):%s" % (e["path"], len(errs), len(ts), errs[-1][-160:]))
                y = (TH + HEAD) * j
                dr.rectangle([0, y, TW * nframes, y + HEAD - 1], fill=(36, 36, 36) if e.get("profile_confidence") != "low" else (90, 40, 0))
                dr.text((8, y + 5), L.safe_text(header_text(e, j + 1), cjk), fill=(235, 235, 235), font=font)
                sheet.paste(row, (0, y + HEAD))
                e["contact"] = "contact/%s#%d" % (name, j + 1)
            sheet.save(cdir / name, quality=85)
            made.append(name)
            print("  ✓ contact/%s(%d 条)" % (name, len(part)), flush=True)
    return made, bad


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


def write_json(path, obj):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="素材盘点:footage.json + 联系表 + 镜头日志模板 + 对账报告")
    ap.add_argument("root")
    ap.add_argument("--out", default="work", help="输出目录(一般是 <proj>/work)")
    ap.add_argument("--include", nargs="+", action="extend", default=[], metavar="子目录",
                    help="白名单:只扫这些子目录(相对素材根目录)")
    ap.add_argument("--exclude", action="append", default=[], help="跳过的相对路径 glob,可多次")
    ap.add_argument("--frames", type=int, default=6, help="联系表每条抽几帧")
    ap.add_argument("--per-sheet", type=int, default=10, help="每张联系表最多几条")
    ap.add_argument("--force", action="store_true", help="不用缓存,全部重新分析、重抽缩略图")
    ap.add_argument("--retry-failed", action="store_true", help="只重做上次 inventory-failed.json 里失败的,沿用上次的扫描范围")
    ap.add_argument("--no-contact", action="store_true", help="只出 footage.json 和 shotlog.md")
    ap.add_argument("--no-queue", action="store_true", help="不自动排队")
    a = ap.parse_args()
    L.ensure_queued()

    root = Path(os.path.realpath(os.path.expanduser(a.root)))
    out = Path(a.out).resolve()
    if not root.is_dir():
        fatal("素材目录不存在:%s" % root)
    ok, why = L.writable_dir(out)
    if not ok:
        fatal("--out 目录写不进:%s(%s)。footage.json、联系表、对账报告都写在这里" % (out, why))
    L.set_cache_dir(out / "cache")
    cd = L.cache_dir()
    print("缓存:%s" % (cd if cd else "只存内存(原因见上一行)"), flush=True)

    rep_path = out / REPORT
    retry = set()
    include, exclude = a.include, a.exclude
    if a.retry_failed:
        if not rep_path.exists():
            fatal("没有 %s:先完整盘点一次" % rep_path)
        prev = json.loads(rep_path.read_text("utf-8"))
        scan = prev.get("scan") or {}
        if scan.get("root") and Path(scan["root"]) != root:
            fatal("上次盘点的素材根目录是 %s,这次是 %s;--retry-failed 只能重做同一批" % (scan["root"], root))
        include = include or scan.get("include") or []
        exclude = exclude or scan.get("exclude") or []
        retry = {f["path"] for f in prev.get("failed", [])}
        if not retry:
            print("上次盘点没有失败项(%s),照常核对一遍" % rep_path.name)
        else:
            print("重做上次失败的 %d 条(沿用 --include %s)" % (len(retry), " ".join(include) or "全部"), flush=True)
    include = norm_includes(root, include)

    fj = out / "footage.json"
    old = {}
    if fj.exists() and not a.force:
        try:
            data = json.loads(fj.read_text("utf-8"))
            old = {e["path"]: e for e in (data["items"] if isinstance(data, dict) else data)}
        except (ValueError, KeyError, TypeError) as ex:
            L.warn("footage.json 读不了(%s),全部重新分析" % ex)

    t0 = time.time()
    rels, excluded = walk(root, exclude, out, include)
    print("扫到 %d 条视频(%s%s);规则跳过 %d 项" % (len(rels), root, (" · 只扫 " + "、".join(include)) if include else "",
                                             len(excluded)), flush=True)
    items, failed = [], []
    cached = 0
    for i, rel in enumerate(rels, 1):
        p = root / rel
        t1 = time.time()
        try:
            st = p.stat()
        except OSError as ex:
            failed.append({"path": rel, "stage": "stat", "reason": "读不了文件信息:%s" % ex})
            print("[%3d/%d] %-52s ✗ 读不了:%s" % (i, len(rels), rel, ex), flush=True)
            continue
        c = old.get(rel)
        if c and rel not in retry and c.get("size") == st.st_size and c.get("mtime") == int(st.st_mtime):
            e, how = c, "缓存"
            cached += 1
        else:
            try:
                e, how = L.analyze(p, rel=rel), "分析"
            except Exception as ex:  # noqa: BLE001  坏文件不拖垮整批,但必须记账
                reason = ("%s: %s" % (type(ex).__name__, ex)).strip()[-500:]
                failed.append({"path": rel, "stage": "analyze", "reason": reason})
                print("[%3d/%d] %-52s ✗ 分析失败 %.1fs:%s" % (i, len(rels), rel, time.time() - t1, reason.splitlines()[0][:160]),
                      flush=True)
                continue
        items.append(e)
        print("[%3d/%d] %-52s %-11s %-6s %s %.1fs" % (i, len(rels), rel, e["profile"], e.get("profile_confidence", ""),
                                                     how, time.time() - t1), flush=True)
    t_an = time.time() - t0
    assert len(rels) == len(items) + len(failed), "对账不平"

    contact_bad = {}
    if not a.no_contact and items:
        print("出联系表…", flush=True)
        _, contact_bad = build_sheets(items, root, out, a.frames, a.per_sheet, cache_ok=not a.force, nocache=retry)
    t_ct = time.time() - t0 - t_an
    for path, why in sorted(contact_bad.items()):
        failed.append({"path": path, "stage": "contact", "reason": "联系表一帧都没抽出来:%s" % why})

    items.sort(key=lambda e: e["path"])
    write_json(fj, items)
    sl = write_shotlog(out, items, root)

    n_fail = sum(1 for f in failed if f["stage"] != "contact")
    complete = not failed
    counts = {"discovered": len(rels), "analyzed": len(items), "cached": cached, "failed": n_fail,
              "contact_failed": len(contact_bad), "excluded": len(excluded)}
    write_json(rep_path, {
        "generated": datetime.now().isoformat(timespec="seconds"), "status": "complete" if complete else "incomplete",
        "counts": counts, "failed": failed, "excluded": excluded, "cache": L.cache_desc(),
        "scan": {"root": str(root), "include": include, "exclude": exclude, "discovered": rels},
    })

    by = {}
    for e in items:
        by.setdefault(e["profile"], []).append(e)
    print("\n对账:发现 %d 条 = 分析成功 %d 条(其中沿用缓存 %d)+ 失败 %d 条;规则跳过 %d 项(不计入);联系表失败 %d 条"
          % (counts["discovered"], counts["analyzed"], cached, n_fail, counts["excluded"], counts["contact_failed"]))
    print("footage.json %d 条 · shotlog.md %s · 分析 %.0f s · 联系表 %.0f s" % (len(items), sl, t_an, t_ct))
    for k, es in sorted(by.items()):
        print("  %-12s %3d 条(%s)" % (k, len(es), ", ".join(sorted({e.get('profile_confidence', '?') for e in es}))))
    if excluded:
        kinds = {}
        for x in excluded:
            kinds.setdefault(x["reason"], []).append(x["path"])
        for why, ps in sorted(kinds.items()):
            print("  跳过 %d 项(%s):%s%s" % (len(ps), why, "、".join(ps[:4]), " …" if len(ps) > 4 else ""))
    low = [e for e in items if e.get("profile_confidence") == "low" or e["profile"] not in ("sony-slog3", "dji-dlogm", "rec709")]
    for e in low:
        print("  ⚠️ 需确认 %s → %s:%s" % (e["path"], e["profile"], e["profile_reason"]))
    odd = [e for e in items if abs(e.get("rotation") or 0) % 180 == 90 or
           abs((e.get("width") or 16) / max(1, e.get("height") or 9) / (16 / 9) - 1) > 0.2]
    for e in odd:
        print("  ⚠️ 非横版 16:9:%s(%d×%d,旋转 %d°),切片段时会两边补黑" % (e["path"], e["width"], e["height"], e.get("rotation") or 0))

    if not complete:
        print("\n✗ 盘点不完整(退出码 2):%d 条分析失败、%d 条联系表失败,清单和原因见 %s" % (n_fail, len(contact_bad), rep_path))
        for f in failed[:12]:
            print("  ✗ [%s] %s:%s" % (f["stage"], f["path"], f["reason"].splitlines()[0][:200]))
        if len(failed) > 12:
            print("  … 还有 %d 条" % (len(failed) - 12))
        print("  修好原因后重跑:python3 %s %s --out %s --retry-failed" % (os.path.abspath(__file__), a.root, a.out))
        print("  🔴 不要拿不完整的盘点去定方案:先补齐,或明确告诉用户哪些素材没进来")
        sys.exit(2)
    print("\n✓ 盘点完整:%d / %d 条都已分析,报告 %s" % (len(items), len(rels), rep_path))


if __name__ == "__main__":
    main()
