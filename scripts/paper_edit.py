#!/usr/bin/env python3
"""纸面剪辑:采访选段清单 → interview 镜头数组(粘进 edit.json)+ words.json(逐字时间换算到成片帧)。

用法:
  python3 paper_edit.py <proj> [--sel <proj>/work/paper_edit.json] [--transcripts <proj>/work/transcripts]
                        [--at 0] [--prefix IV] [--fix "错=对,…"] [--pad-in 0.10] [--pad-out 0.15]
  python3 paper_edit.py <proj> --from-sentences "interview/A001_C003.MP4:3-5,8,12-14"   按句号选段
        (句号就是 transcribe.py 写的 .sentences.md 每行开头的三位数;多个素材用分号隔开;
         会先把选段写成 work/paper_edit.json(旧的备份成 .bak),方便再手调秒数)

选段清单 work/paper_edit.json(秒数从 .sentences.md 抄,写个大概就行,会自动吸附到词间停顿):
  [{"src": "interview/A001_C003.MP4", "from_s": 12.3, "to_s": 20.1, "note": "入坑"},
   {"src": "interview/A001_C003.MP4", "sent": "15-17", "id": "IV_Q2"}]
  可选字段:id(镜头 id)、note、make_room("right" 给右侧信息卡让位)、sent(句号范围,代替 from_s / to_s)。

转写:每条素材要先有 <transcripts>/<文件名去扩展>.scribe.json(transcribe.py --out <proj>/work/transcripts <素材> 生成)。

产物:
  stdout + <proj>/work/paper_edit.shots.json   interview 镜头数组(layer 0、move interview、audio true、顺接;
                                                第一镜带 --at 给的 at,其余靠顺接)。粘进 edit.json 的 shots。
  <proj>/words.json   [{"t","f","e","src","shot","ss","se"}]:t 字(句末标点粘在前一个字上),f / e 成片帧(可为小数),
                      ss / se 源素材秒数。timeline.py 会按 shot + ss 重新定位,所以纸面剪辑之后在采访中间插 B-roll /
                      问题卡、改顺序,words.json 都不用重跑;只有改了选段本身才要重跑本脚本。

自动做的事:
  - 选段边界吸附到词间停顿:开头取「第一个字前 pad-in 秒」和「与上一个字的停顿中点」里靠后的;结尾对称。字不会被切掉。
    选段首尾的「呃 / 嗯」默认去掉(--keep-fillers 保留)。
  - 修正 Scribe 的零时长字(句末字常常 end = start,真实尾音被标点占了):补到 0.2 s 或下一个字开头。
  - 错字表:--fix 或 <proj>/work/fix.txt,跨词元的错字合成一个词元。
  - 检查句子完整性(合规删句最容易出事,访谈示例片第 3 题 就停在「来说」上):
      结尾落在「来说 / 就是 / 然后 / 因为 / 的话 …」这类没说完的词上、结尾是逗号、结尾后 0.25 s 内还在说话 → 警告;
      开头前 0.15 s 内还在说话(接在半句中间)→ 警告。
"""
import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import captions as CAP  # noqa: E402

PUNCT_ONLY = re.compile(r"^[\s，。、？！；：,.?!;:…「」『』“”\"'()（）-]+$")
DANGLING = ("来说", "就是", "然后", "因为", "所以", "但是", "而且", "还有", "的话", "比如", "包括", "如果", "虽然",
            "不过", "可是", "或者", "还是", "以及", "并且", "就", "和", "跟", "把", "被", "让", "对", "在", "从",
            "这个", "那个", "一个", "我们", "他们", "其实", "觉得", "等于", "是", "的", "地", "得")
COMMA_END = re.compile(r"[，、,；;：:]$")
STOP_END = re.compile(r"[。？！?!.…]$")


def norm_words(units):
    """Scribe words[] → [{"t","s","e"}]:只留 word;标点并到前一个字;英文词后面的空格保留;补零时长的字。"""
    out = []
    for u in units:
        typ = u.get("type", "word")
        if typ == "spacing":
            if out and " " in u.get("text", ""):
                out[-1]["sp"] = True
            continue
        if typ != "word":
            continue
        txt = u.get("text", "")
        if PUNCT_ONLY.match(txt):
            if out:
                out[-1]["t"] += txt.strip()
            continue
        out.append({"t": txt.strip(), "s": float(u["start"]), "e": float(u["end"]), "e0": float(u["end"])})
    for i, w in enumerate(out):
        nxt = out[i + 1]["s"] if i + 1 < len(out) else w["e"] + 0.3
        if w["e"] - w["s"] < 0.06:
            w["e"] = max(w["e"], min(w["s"] + 0.2, nxt - 0.01))
        if w.pop("sp", False) and i + 1 < len(out):
            a, b = w["t"][-1:], out[i + 1]["t"][:1]
            if a.isascii() and a.isalnum() and b.isascii() and b.isalnum():
                w["t"] += " "
    return out


def find_transcript(src, dirs):
    stem = os.path.splitext(os.path.basename(src))[0]
    for d in dirs:
        p = os.path.join(d, stem + ".scribe.json")
        if os.path.exists(p):
            return p
    return None


