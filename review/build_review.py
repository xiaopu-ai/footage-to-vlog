#!/usr/bin/env python3
"""生成本地审核台。分镜 / 风格帧：从 src/timeline.json 选帧 → 模板 tools/stills.mjs 渲静帧 → 出图、拼转场图 → page.json → index.html。
媒体审核页（配乐候选、节奏预演、成片）：列出音视频播放器，逐条标通过 / 要改 / 待定并写意见。

用法：
  python3 review/build_review.py <proj> [--kind board|sf] [--frames-per-shot N] [--version v1] [--title "…"]
                                 [--notes notes.json] [--timeline 另一份.json] [--no-render] [--serve] [--port 8765]
  python3 review/build_review.py <proj> --kind media [--name music|rhythm|film|…] --audio a.mp3 b.mp3 … --video preview.mp4 …
                                 [--title "…"] [--notes notes.json|"一句话"] [--marks marks.json] [--from-frame 0] [--no-wave]
                                 [--version v1] [--serve] [--port 8765]
  <proj>  从 template/ 复制出来的项目目录（有 src/timeline.json、tools/stills.mjs、node_modules）
  --kind board（默认）分镜：每个主轨镜头取「开场 / 每个文字或动作节拍落定后 / 落幅」+ 出场转场图
         sf         风格帧：每镜一张（文字最全、落定后的那一帧），不拼转场
  --frames-per-shot N  每镜（不算转场图）正好出 N 张：多了按时间均匀保留，少了在开场和落幅之间均匀补
  --notes  「本版改动」面板的条目（JSON 字符串数组，可带 <b>）
  --no-render  不重渲静帧，直接用上次的 _png/（只改文案时用）
  --serve  生成完直接起本地服务（前台运行，Ctrl+C 停）

产物：<proj>/work/review/<kind>/ 下的 index.html、page.json、frames/、thumbs/、_png/（原始静帧）；意见存在同目录 feedback.json。
服务：python3 review/serve_local.py <proj>/work/review/<kind> --port 8765，把 http://localhost:8765/ 给用户。
素材覆盖：--kind board 时如果有 <proj>/work/coverage.json（scripts/coverage.py 产出），顶栏显示「素材 X / Y」，
  点开是「已用 X / Y 条素材，复用最多的是…，还有 N 条没用」和按目录分组的未用清单；没有这个文件就不显示。

媒体审核页（--kind media）：
  🔴 不要把 mp3 / mp4 文件直接丢给用户、也不要靠聊天里的内嵌图片：不少 agent 宿主预览不了本地音视频、不显示图片
     （DeepSeek 实测三轮沟通白费）。一律出这个页面 + serve_local.py，把 http://localhost:端口/ 给用户。
  --name       子目录名，产物在 <proj>/work/review/<name>/，默认 media。约定：配乐候选 music、节奏预演 rhythm、成片 film，
               各自一份 feedback.json，互不覆盖。同名文件重出时沿用已有意见（条目 id = 文件名去掉扩展名）。
  --audio / --video   要审的文件（可多个；相对路径按当前目录解析）。文件用硬链接放进 <name>/media/（跨盘改软链，再不行复制）。
  --marks      每条的说明、段落和重点：{"<文件名或 id>": {"desc": "说明", "chapters": [[起秒, 止秒, "段名"]], "marks": [[秒, "说明"]]}}
               （也可以直接写成 [[秒, "说明"] 或 [起, 止, "段名"], …]）。
  有 src/timeline.json 时：视频条目自动列出 sections（段落）和音效冲击点；--from-frame N 表示视频第 0 秒对应时间线第 N 帧
  （节奏预演从 0 帧渲就是 0）。音频条目只有它就是时间线的配乐（audio.music.src）时才套 sections。
  每条显示文件名、时长、大小、波形（ffmpeg 解码音轨，轻任务；--no-wave 跳过）；页面记录每条实际播放过的比例（played）。
  键盘：空格 / K 播放暂停，J / L ∓5 秒，←/→ 换条，↑/↓ 跳段落，1/2/3 = 通过 / 要改 / 待定。

转场图：硬切 = 「左：上一镜最后一帧 | 右：本镜第一帧」；其余转场 = 转场窗口里均匀取 4 帧拼 2×2，按序号读。
🔴 本机同一时间只跑一个重任务：渲静帧前先把排队脚本路径放进环境变量 RENDER_QUEUE（stills.mjs 会自己排队），
   或者整条命令套排队脚本运行：<queue.sh> python3 review/build_review.py …
需要 Python 3.9+、Pillow；node 从 PATH 找（或设 NODE=/path/to/node）。
"""
import argparse
import datetime
import json
import os
import re
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


