#!/usr/bin/env python3
"""coverage.py · 素材覆盖度:这一版剪辑用了多少条素材、哪些一条都没碰、同一条用了几次、镜头日志有没有漏条。

起因:一条燃剪 73 条素材只用了 27 条,一个文件夹 7 条全没用,同时一条航拍用了 4 次。在结构阶段就把候选池收窄到
十几条、之后只在池子里互换,是复用的直接原因。分镜前、每次换镜后跑一次,先回未用的素材里找。

用法:
  python3 scripts/coverage.py <proj> [--json] [--strict]
    --json     把 coverage.json 的内容打印到标准输出(不打印中文摘要);文件照样写
    --strict   有未豁免的源区间重叠,或镜头日志缺记录 / 没填,退出码 1(qa / 审核台前置检查用)

读:
  <proj>/work/footage.json            素材清单(inventory.py 写;数组,或 {"footage" | "items" | "files": [...]})
  <proj>/src/timeline.json            编译后的时间线(timeline.py 写);每镜用 src_used_s(没有就用 src_in_s–src_out_s)
  <proj>/work/shotlog.md              镜头日志(inventory.py 生成模板,每条素材一节「## <相对路径>」,下面填「- 内容:」等)
  <proj>/work/inventory-failed.json   盘点对账(可选;有就把 status / counts 带进结果)
写:
  <proj>/work/coverage.json
打印:素材总数、用了几条和占比、按目录分组的未用清单、复用 ≥ 2 次的素材和区间、源区间重叠、镜头日志缺漏。
退出码:0;--strict 且有问题 → 1;缺 footage.json / timeline.json → 2。

coverage.json 字段(审核台、qa 读它;改字段要同步改它们):
  version          1
  footage_total    素材总数(footage.json 条数)
  used             被时间线用到的素材条数(任意层)
  used_share       used / footage_total(0–1,保留 3 位)
  unused           没用到的条数
  shots_with_src   时间线里有素材的镜头数(src 为 null 的黑场不算)
  by_dir           [{"dir": 目录(根目录为 ""), "total", "used", "unused": [相对路径…]}],按目录名排序
  items            [{"path", "dir", "duration", "fps", "shots": 用到它的镜头数, "uses": 次数(同层首尾相接、源时间接得上的接续算一次),
                     "segments": [{"shots": [镜头 id…], "layer", "from", "to"(成片帧), "range": [源起秒, 源止秒]}],
                     "shotlog": "ok" | "empty" | "missing" | "mentioned"}]
                   shotlog:ok = 有「## 路径」小节且「内容」填了;empty = 有小节但内容没填;mentioned = 没有小节但正文里提到了
                   文件名(自由格式的日志,算有记录);missing = 完全没提到
  reuse            [{"path", "uses", "segments": [...]}]:uses ≥ 2 的素材,按次数降序
  overlaps         [{"path", "a", "b"(镜头 id), "range": [源起, 源止], "overlap_s", "allowed": 任一镜写了 allow_reuse}]
                   同一素材两镜的源区间重叠 > 0.2 s 才列
  allow_reuse      写了 "allow_reuse": true 的镜头 id
  unknown_src      时间线里用了、但 footage.json 里找不到的 src
  shotlog          {"exists", "missing": [路径…], "empty": [路径…], "mentioned": [路径…]}
  inventory        {"status": "complete" | "incomplete" | "unknown", "counts": {...}}(来自 inventory-failed.json)
  problems         ["overlap", "shotlog", "inventory_incomplete", "unknown_src"] 里出现的项(--strict 只看前两项)
依赖:只用标准库。不解码视频,轻任务。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

OVERLAP_S = 0.2


def jload(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_footage(proj):
    data = jload(os.path.join(proj, "work", "footage.json"))
    if data is None:
        return None
    if isinstance(data, dict):
        data = data.get("footage") or data.get("items") or data.get("files") or []
    return [d for d in data if isinstance(d, dict) and d.get("path")]


def shotlog_status(text, paths):
    """→ {path: ok|empty|missing|mentioned}。按 inventory.py 的模板「## <路径>」分节;没有小节时退回到全文搜文件名。"""
    out = {}
    secs = {}
    if text:
        parts = re.split(r"^## +(.+?)\s*$", text, flags=re.M)
        for i in range(1, len(parts) - 1, 2):
            secs[parts[i].strip()] = parts[i + 1]
    low = {k.lower(): v for k, v in secs.items()}
    for p in paths:
        body = secs.get(p)
        if body is None:
            body = low.get(p.lower())
        if body is None:
            # 小节标题只写了文件名的也认
            base = os.path.basename(p).lower()
            cand = [v for k, v in low.items() if os.path.basename(k) == base]
            body = cand[0] if len(cand) == 1 else None
        if body is not None:
            m = re.search(r"^-[ \t]*内容[ \t]*[::][ \t]*(.*)$", body, re.M)
            filled = bool(m and m.group(1).strip()) if m else bool(re.sub(r"^-\s*规格.*$", "", body, flags=re.M).strip(" \n-"))
            out[p] = "ok" if filled else "empty"
            continue
        stem = os.path.splitext(os.path.basename(p))[0]
        pat = r"(?<![A-Za-z0-9])" + re.escape(stem) + r"(?![A-Za-z0-9])"
        out[p] = "mentioned" if text and re.search(pat, text, re.I) else "missing"
    return out