def sentence_span(scribe, rng, fps):
    """句号范围 "3-5" → (起秒, 止秒),编号和 transcribe.py 的 .sentences.md 一致。"""
    import transcribe  # 同目录
    sentences, _, _ = transcribe.build(scribe["words"], fps, 16, 0.5)
    a, _, b = str(rng).partition("-")
    a, b = int(a), int(b or a)
    if not (1 <= a <= b <= len(sentences)):
        raise ValueError(f"句号 {rng} 超出范围(共 {len(sentences)} 句)")
    seg = [s for s in sentences[a - 1:b] if not s[2].startswith("[")]
    if not seg:
        raise ValueError(f"句号 {rng} 全是声音事件")
    return seg[0][0], seg[-1][1], "".join(s[2] for s in seg)


FILLER_TOK = re.compile(r"^[呃嗯额啊哎][\s，。、？！；：,.?!;:…]*$")


def snap(W, t0, t1, pad_in, pad_out, skip_fillers=True):
    """返回 (起秒, 止秒, 首字下标, 末字下标, 开头停顿, 结尾停顿)。
    选哪些字按 Scribe 原始时间的中点判断(句末零时长的字也算在内);首尾的「呃 / 嗯」默认去掉。"""
    mid = lambda w: (w["s"] + w.get("e0", w["e"])) / 2
    i0 = next((i for i, w in enumerate(W) if mid(w) >= t0 - 0.01), None)
    i1 = next((i for i in range(len(W) - 1, -1, -1) if mid(W[i]) <= t1 + 0.01), None)
    if i0 is None or i1 is None or i1 < i0:
        return None
    if skip_fillers:
        while i0 < i1 and FILLER_TOK.match(W[i0]["t"]):
            i0 += 1
        while i1 > i0 and FILLER_TOK.match(W[i1]["t"]):
            i1 -= 1
    w0, w1 = W[i0], W[i1]
    prev_e = W[i0 - 1]["e"] if i0 > 0 else max(0.0, w0["s"] - 1.0)
    lo = (prev_e + w0["s"]) / 2 if i0 > 0 else max(0.0, w0["s"] - 0.5)
    start = max(min(t0, w0["s"] - pad_in), lo)
    next_s = W[i1 + 1]["s"] if i1 + 1 < len(W) else w1["e"] + 1.0
    hi = (w1["e"] + next_s) / 2 if i1 + 1 < len(W) else w1["e"] + 0.5
    end = min(max(t1, w1["e"] + pad_out), hi)
    end = max(end, w1["e"] + 0.02) if i1 + 1 >= len(W) or next_s > w1["e"] + 0.04 else end
    return start, end, i0, i1, w0["s"] - prev_e, next_s - w1["e"]


