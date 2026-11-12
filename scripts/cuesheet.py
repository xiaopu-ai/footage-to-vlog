#!/usr/bin/env python3
"""卡点表(交付物):<proj>/src/timeline.json → Markdown。给客户 / 剪辑师对照,方便在剪映等软件里按表替换配乐、音效。

用法:
  python3 cuesheet.py <proj> [--out <proj>/out/卡点表.md] [--title "燃剪示例 · 卡点表(主版)"]

内容:
  - 规格:帧率、总帧数、时长;有拍网格时写 BPM、一拍 / 一小节多少帧,「小节.拍」从 1.1 起算(「+」= 后半拍)。
  - 节奏统计(timeline.json 的 stats):硬切落在强重音上的比例、drop 段镜数 / 平均镜长 / 变速坡、复用。
  - 段落(edit.json 的 sections,带类型 kind)。
  - 配乐:文件、起点、trim、音量关键点、压低区间数;audio.music.edl 指向 bar_edit.py 的 EDL 时逐段列出源区间。
  - 逐镜(主轨)+ 插入镜头:帧、时间、小节.拍、卡拍(切点离最近的 8 分音符 ≤ 2 帧算 ✓;已扣掉 lead 提前量)、
    重音(切点 ±2 帧内有强重音 s ≥ 0.7 时写 ✓ 和重音序号 / 类型;没有重音表时这一列不出)、
    进场转场、画面说明(note)、素材 · 源秒 · 速度。
  - 变速镜头:按段落列出每个变速镜头的速度曲线,关键点换算成成片帧和「小节.拍」,方便核对坡心是不是落在拍上。
  - 文字 / 包装(endcard 的 logos、image 的 src 写字符串或 {"src", "w"} 对象都认)、原声段、音效(音效不扣 lead)。
    字幕只给条数(全文在 timeline.json)。
不改任何文件,只写 --out。
"""
import argparse
import json
import math
import os

TR_NAME = {"cut": "硬切", "dip": "黑场过渡", "fade": "叠化", "whip": "甩镜", "punch": "冲击推镜", "sweep": "光扫", "wipe": "擦入"}
KIND_NAME = {"intro": "开场", "build": "蓄力", "stop": "停拍", "drop": "drop", "breakdown": "撤鼓", "end": "落版", "other": ""}
STRONG_S = 0.7
HIT_WIN = 2


def logo_name(x):
    """Logo 写法:字符串路径或 {"src", "w"?} 对象 → 文件名;不合法的写成「?」,不让卡点表崩。"""
    if isinstance(x, dict):
        x = x.get("src")
    return os.path.basename(x) if isinstance(x, str) and x else "?"