def main():
    ap = argparse.ArgumentParser(description="素材覆盖度 → work/coverage.json")
    ap.add_argument("proj")
    ap.add_argument("--json", action="store_true", help="把结果 JSON 打印到标准输出")
    ap.add_argument("--strict", action="store_true", help="有源区间重叠或镜头日志缺漏时退出 1")
    a = ap.parse_args()
    proj = os.path.abspath(a.proj)

    footage = load_footage(proj)
    if footage is None:
        print(f"✗ 没有 {os.path.join(proj, 'work', 'footage.json')}(先跑 inventory.py)", file=sys.stderr)
        sys.exit(2)
    tl = jload(os.path.join(proj, "src", "timeline.json"))
    if tl is None:
        print(f"✗ 没有 {os.path.join(proj, 'src', 'timeline.json')}(先跑 timeline.py)", file=sys.stderr)
        sys.exit(2)

    paths = [f["path"] for f in footage]
    info = {f["path"]: f for f in footage}
    lower = {p.lower(): p for p in paths}
    bybase = {}
    for p in paths:
        bybase.setdefault(os.path.basename(p).lower(), []).append(p)

    def resolve(src):
        if src in info:
            return src
        if src.lower() in lower:
            return lower[src.lower()]
        b = bybase.get(os.path.basename(src).lower(), [])
        return b[0] if len(b) == 1 else None

    shots = [s for s in tl.get("shots", []) if s.get("src")]
    used = {}
    unknown = set()
    for s in shots:
        p = resolve(s["src"])
        if p is None:
            unknown.add(s["src"])
            continue
        rng = s.get("src_used_s") or [s.get("src_in_s"), s.get("src_out_s")]
        if rng[0] is None or rng[1] is None:
            rng = [s.get("src_in_s") or 0.0, s.get("src_out_s") or s.get("src_in_s") or 0.0]
        used.setdefault(p, []).append({"id": s["id"], "layer": s.get("layer", 0), "cut": s["cut"], "end": s["end"],
                                       "in": s.get("src_in_s"), "out": s.get("src_out_s"), "range": [float(min(rng)), float(max(rng))],
                                       "fps": s.get("native_fps") or 30, "allow": bool(s.get("allow_reuse"))})

    items, reuse, overlaps = [], [], []
    for p in paths:
        us = sorted(used.get(p, []), key=lambda u: (u["layer"], u["cut"]))
        segs = []
        for u in us:
            if segs:
                q = segs[-1]
                if (q["layer"] == u["layer"] and q["to"] == u["cut"] and q["_out"] is not None and u["in"] is not None
                        and abs(q["_out"] - u["in"]) <= 2.0 / max(1.0, u["fps"])):
                    q["shots"].append(u["id"])
                    q["to"] = u["end"]
                    q["range"][1] = max(q["range"][1], u["range"][1])
                    q["_out"] = u["out"]
                    continue
            segs.append({"shots": [u["id"]], "layer": u["layer"], "from": u["cut"], "to": u["end"],
                         "range": [round(u["range"][0], 3), round(u["range"][1], 3)], "_out": u["out"]})
        for sg in segs:
            sg.pop("_out", None)
            sg["range"] = [round(sg["range"][0], 3), round(sg["range"][1], 3)]
        byr = sorted(us, key=lambda u: u["range"][0])
        for i in range(len(byr)):
            for j in range(i + 1, len(byr)):
                x, y = byr[i], byr[j]
                ov = min(x["range"][1], y["range"][1]) - max(x["range"][0], y["range"][0])
                if ov > OVERLAP_S:
                    overlaps.append({"path": p, "a": x["id"], "b": y["id"],
                                     "range": [round(max(x["range"][0], y["range"][0]), 3), round(min(x["range"][1], y["range"][1]), 3)],
                                     "overlap_s": round(ov, 3), "allowed": x["allow"] or y["allow"]})
        it = {"path": p, "dir": os.path.dirname(p), "duration": info[p].get("duration"), "fps": info[p].get("fps"),
              "shots": len(us), "uses": len(segs), "segments": segs}
        items.append(it)
        if len(segs) >= 2:
            reuse.append({"path": p, "uses": len(segs), "segments": segs})
    reuse.sort(key=lambda r: (-r["uses"], r["path"]))

    sl_path = os.path.join(proj, "work", "shotlog.md")
    sl_text = open(sl_path, encoding="utf-8").read() if os.path.exists(sl_path) else ""
    sl = shotlog_status(sl_text, paths)
    for it in items:
        it["shotlog"] = sl[it["path"]]

    dirs = {}
    for it in items:
        d = dirs.setdefault(it["dir"], {"dir": it["dir"], "total": 0, "used": 0, "unused": []})
        d["total"] += 1
        if it["shots"]:
            d["used"] += 1
        else:
            d["unused"].append(it["path"])
    by_dir = [dirs[k] for k in sorted(dirs)]

    inv = jload(os.path.join(proj, "work", "inventory-failed.json")) or {}
    inventory = {"status": inv.get("status", "unknown"), "counts": inv.get("counts", {})}

    n_used = sum(1 for it in items if it["shots"])
    problems = []
    if any(not o["allowed"] for o in overlaps):
        problems.append("overlap")
    if any(v in ("missing", "empty") for v in sl.values()):
        problems.append("shotlog")
    if inventory["status"] == "incomplete":
        problems.append("inventory_incomplete")
    if unknown:
        problems.append("unknown_src")
    res = {
        "version": 1, "generated": datetime.now().isoformat(timespec="seconds"),
        "footage_total": len(items), "used": n_used, "used_share": round(n_used / len(items), 3) if items else 0.0,
        "unused": len(items) - n_used, "shots_with_src": len(shots),
        "by_dir": by_dir, "items": items, "reuse": reuse, "overlaps": overlaps,
        "allow_reuse": sorted({s["id"] for s in shots if s.get("allow_reuse")}),
        "unknown_src": sorted(unknown),
        "shotlog": {"exists": bool(sl_text), "missing": [p for p in paths if sl[p] == "missing"],
                    "empty": [p for p in paths if sl[p] == "empty"], "mentioned": [p for p in paths if sl[p] == "mentioned"]},
        "inventory": inventory, "problems": problems,
    }
    out = os.path.join(proj, "work", "coverage.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        tot = len(items)
        print(f"素材 {tot} 条,时间线用了 {n_used} 条({res['used_share']:.0%}),{tot - n_used} 条没用;有素材的镜头 {len(shots)} 个")
        if inventory["status"] == "incomplete":
            print(f"  ⚠️ 盘点不完整(inventory-failed.json:{inventory['counts']}),没进 footage.json 的素材这里也看不到")
        print("  按目录:")
        for d in by_dir:
            print(f"    {d['dir'] or '(根目录)'}/  用了 {d['used']}/{d['total']}" + ("  ← 一条都没用" if d["total"] and not d["used"] else ""))
        for d in by_dir:
            if d["unused"]:
                names = [os.path.basename(x) for x in d["unused"]]
                print(f"  未用 · {d['dir'] or '(根目录)'}/({len(names)}):" + "、".join(names[:30]) + (" …" if len(names) > 30 else ""))
        if reuse:
            print("  复用(用了 2 次以上;首尾相接的接续算一次):")
            for r in reuse:
                segs = ";".join(f"{'+'.join(sg['shots'])} {sg['range'][0]:.2f}–{sg['range'][1]:.2f}s" for sg in r["segments"])
                print(f"    {r['path']} × {r['uses']}:{segs}" + ("  ⚠️" if r["uses"] >= 3 else ""))
        for o in overlaps:
            print(f"  {'ℹ️ 已豁免' if o['allowed'] else '❌'} 源区间重叠:{o['path']} 的 {o['a']} 和 {o['b']} 在 "
                  f"{o['range'][0]:.2f}–{o['range'][1]:.2f}s 重叠 {o['overlap_s']:.2f}s")
        if unknown:
            print(f"  ⚠️ 时间线里有、footage.json 里没有的素材:{'、'.join(sorted(unknown))}")
        if not sl_text:
            print(f"  ⚠️ 没有镜头日志 {sl_path}(inventory.py 会生成模板,逐条填)")
        else:
            miss, emp, men = res["shotlog"]["missing"], res["shotlog"]["empty"], res["shotlog"]["mentioned"]
            print(f"  镜头日志:{tot - len(miss) - len(emp)}/{tot} 条有记录" + (f"(其中 {len(men)} 条只在正文里提到、没有单独小节)" if men else ""))
            if miss:
                print(f"    缺记录 {len(miss)} 条:" + "、".join(os.path.basename(x) for x in miss[:30]) + (" …" if len(miss) > 30 else ""))
            if emp:
                print(f"    有小节但「内容」没填 {len(emp)} 条:" + "、".join(os.path.basename(x) for x in emp[:30]) + (" …" if len(emp) > 30 else ""))
        print(f"✓ {out}")
    if a.strict and ("overlap" in problems or "shotlog" in problems):
        if not a.json:
            print("✗ --strict:" + "、".join({"overlap": "有未豁免的源区间重叠", "shotlog": "镜头日志有缺漏"}[p] for p in problems if p in ("overlap", "shotlog")))
        sys.exit(1)


if __name__ == "__main__":
    main()
