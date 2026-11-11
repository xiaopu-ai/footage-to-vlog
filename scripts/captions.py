#!/usr/bin/env python3
"""采访字幕:words.json(逐字时间,成片帧)→ 短句字幕 [{"text","from","to"}]。

用法(单独用):
  python3 captions.py <proj>/words.json [--max 17] [--gap 9] [--dict 词1,词2] [--proj <proj>]
                      [--fix "错=对,…"] [--out captions.json] [--srt captions.srt] [--fps 30]
  timeline.py 在 edit.json 的 captions.enabled 为 true 时 import 本文件的 make_captions(),结果并进 timeline.json。

断句规则(从 访谈示例片 v7 定版移植,用户意见:「钓鱼」被拆成上一条结尾和下一条开头):
  1. 先按标点和停顿断成分句:词元以 ,。?! 等结尾就断;前后两个字间隔 > gap 帧(默认 9 帧 ≈ 0.3 s)也断;
     words.json 带 shot 字段时,跨镜头(纸面剪辑的两段之间)也断。
  2. 分句超过 max(显示宽度:汉字 1、英文数字 0.55)才拆,而且只拆在「jieba 分词边界 ∩ 转写词元边界」上,
     尽量从中间拆;优先拆在「的 / 了 / 呢 / 啊 / 吧 / 嘛 / 呀」之后、「因为 / 但是 / 然后 / 直接 …」之前;两边各不少于 4 字。
  3. 开头的「呃 / 嗯」去掉,单独的语气词「呃嗯哎啊哦额」不出字幕;去掉句末标点。
     停顿切出来、不到 4 字的碎片(「我的 ‖ ATV ‖ 参加了」里的 ATV)并到前一条(或后一条);标点断开的短句不并。
  4. 每条从第一个字起,到最后一个字结束后 4 帧;至少 15 帧;相邻字幕不重叠(前一条截到后一条开头)。
专有名词:--dict 词1,词2,或 <proj>/work/dict.txt(每行一个或逗号分隔),加进 jieba 词典,保证不被拆开。
错字表:--fix "在坐=在座,以经=已经",或 <proj>/work/fix.txt(每行「错=对」);paper_edit.py 生成 words.json 时已经按词元改过一遍。

依赖:jieba(没装也能跑,退化成只在转写词元边界上拆,会提示)。
"""
import argparse
import json
import os
import re
import sys

PUNCT = "，。、？！；：,.?!;:…"
PUNCT_RE = re.compile("[" + re.escape(PUNCT) + "]")
END_RE = re.compile("[" + re.escape(PUNCT) + "]+$")
FILLER = re.compile(r"^[呃嗯哎啊哦额]+$")
LEAD_FILLER = re.compile(r"^([呃嗯额][，,]?)+")
AFTER = set("的了呢啊吧嘛呀")
BEFORE = ("因为", "但是", "然后", "直接", "包括", "无论", "还是", "可以", "所以", "并且", "就是", "让", "到", "做", "派")

_jieba = None
_jieba_words = set()


def _get_jieba(extra=()):
    global _jieba
    if _jieba is None:
        try:
            import jieba  # noqa
            jieba.setLogLevel(60)
            _jieba = jieba
        except ImportError:
            _jieba = False
            print("⚠️ 没装 jieba(pip install jieba),字幕只在转写词元边界上拆,可能拆开词", file=sys.stderr)
    if _jieba:
        for w in extra:
            if w and w not in _jieba_words:
                _jieba.add_word(w)
                _jieba_words.add(w)
    return _jieba


def width(text):
    """显示宽度:汉字 / 全角 = 1,英文数字空格 ≈ 0.55"""
    return sum(1 if ord(c) > 0x2E80 else 0.55 for c in text)


def clean(t):
    return PUNCT_RE.sub("", t)


def load_dict(proj=None, extra=""):
    words = [w.strip() for w in (extra or "").split(",") if w.strip()]
    if proj:
        p = os.path.join(proj, "work", "dict.txt")
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                words += [w.strip() for w in re.split(r"[,，\s]+", line) if w.strip() and not w.startswith("#")]
    return words