# ───────────────────────── 素材覆盖（board 顶栏）─────────────────────────

def _num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return int(x)
    if isinstance(x, (list, dict)):
        return len(x)
    return None


def _first(d, *keys):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def load_coverage(proj):
    """读 work/coverage.json（同事的 scripts/coverage.py 产出），整理成 {used, total, unused_n, summary, top_reused, unused}。
    字段名按常见写法容错；读不出用量和总数就返回 None（页面不显示，不报错）。"""
    f = os.path.join(proj, "work", "coverage.json")
    if not os.path.isfile(f):
        return None
    try:
        d = json.load(open(f, encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"⚠ work/coverage.json 读不了（{e}），审核台不显示素材用量")
        return None
    if not isinstance(d, dict):
        return None
    src = d.get("summary") if isinstance(d.get("summary"), dict) else d
    total = _num(_first(src, "total", "footage_total", "n_footage", "discovered", "footage")) or _num(_first(d, "total", "footage"))
    used = _num(_first(src, "used", "used_count", "n_used", "used_sources", "sources_used"))
    unused_raw = _first(d, "unused", "unused_by_dir", "unused_sources", "not_used")
    unused = {}
    if isinstance(unused_raw, dict):
        for k, v in unused_raw.items():
            if isinstance(v, list):
                unused[str(k)] = [os.path.basename(str(x.get("path") or x.get("src") or x) if isinstance(x, dict) else str(x)) for x in v]
    elif isinstance(unused_raw, list):
        for x in unused_raw:
            p = str(x.get("path") or x.get("src") or "") if isinstance(x, dict) else str(x)
            unused.setdefault(os.path.dirname(p), []).append(os.path.basename(p))
    # coverage.py v1:unused 是条数,按目录的未用清单在 by_dir[{"dir","unused":[路径…]}] 里
    if not unused and isinstance(d.get("by_dir"), list):
        for e in d["by_dir"]:
            if isinstance(e, dict) and isinstance(e.get("unused"), list) and e["unused"]:
                unused[str(e.get("dir", ""))] = [os.path.basename(str(x)) for x in e["unused"]]
    if isinstance(d.get("unused"), (int, float)) and _num(_first(src, "unused_count", "n_unused")) is None:
        src = dict(src, unused_count=d["unused"])
    # 没用的条数:显式字段 > 总数 − 已用 > 清单长度(清单可能只列了一部分)
    unused_n = _num(_first(src, "unused_count", "n_unused"))
    if unused_n is None and total is not None and used is not None:
        unused_n = total - used
    if unused_n is None and unused:
        unused_n = sum(len(v) for v in unused.values())
    if used is None and total is not None and unused_n is not None:
        used = total - unused_n
    if not total or used is None:
        print("⚠ work/coverage.json 里认不出「已用 / 总数」，审核台不显示素材用量")
        return None
    reuse_raw = _first(d, "top_reused", "reused", "reuse", "repeats", "reuse_counts", "uses")
    top = []
    if isinstance(reuse_raw, dict):
        top = [[os.path.basename(str(k)), int(v if isinstance(v, (int, float)) else _num(v) or 0)] for k, v in reuse_raw.items()]
    elif isinstance(reuse_raw, list):
        for x in reuse_raw:
            if isinstance(x, dict):
                n = _first(x, "count", "n", "times", "uses", "shots")
                top.append([os.path.basename(str(_first(x, "src", "path", "name", "file") or "?")), _num(n) or 0])
            elif isinstance(x, (list, tuple)) and len(x) >= 2:
                top.append([os.path.basename(str(x[0])), _num(x[1]) or 0])
    top = sorted((t for t in top if t[1] > 0), key=lambda t: -t[1])[:8]
    rep = [t for t in top if t[1] > 1]
    summary = f"已用 {used} / {total} 条素材" + (
        "，复用最多的是 " + "、".join(f"{n}（{k} 次）" for n, k in rep[:3]) if rep else "，没有素材被复用") + (
        f"，还有 {unused_n} 条没用" if unused_n else "，全部用上了")
    return {"used": used, "total": total, "unused_n": unused_n or 0, "summary": summary, "top_reused": top, "unused": unused}


# ───────────────────────── 媒体审核页 ─────────────────────────

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
NAME_CN = {"music": "配乐候选", "rhythm": "节奏预演", "film": "成片", "media": "试听 / 试看"}


def _ff():
    return os.environ.get("FFMPEG") or shutil.which("ffmpeg")


def media_info(path):
    """→ {dur, has_audio, has_video}。有 ffprobe 用 ffprobe，没有就解析 ffmpeg -i；都没有就全是 None（页面自己读时长）。"""
    info = {"dur": None, "has_audio": None, "has_video": None}
    fp = os.environ.get("FFPROBE") or shutil.which("ffprobe")
    try:
        if fp:
            r = subprocess.run([fp, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            j = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
            st = j.get("streams", [])
            info["dur"] = float((j.get("format") or {}).get("duration") or 0) or None
            info["has_audio"] = any(x.get("codec_type") == "audio" for x in st)
            info["has_video"] = any(x.get("codec_type") == "video" and not (x.get("disposition") or {}).get("attached_pic") for x in st)
            return info
        ff = _ff()
        if not ff:
            return info
        r = subprocess.run([ff, "-hide_banner", "-nostdin", "-i", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        t = r.stderr.decode("utf-8", "replace")
        m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", t)
        if m:
            info["dur"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        info["has_audio"] = bool(re.search(r"Stream #\S+.*: Audio:", t))
        info["has_video"] = any("attached pic" not in ln for ln in re.findall(r"Stream #\S+.*: Video:.*", t))
    except (OSError, ValueError) as e:
        print(f"⚠ 读不了 {path} 的信息：{e}")
    return info


def wave_peaks(path, n=1000):
    """解码音轨（8 kHz 单声道，轻任务）→ n 个 0–100 的峰值；没有音轨 / 没有 ffmpeg 返回 None。"""
    ff = _ff()
    if not ff:
        return None
    r = subprocess.run([ff, "-hide_banner", "-nostdin", "-v", "error", "-i", path, "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "8000",
                        "-f", "s16le", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0 or len(r.stdout) < 4000:
        return None
    try:
        import numpy as np
        a = np.abs(np.frombuffer(r.stdout[: len(r.stdout) // 2 * 2], np.int16).astype(np.float32))
        k = max(1, len(a) // n)
        m = a[: k * (len(a) // k)].reshape(-1, k).max(axis=1)
        ref = float(np.percentile(m, 99.5)) or 1.0
        return [int(x) for x in np.clip(m / ref * 100, 0, 100).round()]
    except ImportError:
        import array
        a = array.array("h")
        a.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
        k = max(1, len(a) // n)
        m = [max(abs(v) for v in a[i:i + k]) for i in range(0, len(a) - k + 1, k)]
        ref = sorted(m)[int(len(m) * 0.995) - 1 if len(m) > 1 else 0] or 1
        return [min(100, round(x / ref * 100)) for x in m]


def link_into(src, dst):
    """硬链接 → 软链 → 复制。返回用的方式。"""
    if os.path.lexists(dst):
        os.remove(dst)
    try:
        os.link(src, dst)
        return "硬链接"
    except OSError:
        pass
    try:
        os.symlink(src, dst)
        return "软链"
    except OSError:
        shutil.copy2(src, dst)
        return "复制"


def load_notes(arg, default):
    if not arg:
        return default
    if os.path.isfile(arg):
        txt = open(arg, encoding="utf-8").read()
        try:
            v = json.loads(txt)
            return [str(x) for x in v] if isinstance(v, list) else [str(v)]
        except ValueError:
            return [html_escape(x) for x in txt.splitlines() if x.strip()]
    return [html_escape(arg)]


def html_escape(x):
    import html as _h
    return _h.escape(x)


def media_main(a, proj):
    files = [(p, "audio") for p in a.audio] + [(p, "video") for p in a.video]
    if not files:
        sys.exit("--kind media 要给 --audio 和 / 或 --video 文件")
    name = re.sub(r"[^\w.-]+", "-", a.name).strip("-") or "media"
    out = os.path.join(proj, "work/review", name)
    mdir = os.path.join(out, "media")
    os.makedirs(mdir, exist_ok=True)

    tl = None
    tl_path = os.path.abspath(a.timeline) if a.timeline else os.path.join(proj, "src/timeline.json")
    if not a.no_timeline and os.path.isfile(tl_path):
        try:
            tl = json.load(open(tl_path, encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"⚠ {tl_path} 读不了（{e}），不列段落")
    marks_cfg = {}
    if a.marks:
        marks_cfg = json.load(open(a.marks, encoding="utf-8"))
    off = a.from_frame / FPS
    secs, hits, music_abs, music_from = [], [], None, 0.0
    if tl:
        secs = [[x["from"] / FPS - off, x["to"] / FPS - off, str(x.get("name", ""))] for x in (tl.get("sections") or [])
                if isinstance(x, dict) and "from" in x and "to" in x]
        au = tl.get("audio") or {}
        hits = [[x["from"] / FPS - off, str(x.get("note") or x.get("id") or os.path.basename(str(x.get("src", ""))))]
                for x in (au.get("sfx") or []) if isinstance(x, dict) and "from" in x]
        mu = au.get("music") or {}
        if mu.get("src"):
            music_abs = os.path.realpath(os.path.join(proj, "public", mu["src"]))
            music_from = float(mu.get("from") or 0) / FPS

    items, used_ids, linked = [], set(), set()
    for p, kind in files:
        ap = os.path.abspath(p)
        if not os.path.isfile(ap):
            sys.exit(f"文件不存在：{p}")
        base = os.path.basename(ap)
        stem, ext = os.path.splitext(base)
        iid = re.sub(r"[^\w.-]+", "-", stem).strip("-") or "item"
        k, n = iid, 2
        while k in used_ids:
            k, n = f"{iid}-{n}", n + 1
        iid = k
        used_ids.add(iid)
        if kind == "audio" and ext.lower() not in AUDIO_EXT:
            print(f"⚠ {base} 不像音频文件，按 --audio 处理")
        fn = iid + ext.lower()
        how = link_into(ap, os.path.join(mdir, fn))
        linked.add(fn)
        info = media_info(ap)
        if kind == "video" and info["has_video"] is False:
            print(f"⚠ {base} 没有视频流，改按音频列出")
            kind = "audio"
        it = {"id": iid, "name": base, "file": base, "kind": kind, "src": "media/" + fn,
              "dur": round(info["dur"], 3) if info["dur"] else None, "size": os.path.getsize(ap), "chapters": [], "marks": []}
        if tl and (kind == "video" or (music_abs and os.path.realpath(ap) == music_abs)):
            o = 0.0 if kind == "video" else -music_from  # 时间线配乐：音乐自己的第 0 秒 = 时间线 music.from
            D = it["dur"] or 1e9
            it["chapters"] = [[round(max(0.0, x0 + o), 3), round(min(D, x1 + o), 3), nm] for x0, x1, nm in secs if x1 + o > 0 and x0 + o < D]
            it["marks"] = [[round(t + o, 3), nm] for t, nm in hits if 0 <= t + o < D] if kind == "video" else []
        mc = marks_cfg.get(iid) or marks_cfg.get(base) or marks_cfg.get(stem)
        if isinstance(mc, list):
            mc = {"marks": mc}
        if isinstance(mc, dict):
            it["desc"] = str(mc.get("desc", ""))
            for x in mc.get("chapters", []) + mc.get("marks", []):
                if isinstance(x, (list, tuple)) and len(x) >= 3 and isinstance(x[1], (int, float)):
                    it["chapters"].append([float(x[0]), float(x[1]), str(x[2])])
                elif isinstance(x, (list, tuple)) and len(x) >= 2:
                    it["marks"].append([float(x[0]), str(x[1])])
            it["chapters"].sort()
            it["marks"].sort()
        if not a.no_wave and info["has_audio"] is not False:
            it["peaks"] = wave_peaks(ap)
        items.append(it)
        print(f"  ✓ {kind} {base} → {name}/media/{fn}（{how}）· {it['dur'] or '?'} s · {len(it['chapters'])} 段 · {len(it['marks'])} 个重点"
              + ("" if it.get("peaks") else " · 无波形"))
    for f in os.listdir(mdir):  # 上一版留下、这次不审的文件（都是本脚本建的链接 / 副本）
        if f not in linked:
            os.remove(os.path.join(mdir, f))

    na, nv = sum(1 for x in items if x["kind"] == "audio"), sum(1 for x in items if x["kind"] == "video")
    what = NAME_CN.get(name, name)
    default_notes = [
        "逐条播放：<b>空格</b>播放 / 暂停，<b>J / L</b> 后退 / 前进 5 秒，<b>↑ / ↓</b> 跳段落，点波形跳转。",
        "按 <b>1 / 2 / 3</b> 标通过 / 要改 / 待定，意见写在右侧；「记下当前时间」会把播放位置插进意见。",
        "页面记下每条实际播放了多少（右下「已播放 xx%」）。",
    ]
    today = datetime.date.today().isoformat()
    title_proj = (tl or {}).get("title") or os.path.basename(proj)
    slug = "".join(ch if ch.isalnum() else "-" for ch in os.path.basename(proj)).strip("-") or "ftv"
    page = {
        "kind": "media", "slug": f"{slug}-{name}", "title": a.title or f"{title_proj} · {what}",
        "brand": " · ".join(x for x in [what + " " + a.version, f"{na} 条音频" if na else "", f"{nv} 条视频" if nv else ""] if x),
        "version": f"{name}-{a.version}", "sub": f"{what} {a.version} · {today}",
        "notes": load_notes(a.notes, default_notes), "items": items,
    }
    json.dump(page, open(os.path.join(out, "page.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(local_page.build_player(page))
    print(f"✓ {out}/index.html：{len(items)} 条（{na} 音频 / {nv} 视频）；意见存 {out}/feedback.json")
    serve = [sys.executable, os.path.join(HERE, "serve_local.py"), out, "--port", str(a.port)]
    print("服务：" + " ".join(serve) + f"   → http://localhost:{a.port}/")
    if a.serve:
        os.execv(sys.executable, serve)


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
    ap.add_argument("--kind", choices=["board", "sf", "media"], default="board")
    ap.add_argument("--frames-per-shot", type=int, default=0)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--title")
    ap.add_argument("--notes")
    ap.add_argument("--timeline")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--audio", nargs="+", action="extend", default=[], help="media：要审的音频")
    ap.add_argument("--video", nargs="+", action="extend", default=[], help="media：要审的视频")
    ap.add_argument("--name", default="media", help="media：产物子目录（music / rhythm / film …）")
    ap.add_argument("--marks", help="media：每条的说明 / 段落 / 重点 JSON")
    ap.add_argument("--from-frame", type=int, default=0, help="media：视频第 0 秒对应时间线第几帧")
    ap.add_argument("--no-wave", action="store_true", help="media：不算波形")
    ap.add_argument("--no-timeline", action="store_true", help="media：不读 timeline.json 的段落")
    a = ap.parse_args()

    proj = os.path.abspath(a.proj)
    if a.kind == "media":
        return media_main(a, proj)
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
    notes = load_notes(a.notes, default_notes)
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
    if a.kind == "board":
        cov = load_coverage(proj)
        if cov:
            page["coverage"] = cov
            print("素材覆盖：" + cov["summary"])
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
