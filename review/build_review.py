#!/usr/bin/env python3
"""从 src/timeline.json 生成审核台（风格帧 / 分镜）：选帧 → 模板 tools/stills.mjs 渲静帧 → 出图、拼转场图 → page.json → index.html。

用法：
  python3 review/build_review.py <proj> [--kind board|sf] [--frames-per-shot N] [--version v1] [--title "…"]
                                 [--notes notes.json] [--timeline 另一份.json] [--no-render] [--serve] [--port 8765]
  <proj>  从 template/ 复制出来的项目目录（有 src/timeline.json、tools/stills.mjs、node_modules）
  --kind board（默认）分镜：每个主轨镜头取「开场 / 每个文字或动作节拍落定后 / 落幅」+ 出场转场图
         sf         风格帧：每镜一张（文字最全、落定后的那一帧），不拼转场
  --frames-per-shot N  每镜（不算转场图）正好出 N 张：多了按时间均匀保留，少了在开场和落幅之间均匀补
  --notes  「本版改动」面板的条目（JSON 字符串数组，可带 <b>）
  --no-render  不重渲静帧，直接用上次的 _png/（只改文案时用）
  --serve  生成完直接起本地服务（前台运行，Ctrl+C 停）

产物：<proj>/work/review/<kind>/ 下的 index.html、page.json、frames/、thumbs/、_png/（原始静帧）；意见存在同目录 feedback.json。
服务：python3 review/serve_local.py <proj>/work/review/<kind> --port 8765，把 http://localhost:8765/ 给用户。

转场图：硬切 = 「左：上一镜最后一帧 | 右：本镜第一帧」；其余转场 = 转场窗口里均匀取 4 帧拼 2×2，按序号读。
🔴 本机同一时间只跑一个重任务：渲静帧前先把排队脚本路径放进环境变量 RENDER_QUEUE（stills.mjs 会自己排队），
   或者整条命令套排队脚本运行：<queue.sh> python3 review/build_review.py …
需要 Python 3.9+、Pillow；node 从 PATH 找（或设 NODE=/path/to/node）。
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import local_page  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    sys.exit("需要 Pillow：pip install pillow")

FPS = 30
TR_LEN = {"cut": 0, "dip": 8, "fade": 12, "whip": 8, "punch": 8, "sweep": 10, "wipe": 12}
TR_CN = {"cut": "硬切", "dip": "黑场过渡", "fade": "叠化", "whip": "甩镜", "punch": "冲击推镜", "sweep": "光扫", "wipe": "蒙版擦入"}
TR_DESC = {
    "dip": "上一镜淡到黑、这一镜从黑淡入",
    "fade": "两镜交叉叠化",
    "whip": "上一镜横向甩出带拖影，这一镜从反方向甩进",
    "punch": "上一镜加速推进 + 拖影，这一镜从 1.15 倍回落并提亮",
    "sweep": "暖色柔光带扫过，换镜藏在光带最亮处",
    "wipe": "这一镜用蒙版从左往右擦入，入镜画面轻推",
}
TEXT_CN = {"slam": "砸字", "brushword": "毛笔字", "chapter": "章名", "qcard": "问题卡", "lower": "人名条", "spec": "参数卡",
           "reveal": "光扫字标", "lockup": "落版", "endcard": "片尾 Logo", "image": "图片"}
MOVE_CN = {"none": "固定", "push": "缓推", "pull": "缓拉", "drift-left": "左移", "drift-right": "右移", "rise": "升起", "interview": "采访双机位"}
SF_PRIORITY = ["lockup", "qcard", "spec", "reveal", "slam", "chapter", "brushword", "lower", "endcard", "image"]


def tc(f: int) -> str:
    t = f / FPS
    m = int(t // 60)
    return f"{m}:{t - m * 60:05.2f}"


def norm_tr(t):
    """和模板 normalize.ts 一致：pre / post 由 timeline.py 写；缺省 pre = len//2、post = len − pre，punch 的 post = len"""
    if isinstance(t, str):
        t = {"type": t}
    t = t if isinstance(t, dict) else {}
    ty = t.get("type") if t.get("type") in TR_LEN else "cut"
    ln = t.get("len")
    ln = 0 if ty == "cut" else int(round(ln if isinstance(ln, (int, float)) else TR_LEN[ty]))
    pre = t.get("pre") if isinstance(t.get("pre"), (int, float)) else ln // 2
    post = t.get("post") if isinstance(t.get("post"), (int, float)) else (ln if ty == "punch" else ln - ln // 2)
    return {"type": ty, "len": ln, "pre": int(pre), "post": int(post)}


def text_summary(t: dict) -> str:
    ty = t.get("type")
    if ty in ("qcard", "lockup"):
        return " / ".join(str(x) for x in (t.get("lines") or [t.get("text", "")]))
    if ty == "lower":
        return str(t.get("name", ""))
    if ty == "spec":
        return f"{t.get('big', '')}{t.get('unit', '')} {t.get('label', '')}".strip()
    if ty == "endcard":
        return str(t.get("sub") or "Logo")
    if ty == "image":
        return os.path.basename(str(t.get("src", "")))
    if ty == "reveal" and t.get("image"):
        return os.path.basename(str(t["image"]))
    return str(t.get("text", ""))


def settle(t: dict) -> int:
    """文字出现后多少帧算「落定」（动画走完）"""
    ty, dur = t.get("type"), t["to"] - t["from"]
    if ty == "qcard":
        n = sum(len(str(x)) for x in (t.get("lines") or []))
        return min(int(12 + n * 1.2 + 10), dur - 18)
    return {"slam": 6, "brushword": 12, "chapter": 2, "lower": 16, "spec": 20 if dur < 70 else 30, "lockup": 62, "endcard": 26, "image": 10}.get(
        ty, int(t.get("delay", 4)) + int(t.get("sweep", 28 if t.get("image") else 24)) + 2 if ty == "reveal" else 8
    )


def plan(tl: dict, kind: str, per_shot: int):
    dur = int(tl["duration"])
    shots = [dict(s) for s in tl.get("shots", [])]
    for s in shots:
        s["layer"] = int(s.get("layer", 0) or 0)
        s["inTr"] = norm_tr(s.get("in_tr"))
    main = sorted([s for s in shots if s["layer"] == 0], key=lambda s: (s["cut"], s["from"]))
    if not main:
        sys.exit("timeline.json 里没有主轨（layer 0）镜头，审核台没东西可出")
    for i, s in enumerate(main):
        p = main[i - 1] if i else None
        s["prev"] = p if p and p["to"] >= s["from"] - 1 else None
    # 转场窗口以切点为中心（和模板 normalize.ts 一致）：[cut − pre, cut + post)
    for s in main:
        t = s["inTr"]
        s["win"] = (s["cut"], s["cut"]) if t["type"] == "cut" else (s["cut"] - t["pre"], s["cut"] + t["post"])
        s["outTr"] = norm_tr(s.get("out_tr"))
    texts = [t for t in tl.get("texts", []) if isinstance(t, dict) and t.get("to", 0) > t.get("from", 0)]
    inserts = [s for s in shots if s["layer"] > 0]
    caps = tl.get("captions") or []
    if isinstance(caps, dict):
        caps = caps.get("items", [])
    au = tl.get("audio") or {}
    sfx = au.get("sfx") or []
    voice = au.get("voice") or []
    music = au.get("music") or None

    # 拍网格（timeline.py 已换算到成片时间）→ 「第几小节第几拍」；没有就不标
    grid = tl.get("grid") or None
    secs = [x for x in (tl.get("sections") or []) if isinstance(x, dict) and "from" in x and "to" in x]
    where = None
    if grid and grid.get("beat_frames"):
        bf, bpb = float(grid["beat_frames"]), int(grid.get("beats_per_bar") or 4)
        off = float(grid.get("offset_s") or 0) * FPS - float(grid.get("lead_frames") or 0)

        def where(f):
            k = int((f - off + 0.5) // bf)
            sec = next((x["name"] for x in secs if x["from"] <= f < x["to"]), "")
            return (f"{sec} · " if sec else "") + f"第 {k // bpb + 1} 小节第 {k % bpb + 1} 拍（f{f}）"
    entries, frames, comps = [], set(), {}
    for i, s in enumerate(main):
        nxt = main[i + 1] if i + 1 < len(main) else None
        a = s["cut"]
        end = nxt["cut"] if nxt else max(dur, s["to"])
        end = max(end, a + 1)
        # 开场：入场转场走完之后；落幅：出场转场开始之前
        open_f = max(a, s["win"][1]) if s["inTr"]["type"] != "cut" else a
        land = end - 1
        if nxt and nxt["inTr"]["type"] != "cut":
            land = min(land, nxt["win"][0] - 1)
        land = max(min(land, s["to"] - 1), open_f)
        items = [(open_f, "开场", "beat", 0)]
        for t in texts:
            if a <= t["from"] < end:
                f = max(open_f, t["from"], min(t["from"] + settle(t), min(t["to"], end) - 1))
                items.append((f, f"{TEXT_CN.get(t['type'], t['type'])}：{text_summary(t)}", "beat", 2, t["type"]))
        for x in inserts:
            if a <= x["cut"] < end:
                items.append((min(x["cut"] + 6, end - 1), f"插入 {x.get('id', '')}：{x.get('note', '')}".rstrip("："), "beat", 1))
        items.append((land, "落幅", "end", 0))
        items.sort(key=lambda x: (x[0], -x[3]))
        ded = []
        for it in items:  # 6 帧内的重复帧合并：保留优先级高的；两个文字节拍合成一张（取后一帧，说明连起来）
            if ded and it[0] - ded[-1][0] < 6:
                p = ded[-1]
                if it[3] == 2 and p[3] == 2:
                    ded[-1] = (it[0], p[1] + "；" + it[1], "beat", 2, it[4])
                elif it[3] > p[3]:
                    ded[-1] = it
                continue
            ded.append(it)
        if kind == "sf":
            best = None
            for ty in SF_PRIORITY:
                c = [x for x in ded if len(x) > 4 and x[4] == ty]
                if c:
                    best = c[-1]
                    break
            ded = [best or ((open_f + land) // 2, "风格帧", "beat", 0)]
        elif per_shot > 0:
            if len(ded) > per_shot:
                keep = sorted({round(k * (len(ded) - 1) / max(1, per_shot - 1)) for k in range(per_shot)})
                ded = [ded[k] for k in keep]
            while len(ded) < per_shot and land - open_f >= 2 * (len(ded) + 1):
                have = {x[0] for x in ded}
                cands = [round(open_f + (land - open_f) * k / (per_shot + 1)) for k in range(1, per_shot + 1)]
                add = next((f for f in cands if all(abs(f - h) >= 3 for h in have)), None)
                if add is None:
                    break
                ded.append((add, "过程", "beat", 0))
                ded.sort(key=lambda x: x[0])
        boards = []
        for it in ded:
            f = int(it[0])
            frames.add(f)
            boards.append([f"F{f:04d}", f"@{f - a}", it[1], f, it[2]])
        if kind == "board" and not nxt and s["outTr"]["type"] != "cut":
            ot, oc = s["outTr"], int(s.get("end", s["to"]))
            w0, w1 = oc - ot["pre"], min(dur, oc + ot["post"])
            fr = sorted({min(max(0, round(w0 + (w1 - 1 - w0) * k / 3)), dur - 1) for k in range(4)})
            while len(fr) < 4:
                fr.insert(0, max(0, fr[0] - 1))
            frames.update(fr)
            name = f"T-{s['id']}"
            boards.append([name, "转场", f"收尾{TR_CN[ot['type']]}（{w1 - w0} 帧）", oc, "trans"])
            comps[name] = (fr, s["id"], "片尾", TR_CN[ot["type"]])
        if kind == "board" and nxt:
            ty = nxt["inTr"]["type"]
            if ty == "cut":
                fr = [nxt["cut"] - 1, nxt["cut"]]
            else:
                w0, w1 = nxt["win"]
                w1 = max(w1, w0 + 4)
                c = nxt["cut"]
                if ty in ("whip", "punch", "dip") and w0 < c < w1:
                    # 切点两边各两帧：上一镜的半程、切前最后一帧、切后第一帧、下一镜的半程（甩镜、冲击推最猛的两帧都在里面）
                    fr = sorted({w0 + (c - w0) // 2, c - 1, c, c + (w1 - c) // 2})
                else:
                    fr = sorted({round(w0 + (w1 - 1 - w0) * k / 3) for k in range(4)})
                while len(fr) < 4:
                    fr.append(fr[-1] + 1)
            fr = [min(max(0, f), dur - 1) for f in fr]
            frames.update(fr)
            name = f"T-{s['id']}"
            desc = TR_DESC.get(ty, "")
            n_tr = nxt["win"][1] - nxt["win"][0]
            boards.append([name, "转场", f"{TR_CN[ty]} → {nxt['id']}" + (f"（{n_tr} 帧）。{desc}" if ty != "cut" else "：左出画、右入画"), nxt["cut"], "trans"])
            comps[name] = (fr, s["id"], nxt["id"], TR_CN[ty])
        rep = next((b[0] for b in boards if b[4] == "beat" and b[2] not in ("开场", "过程")), next((b[0] for b in boards if b[4] != "trans"), boards[0][0]))

        # 右栏文字
        said = [c["text"] for c in caps if a <= c.get("from", -1) < end]
        shown = [f"{TEXT_CN.get(t['type'], t['type'])}「{text_summary(t)}」" for t in texts if a <= t["from"] < end]
        line = " ｜ ".join(x for x in ["口播：" + "，".join(said) if said else "", "画面字：" + "、".join(shown) if shown else ""] if x) or "（无口播、无字）"
        if where:
            line = f"{where(a)} ｜ {line}"
        cf = s.get("clip_frames") or []
        nf = s.get("native_fps") or 30
        speed = ""
        if len(cf) > 2:
            d = [(cf[k + 1] - cf[k]) * FPS / nf for k in range(len(cf) - 1)]
            lo, hi = min(d), max(d)
            if hi == 0:
                speed = "定格"
            elif d[-1] == 0:
                k = next(i for i in range(len(d)) if all(x == 0 for x in d[i:]))
                speed = f"{sum(d[:k]) / max(1, k):.2f}× 后定格"
            elif hi - lo > 0.15:
                speed = f"变速 {d[0]:.2f}× → {lo:.2f}× → {d[-1]:.2f}×"
            else:
                speed = f"{sum(d) / len(d):.2f}×"
        mv = s.get("move") or {}
        mv = {"type": mv} if isinstance(mv, str) else mv
        bits = [str(s.get("note") or "").rstrip("。")]
        src = s.get("src") or s.get("clip") or ""
        if not s.get("clip"):
            bits.append("黑场")
        else:
            bits.append(f"素材 {os.path.basename(str(src))}" + (f" 源 {s['src_in_s']:.2f}–{s['src_out_s']:.2f} s" if isinstance(s.get("src_in_s"), (int, float)) and isinstance(s.get("src_out_s"), (int, float)) else ""))
        if speed:
            bits.append(f"速度 {speed}")
        bits.append(f"运镜 {MOVE_CN.get(mv.get('type', 'none'), mv.get('type', ''))}" + ("（让出右侧）" if mv.get("make_room") == "right" else ""))
        if s["inTr"]["type"] != "cut":
            bits.append(f"入场 {TR_CN[s['inTr']['type']]} {s['inTr']['len']} 帧")
        if isinstance(s.get("hold_from"), (int, float)):
            bits.append(f"第 {int(s['hold_from'])} 帧（镜内 @{int(s['hold_from']) - a}）起定格")
        ins = [x for x in inserts if a <= x["cut"] < end]
        if ins:
            bits.append("插入镜头 " + "、".join(f"{x.get('id')}（{x.get('note', '')}）" for x in ins))
        bits.append(f"{end - a} 帧（{(end - a) / FPS:.2f} s）")
        snd = [f"{x.get('id') or os.path.basename(str(x.get('src', '')))} @{int(x['from']) - a}" for x in sfx if a <= x.get("from", -1) < end]
        has_voice = any(v.get("from", 0) < end and v.get("to", 0) > a for v in voice)
        ducked = music and any(r[0] < end and r[1] > a for r in (music.get("duck_ranges") or []))
        sound = "；".join(x for x in [
            "音效：" + "、".join(snd) if snd else "",
            "原声" + ("（配乐压低）" if ducked else "") if has_voice else "",
            "配乐" if music and not has_voice else "",
        ] if x) or "（无）"
        title = str(s.get("note") or s.get("id"))
        for sep in ("（", "：", "，", "。"):
            title = title.split(sep)[0]
        entries.append({"id": str(s["id"]), "title": title[:18], "t": [round(a / FPS, 2), round(end / FPS, 2)], "line": line,
                        "concept": "。".join(x for x in bits if x) + "。", "sound": sound, "boards": boards, "rep": rep})
    if secs:
        music_rows = [[round(x["from"] / FPS, 2), round(x["to"] / FPS, 2), str(x.get("name", ""))] for x in secs]
    else:
        music_rows = [[round((music.get("from") or 0) / FPS, 2), round(dur / FPS, 2), "配乐"]] if music else []
    hits = [[round(x["from"] / FPS, 2), str(x.get("id") or os.path.basename(str(x.get("src", ""))))] for x in sfx if "from" in x]
    return entries, sorted(frames), comps, music_rows, hits


def font_loader(proj):
    path = os.path.join(proj, "public/fonts/NotoSansSC-VF.ttf")

    def font(size, weight=500):
        try:
            f = ImageFont.truetype(path, size)
            try:
                f.set_variation_by_axes([weight])
            except Exception:
                pass
            return f
        except Exception:
            return ImageFont.load_default()

    return font


def main():
    ap = argparse.ArgumentParser(description="footage-to-vlog 审核台")
    ap.add_argument("proj")
    ap.add_argument("--kind", choices=["board", "sf"], default="board")
    ap.add_argument("--frames-per-shot", type=int, default=0)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--title")
    ap.add_argument("--notes")
    ap.add_argument("--timeline")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()

    proj = os.path.abspath(a.proj)
    tl_path = os.path.abspath(a.timeline) if a.timeline else os.path.join(proj, "src/timeline.json")
    tl = json.load(open(tl_path, encoding="utf-8"))
    out = os.path.join(proj, "work/review", a.kind)
    png = os.path.join(out, "_png")
    for d in (out, os.path.join(out, "frames"), os.path.join(out, "thumbs"), png):
        os.makedirs(d, exist_ok=True)

    entries, frames, comps, music_rows, hits = plan(tl, a.kind, a.frames_per_shot)
    print(f"{len(entries)} 镜，{len(frames)} 帧要渲，{len(comps)} 张转场图")

    if not a.no_render:
        for f in os.listdir(png):
            if f.endswith(".png"):
                os.remove(os.path.join(png, f))
        node = os.environ.get("NODE") or shutil.which("node") or "node"
        cmd = [node, os.path.join(proj, "tools/stills.mjs")] + ([tl_path] if a.timeline else []) + [png] + [str(f) for f in frames] + ["--scale=1"]
        r = subprocess.run(cmd, cwd=proj)
        if r.returncode != 0:
            sys.exit("静帧没渲完（见上面的报错）；修好后重跑，或加 --no-render 用已有的图")

    P = lambda f: os.path.join(png, f"f{f:04d}.png")
    miss = [f for f in frames if not os.path.exists(P(f))]
    if miss:
        sys.exit(f"缺静帧：{miss[:10]}")
    font = font_loader(proj)

    def save(name, im):
        im.convert("RGB").resize((1600, 900), Image.LANCZOS).save(os.path.join(out, "frames", f"{name}.jpg"), quality=82, optimize=True, progressive=True)
        im.convert("RGB").resize((384, 216), Image.LANCZOS).save(os.path.join(out, "thumbs", f"{name}.jpg"), quality=80, optimize=True)

    cache = {}

    def img(f):
        if f not in cache:
            cache[f] = Image.open(P(f)).convert("RGB")
        return cache[f]

    for e in entries:
        for b in e["boards"]:
            if b[4] != "trans":
                save(b[0], img(b[3]))
    F28, F30B, F26B = font(28, 500), font(30, 700), font(26, 700)
    for name, (fr, sid, nsid, title) in comps.items():
        c = Image.new("RGB", (1920, 1080), (14, 14, 16))
        d = ImageDraw.Draw(c)
        w, h = 920, 518
        if len(fr) == 2:
            pos = [(26, 262), (974, 262)]
            for (x, y), f in zip(pos, fr):
                c.paste(img(f).resize((w, h), Image.LANCZOS), (x, y))
            for (x, y), t in zip(pos, [f"{sid} 出画 · {tc(fr[0])}", f"{nsid} 入画 · {tc(fr[1])}"]):
                d.text((x + w / 2, y + h + 40), t, font=F28, fill=(200, 200, 204), anchor="mm")
            d.polygon([(936, 516), (936, 564), (984, 540)], fill=(240, 240, 240))
            d.text((960, 216), title, font=F30B, fill=(240, 240, 240), anchor="mm")
        else:
            pos = [(26, 14), (974, 14), (26, 548), (974, 548)]
            for n, ((x, y), f) in enumerate(zip(pos, fr)):
                c.paste(img(f).resize((w, h), Image.LANCZOS), (x, y))
                d.ellipse((x + 16, y + 16, x + 68, y + 68), fill=(20, 20, 22), outline=(240, 240, 240), width=2)
                d.text((x + 42, y + 42), str(n + 1), font=F26B, fill=(240, 240, 240), anchor="mm")
                d.text((x + w - 16, y + h - 16), tc(f), font=F26B, fill=(240, 240, 240), anchor="rs", stroke_width=3, stroke_fill=(10, 10, 12))
        save(name, c)

    kind_cn = "风格帧" if a.kind == "sf" else "分镜"
    dur_s = tl["duration"] / FPS
    today = datetime.date.today().isoformat()
    default_notes = (
        ["每镜一张定稿质量的静帧（文字落定之后）：看调色、字体、文字位置。", "按 1 / 2 / 3 标通过 / 要改 / 待定，意见写在右侧，可以写帧号（如 @12）。"]
        if a.kind == "sf"
        else [
            "每镜按节拍出图：开场、每个文字或动作落定后、落幅；最后一张（虚线框、蓝色「转场」标签）是出场转场。",
            "硬切拼成「左：上一镜最后一帧 | 右：这一镜第一帧」；其他转场在转场窗口里均匀取 4 帧，按序号读。",
            "静帧只能看画面；节奏、卡点要看动起来的小样。键盘：←/→ 换镜，↑/↓ 换图，1/2/3 = 通过 / 要改 / 待定。",
        ]
    )
    notes = json.load(open(a.notes, encoding="utf-8")) if a.notes else default_notes
    slug = "".join(ch if ch.isalnum() else "-" for ch in os.path.basename(proj)).strip("-") or "ftv"
    page = {
        "slug": f"{slug}-{a.kind}",
        "title": a.title or f"{tl.get('title') or os.path.basename(proj)} · {kind_cn}",
        "brand": f"{kind_cn} {a.version} · {dur_s:.1f} 秒 · {len(entries)} 镜",
        "version": f"{a.kind}-{a.version}",
        "end": round(dur_s, 2),
        "sub": f"{kind_cn} {a.version} · {today}",
        "notes": notes,
        "shots": entries,
        "music": music_rows,
        "hits": hits,
    }
    pj = os.path.join(out, "page.json")
    json.dump(page, open(pj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(local_page.build(page))
    print(f"✓ {out}/index.html：{len(entries)} 镜，{sum(len(e['boards']) for e in entries)} 张图")
    serve = [sys.executable, os.path.join(HERE, "serve_local.py"), out, "--port", str(a.port)]
    print("服务：" + " ".join(serve) + f"   → http://localhost:{a.port}/")
    if a.serve:
        os.execv(sys.executable, serve)


if __name__ == "__main__":
    main()