def parse_fix(s="", proj=None):
    """「错=对,错2=对2」→ dict;再合并 <proj>/work/fix.txt(每行「错=对」)。"""
    out = {}
    items = [x for x in re.split(r"[,，]", s or "") if "=" in x]
    if proj:
        p = os.path.join(proj, "work", "fix.txt")
        if os.path.exists(p):
            items = [ln.strip() for ln in open(p, encoding="utf-8") if "=" in ln and not ln.startswith("#")] + items
    for it in items:
        a, b = it.split("=", 1)
        if a.strip():
            out[a.strip()] = b.strip()
    return out


def apply_fix_words(words, fixes):
    """按错字表改词元:错字跨几个词元时合成一个词元(时间取首尾),保证 say 能搜到、字幕不拆开。"""
    if not fixes:
        return words
    out = [dict(w) for w in words]
    for wrong, right in sorted(fixes.items(), key=lambda kv: -len(kv[0])):
        texts = [w["t"] for w in out]
        full = "".join(texts)
        if wrong not in full:
            continue
        starts, n = [], 0
        for t in texts:
            starts.append(n)
            n += len(t)
        hits, pos = [], full.find(wrong)
        while pos >= 0:
            hits.append(pos)
            pos = full.find(wrong, pos + len(wrong))
        for pos in reversed(hits):
            end = pos + len(wrong)
            i = max(k for k in range(len(out)) if starts[k] <= pos)
            j = max(k for k in range(len(out)) if starts[k] < end)
            merged = "".join(out[k]["t"] for k in range(i, j + 1))
            off = pos - starts[i]
            merged = merged[:off] + right + merged[off + len(wrong):]
            nw = dict(out[i])
            nw["t"] = merged
            for key in ("e", "se"):
                if key in out[j]:
                    nw[key] = out[j][key]
            out[i:j + 1] = [nw]
            starts[i:j + 1] = [starts[i]]
            # 后面的起点整体偏移(只影响更靠前的命中,倒序处理时不再用到)
    return out


def split_clause(toks, maxw, min_side=4):
    """一个分句的词元 → 若干段(每段宽度 ≤ maxw,只在词边界拆)。"""
    texts = [clean(w["t"]) for w in toks]
    full = "".join(texts)
    if width(full.strip()) <= maxw or len(toks) < 2:
        return [toks]
    tokb, n = set(), 0
    for t in texts[:-1]:
        n += len(t)
        tokb.add(n)
    jb = _get_jieba()
    wordb = set()
    if jb:
        n = 0
        for wd in jb.lcut(full)[:-1]:
            n += len(wd)
            wordb.add(n)
    cands = sorted(p for p in (tokb & wordb if wordb else tokb) if 0 < p < len(full))
    if not cands:
        cands = sorted(p for p in tokb if 0 < p < len(full))
    if not cands:
        return [toks]
    L = width(full)

    def cost(pos):
        wp = width(full[:pos])
        c = abs(wp - L / 2)
        if full[pos - 1] in AFTER:
            c -= 3
        if full[pos:].startswith(BEFORE):
            c -= 3
        if wp < min_side or L - wp < min_side:
            c += 6
        return c

    pos = min(cands, key=cost)
    n, k = 0, 0
    while n < pos and k < len(texts):
        n += len(texts[k])
        k += 1
    if k <= 0 or k >= len(toks):
        return [toks]
    return split_clause(toks[:k], maxw, min_side) + split_clause(toks[k:], maxw, min_side)