def main():
    ap = argparse.ArgumentParser(description="纸面剪辑 → interview 镜头 + words.json")
    ap.add_argument("proj")
    ap.add_argument("--sel", default=None, help="选段清单(默认 <proj>/work/paper_edit.json)")
    ap.add_argument("--from-sentences", default=None, help='按句号选段:"素材:3-5,8;素材2:2"')
    ap.add_argument("--transcripts", default=None, help="转写目录(默认 <proj>/work/transcripts,其次 <proj>/work)")
    ap.add_argument("--at", type=int, default=None, help="第一段放在成片第几帧(默认 0,且不写 at,靠顺接)")
    ap.add_argument("--prefix", default="IV")
    ap.add_argument("--fix", default="", help="错字表「错=对,…」")
    ap.add_argument("--pad-in", type=float, default=0.10)
    ap.add_argument("--pad-out", type=float, default=0.15)
    ap.add_argument("--fps", type=int, default=None)
    ap.add_argument("--keep-fillers", action="store_true", help="选段首尾的「呃 / 嗯」不自动去掉")
    a = ap.parse_args()
    proj = os.path.abspath(a.proj)
    pj = {}
    if os.path.exists(os.path.join(proj, "project.json")):
        pj = json.load(open(os.path.join(proj, "project.json"), encoding="utf-8"))
    fps = a.fps or int(pj.get("fps", 30))
    tdirs = [a.transcripts] if a.transcripts else [os.path.join(proj, "work", "transcripts"), os.path.join(proj, "work")]
    sel_path = a.sel or os.path.join(proj, "work", "paper_edit.json")

    if a.from_sentences:
        sel = []
        for part in a.from_sentences.split(";"):
            if not part.strip():
                continue
            src, _, rngs = part.rpartition(":")
            for r in rngs.split(","):
                if r.strip():
                    sel.append({"src": src.strip(), "sent": r.strip()})
        os.makedirs(os.path.dirname(sel_path), exist_ok=True)
        if os.path.exists(sel_path):
            shutil.copy(sel_path, sel_path + ".bak")
        tmp = []
        for e in sel:
            tp = find_transcript(e["src"], tdirs)
            if not tp:
                sys.exit(f"找不到 {e['src']} 的转写(.scribe.json),先跑 transcribe.py --out {tdirs[0]}")
            s0, s1, txt = sentence_span(json.load(open(tp, encoding="utf-8")), e["sent"], fps)
            tmp.append({"src": e["src"], "from_s": round(s0, 3), "to_s": round(s1, 3), "sent": e["sent"], "text": txt})
        sel = tmp
        json.dump(sel, open(sel_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"选段写进 {sel_path}")
    else:
        if not os.path.exists(sel_path):
            sys.exit(f"找不到选段清单 {sel_path}(或用 --from-sentences)")
        sel = json.load(open(sel_path, encoding="utf-8"))

    fixes = CAP.parse_fix(a.fix, proj)
    cache = {}
    shots, words, warns = [], [], []
    cur = a.at or 0
    for k, e in enumerate(sel):
        sid = e.get("id") or f"{a.prefix}{k + 1:02d}"
        tp = find_transcript(e["src"], tdirs)
        if not tp:
            sys.exit(f"找不到 {e['src']} 的转写(.scribe.json),先跑 transcribe.py --out {tdirs[0]} <素材>")
        if tp not in cache:
            sj = json.load(open(tp, encoding="utf-8"))
            cache[tp] = (sj, norm_words(sj["words"]))
        sj, W = cache[tp]
        if "sent" in e and ("from_s" not in e or "to_s" not in e):
            t0, t1, _ = sentence_span(sj, e["sent"], fps)
        else:
            t0, t1 = float(e["from_s"]), float(e["to_s"])
        r = snap(W, t0, t1, a.pad_in, a.pad_out, not a.keep_fillers)
        if not r:
            sys.exit(f"{sid}:{e['src']} {t0}–{t1}s 里没有字")
        start, end, i0, i1, gap_in, gap_out = r
        nf = int(round((end - start) * fps))
        seg_words = [dict(w) for w in W[i0:i1 + 1]]
        seg_words = CAP.apply_fix_words([{"t": w["t"], "f": w["s"], "e": w["e"]} for w in seg_words], fixes)
        text = "".join(w["t"] for w in seg_words)
        # 句子完整性
        last = re.sub(r"[\s，。、？！；：,.?!;:…]+$", "", text)
        w_msgs = []
        if not STOP_END.search(text.strip()):
            dang = next((d for d in DANGLING if last.endswith(d)), None)
            if dang:
                w_msgs.append(f"结尾停在「{dang}」上,像是半句话")
            elif COMMA_END.search(text.strip()):
                w_msgs.append("结尾是逗号,句子可能没说完")
            if gap_out < 0.25:
                w_msgs.append(f"结尾后 {gap_out:.2f}s 就接着说下一个字,可能切在半句中间")
        if gap_in < 0.15 and i0 > 0:
            prev_t = W[i0 - 1]["t"]
            if not STOP_END.search(prev_t) and not COMMA_END.search(prev_t):
                w_msgs.append(f"开头前 {gap_in:.2f}s 还在说「{prev_t}」,可能接在半句中间")
        for m in w_msgs:
            warns.append(f"{sid}:{m}")
        note = e.get("note") or text[:24]
        for wrong, right in fixes.items():
            note = note.replace(wrong, right)
        shot = {"id": sid, "src": e["src"], "in": round(start, 3), "dur": nf, "layer": 0, "move": "interview",
                "audio": True, "note": note}
        if k == 0 and a.at is not None:
            shot = {"id": sid, "src": e["src"], "in": round(start, 3), "at": a.at, "dur": nf, "layer": 0,
                    "move": "interview", "audio": True, "note": shot["note"]}
        if e.get("make_room"):
            shot["make_room"] = e["make_room"]
        shots.append(shot)
        for w in seg_words:
            words.append({"t": w["t"], "f": round(cur + (w["f"] - start) * fps, 1), "e": round(cur + (w["e"] - start) * fps, 1),
                          "src": e["src"], "shot": sid, "ss": round(w["f"], 3), "se": round(w["e"], 3)})
        print(f"{sid}  {e['src']}  源 {start:7.2f}–{end:7.2f}s(选 {t0:.2f}–{t1:.2f})→ 成片 {cur}–{cur + nf}({nf} 帧)")
        print(f"      {text}")
        for m in w_msgs:
            print(f"      ⚠️ {m}")
        cur += nf

    os.makedirs(os.path.join(proj, "work"), exist_ok=True)
    sp = os.path.join(proj, "work", "paper_edit.shots.json")
    json.dump(shots, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    wp = os.path.join(proj, "words.json")
    json.dump(words, open(wp, "w", encoding="utf-8"), ensure_ascii=False)
    print("\n# 粘进 edit.json 的 shots:")
    print("[\n" + ",\n".join("  " + json.dumps(s, ensure_ascii=False) for s in shots) + "\n]")
    print(f"\n✓ {len(shots)} 段,共 {cur - (a.at or 0)} 帧({(cur - (a.at or 0)) / fps:.1f} s),{len(words)} 个字 → {wp};镜头数组 → {sp}")
    if warns:
        print(f"⚠️ {len(warns)} 处句子可能不完整,听一下再定:")
        for m in warns:
            print("   " + m)


if __name__ == "__main__":
    main()
