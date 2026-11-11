#!/usr/bin/env python3
"""卡点表(交付物):<proj>/src/timeline.json → Markdown。给客户 / 剪辑师对照,方便在剪映等软件里按表替换配乐、音效。

用法:
  python3 cuesheet.py <proj> [--out <proj>/out/卡点表.md] [--title "燃剪示例 · 卡点表(主版)"]

内容:
  - 规格:帧率、总帧数、时长;有拍网格时写 BPM、一拍 / 一小节多少帧,「小节.拍」从 1.1 起算(「+」= 后半拍)。
  - 段落(edit.json 的 sections)。
  - 配乐:文件、起点、trim、音量关键点、压低区间数;audio.music.edl 指向 bar_edit.py 的 EDL 时逐段列出源区间。
  - 逐镜(主轨)+ 插入镜头:帧、时间、小节.拍、卡拍(切点离最近的 8 分音符 ≤ 2 帧算 ✓;已扣掉 lead 提前量)、
    进场转场、画面说明(note)、素材 · 源秒 · 速度。
  - 文字 / 包装、原声段、音效(音效不扣 lead)。字幕只给条数(全文在 timeline.json)。
不改任何文件,只写 --out。
"""
import argparse
import json
import math
import os

TR_NAME = {"cut": "硬切", "dip": "黑场过渡", "fade": "叠化", "whip": "甩镜", "punch": "冲击推镜", "sweep": "光扫", "wipe": "擦入"}


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

    def tc(f):
        return f"{int(f // fps // 60)}:{f / fps % 60:05.2f}"

    def bp_of(f, lead):
        return ((f + (g["lead_frames"] if lead else 0) + 0.5) / fps - g["offset_s"]) / g["beat_s"]

    def pos(f, lead=True):
        if not g:
            return ""
        bp = bp_of(f, lead)
        bpb = g["beats_per_bar"]
        bar = math.floor(bp / bpb)
        inbar = bp - bar * bpb
        return f"{bar + 1}.{int(inbar) + 1}" + ("+" if inbar % 1 >= 0.45 else "")

    def onbeat(f):
        if not g:
            return ""
        bp = ((f + g["lead_frames"]) / fps - g["offset_s"]) / g["beat_s"]
        near = round(bp * 2) / 2
        gf = int(round((g["offset_s"] + near * g["beat_s"]) * fps)) - g["lead_frames"]
        d = f - max(0, gf)
        return "✓" if abs(d) <= 2 else f"{d:+d} 帧"

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
        if typ in ("slam", "brushword", "chapter", "reveal"):
            return t.get("text") or t.get("image", "")
        if typ == "qcard":
            return f"Q{t.get('no', '')}:" + " / ".join(t.get("lines", []))
        if typ == "lower":
            return " · ".join(x for x in (t.get("name"), t.get("title")) if x)
        if typ == "spec":
            return f"{t.get('big', '')}{t.get('unit', '')} · {t.get('label', '')}" + (f" · {t['sub']}" if t.get("sub") else "")
        if typ == "lockup":
            return " · ".join(t.get("lines", [])) + (" + Logo" if t.get("logo") else "")
        if typ == "endcard":
            return "官方 Logo:" + "、".join(os.path.basename(x) for x in t.get("logos", []))
        if typ == "image":
            return os.path.basename(t.get("src", ""))
        return ""

    title = a.title or f"{tl.get('title') or os.path.basename(proj)} · 卡点表"
    L = [f"# {title}", ""]
    L.append(f"- {tl['width']}×{tl['height']},{fps} fps,共 {D} 帧({tc(D)},{D / fps:.2f} s);时间码 = 分:秒.百分秒。")
    if g:
        L.append(f"- 配乐 {g['bpm']:.2f} BPM:一拍 {g['beat_frames']:.2f} 帧、一小节 {g['beat_frames'] * g['beats_per_bar']:.2f} 帧;"
                 f"「小节.拍」从 1.1 起算,「+」是后半拍。")
        L.append(f"- 「卡拍」= 切点离最近的 8 分音符 ≤ 2 帧。画面切点按设计比拍点提前 {g['lead_frames']} 帧(底鼓听感起音偏晚,画面宁早不晚);音效不提前。")
    L.append("- 配乐、音效都可以按这张表在其他剪辑软件里替换。")
    secs = tl.get("sections") or []
    if secs:
        L += ["", "## 段落", "", "| 段 | 起止 | 帧 |" + (" 小节 |" if g else ""), "|---|---|---|" + ("---|" if g else "")]
        for s in secs:
            L.append(f"| {s['name']} | {tc(s['from'])}–{tc(s['to'])} | {s['from']}–{s['to']} |"
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
        head = "| 镜 |" + (" 层 |" if layer_col else "") + " 帧 | 时间 |" + (" 小节.拍 | 卡拍 |" if g else "") + " 进场 | 画面 | 素材 · 源秒 · 速度 |"
        rows = [head, "|" + "---|" * (head.count("|") - 1)]
        for s in shots:
            src = f"`{os.path.basename(s['src'])}` · {s['src_in_s']:.2f} · {speed(s)}" if s.get("src") else "黑场"
            note = (s.get("note") or "").replace("|", "/")
            if s.get("hold_from") is not None:
                note += f"(帧 {s['hold_from']} 起定格)"
            rows.append(f"| {s['id']} |" + (f" {s['layer']} |" if layer_col else "") + f" {s['cut']}–{s['end']} | {tc(s['cut'])} |"
                        + (f" {pos(s['cut'])} | {onbeat(s['cut'])} |" if g else "") + f" {tr(s)} | {note} | {src} |")
        return rows

    main = [s for s in tl["shots"] if s["layer"] == 0]
    ins = [s for s in tl["shots"] if s["layer"] > 0]
    L += ["", f"## 逐镜(主轨 {len(main)} 镜)", ""] + shot_rows(main)
    if ins:
        L += ["", f"## 插入镜头({len(ins)} 镜,盖在主轨上)", ""] + shot_rows(ins, True)
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
    print(f"✓ {out}:主轨 {len(main)} 镜、插入 {len(ins)} 镜、文字 {len(tl['texts'])}、音效 {len(sfx)}、原声 {len(voice)} 段")


if __name__ == "__main__":
    main()