def main():
    ap = argparse.ArgumentParser(description="timeline.json → 卡点表 Markdown")
    ap.add_argument("proj")
    ap.add_argument("--out", default=None)
    ap.add_argument("--title", default=None)
    a = ap.parse_args()
    proj = os.path.abspath(a.proj)
    tl = json.load(open(os.path.join(proj, "src", "timeline.json"), encoding="utf-8"))
    fps, D = tl["fps"], tl["duration"]
    g = tl.get("grid")
    out = a.out or os.path.join(proj, "out", "卡点表.md")
    lead = g["lead_frames"] if g else 0

    def tc(f):
        return f"{int(f // fps // 60)}:{f / fps % 60:05.2f}"

    def bp_of(f, lead_on):
        return ((f + (lead if lead_on else 0) + 0.5) / fps - g["offset_s"]) / g["beat_s"]

    def pos(f, lead_on=True):
        if not g:
            return ""
        bp = bp_of(f, lead_on)
        bpb = g["beats_per_bar"]
        bar = math.floor(bp / bpb)
        inbar = bp - bar * bpb
        return f"{bar + 1}.{int(inbar) + 1}" + ("+" if inbar % 1 >= 0.45 else "")

    def onbeat(f):
        if not g:
            return ""
        bp = ((f + lead) / fps - g["offset_s"]) / g["beat_s"]
        near = round(bp * 2) / 2
        gf = int(round((g["offset_s"] + near * g["beat_s"]) * fps)) - lead
        d = f - max(0, gf)
        return "✓" if abs(d) <= 2 else f"{d:+d} 帧"

    # 重音:优先用 timeline.json 的 cuts(编译器算好的);没有时按 accents 现算
    accents = tl.get("accents")
    acc_by_i = {x["i"]: x for x in accents or []}
    cut_info = {c["shot"]: c for c in tl.get("cuts") or []}

    def accent_cell(s):
        if accents is None:
            return None
        c = cut_info.get(s["id"])
        if c is not None and "accent" in c:
            i, d = c.get("accent"), c.get("acc_d")
        else:
            best = None
            for x in accents:
                if x["s"] < STRONG_S:
                    continue
                dd = s["cut"] - (x["f"] - lead)
                if abs(dd) <= HIT_WIN and (best is None or abs(dd) < abs(best[1])):
                    best = (x["i"], dd)
            i, d = best if best else (None, None)
        if s["cut"] <= 0:
            return ""
        if i is None:
            return "—"
        x = acc_by_i.get(i, {})
        return f"✓ #{i} {x.get('kind', '')}" + (f" {d:+d}" if d else "")

    def speed(s):
        sp = s.get("speed", 1)
        if isinstance(sp, (int, float)):
            return f"{sp:g}×"
        vals = []
        for _, v in sp:
            if not vals or vals[-1] != v:
                vals.append(v)
        return "变速 " + "→".join(f"{v:g}×" for v in vals)

    def tr(s):
        t = s["in_tr"]
        return TR_NAME.get(t["type"], t["type"]) + (f" {t['len']} 帧" if t["type"] != "cut" else "")

    def text_of(t):
        typ = t["type"]
        if typ in ("slam", "brushword", "chapter"):
            return t.get("text") or ""
        if typ == "reveal":
            return t.get("text") or logo_name(t.get("image"))
        if typ == "qcard":
            return f"Q{t.get('no', '')}:" + " / ".join(t.get("lines", []))
        if typ == "lower":
            return " · ".join(x for x in (t.get("name"), t.get("title")) if x)
        if typ == "spec":
            return f"{t.get('big', '')}{t.get('unit', '')} · {t.get('label', '')}" + (f" · {t['sub']}" if t.get("sub") else "")
        if typ == "lockup":
            return " · ".join(t.get("lines", [])) + (" + Logo" if t.get("logo") else "")
        if typ == "endcard":
            logos = t.get("logos") or []
            if isinstance(logos, (str, dict)):
                logos = [logos]
            return "官方 Logo:" + ("、".join(logo_name(x) for x in logos) if logos else "(用 brand.logo_white)")
        if typ == "image":
            return logo_name(t.get("src"))
        return ""

    title = a.title or f"{tl.get('title') or os.path.basename(proj)} · 卡点表"
    L = [f"# {title}", ""]
    L.append(f"- {tl['width']}×{tl['height']},{fps} fps,共 {D} 帧({tc(D)},{D / fps:.2f} s);时间码 = 分:秒.百分秒。")
    if g:
        L.append(f"- 配乐 {g['bpm']:.2f} BPM:一拍 {g['beat_frames']:.2f} 帧、一小节 {g['beat_frames'] * g['beats_per_bar']:.2f} 帧;"
                 f"「小节.拍」从 1.1 起算,「+」是后半拍。")
        L.append(f"- 「卡拍」= 切点离最近的 8 分音符 ≤ 2 帧。画面切点按设计比拍点提前 {g['lead_frames']} 帧(底鼓听感起音偏晚,画面宁早不晚);音效不提前。")
    if accents is not None:
        L.append(f"- 「重音」= 切点 ±{HIT_WIN} 帧内有强重音(配乐里强度百分位 s ≥ {STRONG_S} 的底鼓 / 军鼓 / 大冲击,tempo.py 测);"
                 "#n 是重音序号,后面的数是切点比重音早(−)/ 晚(+)几帧。")
    L.append("- 配乐、音效都可以按这张表在其他剪辑软件里替换。")

    st = tl.get("stats") or {}
    sbits = []
    ah = st.get("accent_hit")
    if ah and ah.get("n"):
        sbits.append(f"硬切落在强重音上:{'drop 段' if ah['scope'] == 'drop' else '主轨'} {ah['hits']}/{ah['n']}({ah['rate']:.0%})"
                     + (f",全片 {ah['all_hits']}/{ah['all_n']}" if ah.get("scope") == "drop" and ah.get("all_n") else ""))
    dp = st.get("drop")
    if dp:
        sbits.append(f"{'drop 段' if dp['scope'] == 'drop' else '主轨'} {dp['shots']} 镜,平均 {dp['avg_s']:.2f} s,变速坡 {len(dp['ramps'])} 处"
                     + (f"({'、'.join(dp['ramps'])})" if dp["ramps"] else ""))
    if st.get("main_src_shots"):
        sbits.append(f"主轨用了 {st['distinct_src']} 条不同素材 / {st['main_src_shots']} 镜")
    if st.get("reuse"):
        sbits.append("复用 ≥ 3 次:" + ";".join(f"`{os.path.basename(r['src'])}` × {r['uses']}" for r in st["reuse"]))
    if sbits:
        L += ["", "## 节奏统计", ""] + [f"- {b}" for b in sbits]

    secs = tl.get("sections") or []
    if secs:
        L += ["", "## 段落", "", "| 段 | 类型 | 起止 | 帧 |" + (" 小节 |" if g else ""), "|---|---|---|---|" + ("---|" if g else "")]
        for s in secs:
            L.append(f"| {s['name']} | {KIND_NAME.get(s.get('kind', 'other'), s.get('kind', ''))} | {tc(s['from'])}–{tc(s['to'])} | {s['from']}–{s['to']} |"
                     + (f" {pos(s['from']).split('.')[0]}–{pos(max(s['from'], s['to'] - 1)).split('.')[0]} |" if g else ""))

    m = tl["audio"].get("music")
    if m:
        L += ["", "## 配乐", ""]
        L.append(f"- 文件 `{m['src']}`,从第 {m['from']} 帧开始放" + (f",跳过原曲开头 {m['trim_s']:.3f} s" if m.get("trim_s") else "")
                 + f";音量 {m['gain']:g}。")
        if len(m.get("gain_keys", [])) > 1:
            L.append("- 音量关键点:" + ",".join(f"{tc(k)}(帧 {k})→ {v:g}" for k, v in m["gain_keys"]))
        if m.get("duck_ranges"):
            L.append(f"- 人声段自动压低 {abs(m['duck_db']):g} dB(压下 {m['attack']} 帧、恢复 {m['release']} 帧),共 {len(m['duck_ranges'])} 段:"
                     + ",".join(f"{tc(a_)}–{tc(b_)}" for a_, b_ in m["duck_ranges"][:12]) + (" …" if len(m["duck_ranges"]) > 12 else ""))
        edl_p = m.get("edl")
        if edl_p:
            cands = [edl_p, os.path.join(proj, edl_p), os.path.join(proj, "public", edl_p), os.path.join(proj, "work", edl_p)]
            ep = next((c for c in cands if os.path.exists(c)), None)
            if ep:
                edl = json.load(open(ep, encoding="utf-8"))
                L += ["", f"按小节重剪(`{os.path.basename(ep)}`):", "", "| # | 源区间 s | 成片起点 | 说明 |", "|---|---|---|---|"]
                t = m["from"] / fps
                for i, s in enumerate(edl):
                    L.append(f"| {i + 1} | {s['from']:.3f}–{s['to']:.3f} | {tc(t * fps)} | {s.get('_', s.get('note', ''))} |")
                    t += s["to"] - s["from"]
            else:
                L.append(f"- (EDL `{edl_p}` 没找到)")

    def shot_rows(shots, layer_col=False):
        acc_col = accents is not None and not layer_col
        head = ("| 镜 |" + (" 层 |" if layer_col else "") + " 帧 | 时间 |" + (" 小节.拍 | 卡拍 |" if g else "")
                + (" 重音 |" if acc_col else "") + " 进场 | 画面 | 素材 · 源秒 · 速度 |")
        rows = [head, "|" + "---|" * (head.count("|") - 1)]
        for s in shots:
            src = f"`{os.path.basename(s['src'])}` · {s['src_in_s']:.2f} · {speed(s)}" if s.get("src") else "黑场"
            note = (s.get("note") or "").replace("|", "/")
            if s.get("hold_from") is not None:
                note += f"(帧 {s['hold_from']} 起定格)"
            if s.get("snapped") and s["snapped"].get("from") != s["snapped"].get("to"):
                note += f"(切点从 {s['snapped']['from']} 吸附到重音)"
            if s.get("allow_reuse"):
                note += "(允许复用)"
            rows.append(f"| {s['id']} |" + (f" {s['layer']} |" if layer_col else "") + f" {s['cut']}–{s['end']} | {tc(s['cut'])} |"
                        + (f" {pos(s['cut'])} | {onbeat(s['cut'])} |" if g else "")
                        + (f" {accent_cell(s)} |" if acc_col else "") + f" {tr(s)} | {note} | {src} |")
        return rows

    main = [s for s in tl["shots"] if s["layer"] == 0]
    ins = [s for s in tl["shots"] if s["layer"] > 0]
    L += ["", f"## 逐镜(主轨 {len(main)} 镜)", ""] + shot_rows(main)
    if ins:
        L += ["", f"## 插入镜头({len(ins)} 镜,盖在主轨上)", ""] + shot_rows(ins, True)

    ramps = [s for s in tl["shots"] if isinstance(s.get("speed"), list) and len({v for _, v in s["speed"]}) > 1]
    if ramps:
        def sec_of(f):
            for sc in secs:
                if sc["from"] <= f < sc["to"]:
                    return sc["name"]
            return "—"
        L += ["", f"## 变速镜头({len(ramps)} 个)", "",
              "关键点换算成成片帧;坡心(慢放段中点)应落在拍上。", "",
              "| 段 | 镜 | 帧 | 速度曲线(成片帧 → 倍速" + (" @ 小节.拍" if g else "") + ") | 慢放段中点 |", "|---|---|---|---|---|"]
        for s in sorted(ramps, key=lambda s: s["cut"]):
            n = s["end"] - s["cut"]
            pts = [(s["cut"] + int(round(p * n)), v) for p, v in s["speed"]]
            curve = " → ".join(f"{f}:{v:g}×" + (f"@{pos(f)}" if g else "") for f, v in pts)
            lo = min(v for _, v in pts)
            slow = [f for f, v in pts if v == lo]
            mid = int(round((slow[0] + slow[-1]) / 2))
            L.append(f"| {sec_of(s['cut'])} | {s['id']} | {s['cut']}–{s['end']} | {curve} | 帧 {mid}" + (f"({pos(mid)},{onbeat(mid)})" if g else "") + " |")

    if tl["texts"]:
        L += ["", "## 文字 / 包装", "", "| id | 类型 | 帧 | 时间 |" + (" 小节.拍 |" if g else "") + " 内容 |",
              "|---|---|---|---|" + ("---|" if g else "") + "---|"]
        for t in tl["texts"]:
            L.append(f"| {t['id']} | {t['type']} | {t['from']}–{t['to']} | {tc(t['from'])} |" + (f" {pos(t['from'])} |" if g else "")
                     + f" {text_of(t)} |")
    if tl.get("captions"):
        c = tl["captions"]
        L += ["", f"字幕 {len(c)} 条({tc(c[0]['from'])}–{tc(c[-1]['to'])}),全文见 `src/timeline.json` 的 captions。"]
    voice = tl["audio"].get("voice") or []
    if voice:
        L += ["", "## 原声", "", "| 镜 | 帧 | 时间 | 音量 |", "|---|---|---|---|"]
        for v in voice:
            L.append(f"| {v['shot']} | {v['from']}–{v['to']} | {tc(v['from'])}–{tc(v['to'])} | {v['gain']:g} |")
    sfx = tl["audio"].get("sfx") or []
    if sfx:
        L += ["", f"## 音效({len(sfx)} 个)", "", "| 帧 | 时间 |" + (" 小节.拍 |" if g else "") + " 音效 | 音量 | 说明 |",
              "|---|---|" + ("---|" if g else "") + "---|---|---|"]
        for x in sfx:
            L.append(f"| {x['from']} | {tc(x['from'])} |" + (f" {pos(x['from'], False)} |" if g else "")
                     + f" `{os.path.splitext(os.path.basename(x.get('src') or x['id']))[0]}` | {x['gain']:g} | {x.get('note', '')} |")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"✓ {out}:主轨 {len(main)} 镜、插入 {len(ins)} 镜、变速 {len(ramps)} 镜、文字 {len(tl['texts'])}、音效 {len(sfx)}、原声 {len(voice)} 段")


if __name__ == "__main__":
    main()