def make_captions(words, max_w=17, gap=9, dict_words=(), fixes=None, tail=4, min_frames=15):
    """words: [{"t","f","e", 可选 "shot"}](f / e 是成片帧,可以是小数)→ [{"text","from","to"}]"""
    _get_jieba(dict_words)
    fixes = fixes or {}
    words = sorted((w for w in words if str(w.get("t", "")).strip()), key=lambda w: w["f"])
    caps, clause = [], []

    def emit(toks, punct):
        txt = "".join(w["t"] for w in toks).strip()
        txt = LEAD_FILLER.sub("", txt)
        txt = END_RE.sub("", txt).replace("。", "，").strip()
        for a, b in fixes.items():
            txt = txt.replace(a, b)
        if txt and not FILLER.match(clean(txt).strip()):
            caps.append({"text": txt, "from": int(toks[0]["f"]), "to": int(toks[-1]["e"]) + tail,
                         "_e": toks[-1]["e"], "_punct": punct, "_shot": toks[-1].get("shot")})

    def flush(punct=False):
        if not clause:
            return
        parts = split_clause(list(clause), max_w)
        for k, part in enumerate(parts):
            emit(part, punct if k == len(parts) - 1 else False)
        clause.clear()

    for w in words:
        if clause and (w["f"] - clause[-1]["e"] > gap or ("shot" in w and w.get("shot") != clause[-1].get("shot"))):
            flush()
        clause.append(w)
        if END_RE.search(w["t"].strip()):
            flush(True)
    flush()

    # 停顿切出来的碎片(不到 4 字,比如「我的 / ATV / 参加了…」中间的 ATV)并到前一条或后一条:
    # 只并不是标点断开的、间隔不超过 2 × gap、并完不超过 max 的;标点断开的短句(「您好,」)保留。
    def join(a, b):
        sep = " " if a[-1:].isascii() and a[-1:].isalnum() and b[:1].isascii() and b[:1].isalnum() else ""
        return a + sep + b

    i = 0
    while i < len(caps):
        c = caps[i]
        if width(c["text"]) < 4:
            p = caps[i - 1] if i > 0 else None
            n = caps[i + 1] if i + 1 < len(caps) else None
            if (p and not p["_punct"] and p["_shot"] == c["_shot"] and c["from"] - p["_e"] <= 2 * gap
                    and width(join(p["text"], c["text"])) <= max_w):
                p.update({"text": join(p["text"], c["text"]), "to": c["to"], "_e": c["_e"], "_punct": c["_punct"]})
                del caps[i]
                continue
            if (n and not c["_punct"] and n["_shot"] == c["_shot"] and n["from"] - c["_e"] <= 2 * gap
                    and width(join(c["text"], n["text"])) <= max_w):
                n.update({"text": join(c["text"], n["text"]), "from": c["from"]})
                del caps[i]
                continue
        i += 1
    for c in caps:
        for k in ("_e", "_punct", "_shot"):
            c.pop(k, None)
    for a, b in zip(caps, caps[1:]):
        a["to"] = max(a["to"], a["from"] + min_frames)
        if a["to"] > b["from"]:
            a["to"] = b["from"]
    if caps:
        caps[-1]["to"] = max(caps[-1]["to"], caps[-1]["from"] + min_frames)
    return caps


def to_srt(caps, fps):
    def ts(f):
        ms = int(round(f / fps * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "".join(f"{i}\n{ts(c['from'])} --> {ts(c['to'])}\n{c['text']}\n\n" for i, c in enumerate(caps, 1))


def main():
    ap = argparse.ArgumentParser(description="words.json → 字幕")
    ap.add_argument("words")
    ap.add_argument("--max", type=float, default=17, help="每条最大显示宽度(汉字数)")
    ap.add_argument("--gap", type=float, default=9, help="字间隔超过几帧就断句")
    ap.add_argument("--dict", default="", help="专有名词,逗号分隔")
    ap.add_argument("--proj", default=None, help="项目目录(读 work/dict.txt、work/fix.txt)")
    ap.add_argument("--fix", default="", help="错字表「错=对,…」")
    ap.add_argument("--out", default=None, help="captions.json(默认打印)")
    ap.add_argument("--srt", default=None)
    ap.add_argument("--fps", type=float, default=30)
    a = ap.parse_args()
    words = json.load(open(a.words, encoding="utf-8"))
    proj = a.proj or os.path.dirname(os.path.abspath(a.words))
    caps = make_captions(words, a.max, a.gap, load_dict(proj, a.dict), parse_fix(a.fix, proj))
    long_ = [c for c in caps if width(c["text"]) > a.max]
    if a.out:
        json.dump(caps, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    if a.srt:
        open(a.srt, "w", encoding="utf-8").write(to_srt(caps, a.fps))
    for c in caps if not a.out else caps[:12]:
        print(f"{c['from']:6d}–{c['to']:<6d} {c['text']}")
    print(f"✓ {len(caps)} 条字幕" + (f",{len(long_)} 条超过 {a.max:g} 字:" + "、".join(c["text"] for c in long_) if long_ else "")
          + (f" → {a.out}" if a.out else ""))


if __name__ == "__main__":
    main()
