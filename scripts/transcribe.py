#!/usr/bin/env python3
"""口播转写:抽音频 → ElevenLabs Scribe v2 → 逐字时间码 JSON + 逐句文稿(秒 + 帧)+ SRT 字幕 + 停顿清单。

用法(先在命令里加载 key:set -a && . ./.env && set +a):
  python3 scripts/transcribe.py <视频或音频...> [--out DIR] [--keyterms "Cursor,Obsidian,vibe coding"] [--lang zh|en]
                                     [--fps 30] [--max-chars 16] [--pause 0.5] [--force]

产物(每个输入一组,默认放在输入文件旁边;--out 指定目录):
  <名>.scribe.json    原始返回(words[]: text / type=word|spacing|audio_event / start / end / logprob)
  <名>.sentences.md   逐句文稿:[起–止 | 起帧–止帧] 句子;声音事件单独成行;末尾附停顿清单(剪口播用)
  <名>.srt            字幕:按标点切成短句,每条显示宽度不超过 --max-chars 个汉字(英文字母按 0.55 算;禁折行),去掉句末标点

已有 .scribe.json 时直接复用、不再调接口(省钱);要重转加 --force。
坑(2026-09-29 实测):中文按单字给时间码;拼句要把 spacing 单元一起拼,否则英文词会粘在一起;
专有名词(产品名、学校缩写)用 --keyterms 提前告诉模型。
"""
import argparse
import json
import os
import re
import subprocess
import tempfile

FF = (os.environ.get("FFMPEG") or __import__("shutil").which("ffmpeg") or "ffmpeg")
API = "https://api.elevenlabs.io/v1/speech-to-text"
END = re.compile(r"[。？！?!.]$")  # 英文句点也断句(--lang en)
CLAUSE = re.compile(r"[，,。？！?!；;：:、.]$")
PUNCT = "，,。？！?!；;：:、 "


def ts(s):
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


def srt_ts(s):
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def transcribe(src, out_json, keyterms, lang="zh"):
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise SystemExit("缺 ELEVENLABS_API_KEY:命令前加 set -a && . ./.env && set +a")
    with tempfile.TemporaryDirectory() as tmp:
        mp3 = os.path.join(tmp, "a.mp3")
        subprocess.run([FF, "-y", "-loglevel", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", mp3], check=True)
        cmd = ["curl", "-sS", "--fail-with-body", "-X", "POST", API, "-H", f"xi-api-key: {key}",
               "-F", "model_id=scribe_v2", "-F", f"file=@{mp3}", "-F", f"language_code={lang}",
               "-F", "timestamps_granularity=word", "-F", "tag_audio_events=true", "-o", out_json]
        for k in keyterms:
            cmd += ["-F", f"keyterms={k}"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            msg = open(out_json, encoding="utf-8").read()[:300] if os.path.exists(out_json) else r.stderr
            if os.path.exists(out_json):
                os.remove(out_json)
            raise SystemExit(f"转写失败:{msg}")


def width(text):
    """显示宽度:汉字 / 全角 = 1,英文数字等半角 ≈ 0.55(字幕限长按宽度算,英文词不会被切太碎)"""
    return sum(1 if ord(c) > 0x2E80 else 0.55 for c in text)


def build(words, fps, max_chars, pause):
    """返回 (逐句列表, 字幕列表, 停顿列表)。句子 = [(start, end, text)];事件句 text 以 [ 开头。"""
    sentences, cues, cur = [], [], []

    def flush():
        ws = [x for x in cur if x["type"] == "word"]
        if ws:
            sentences.append((ws[0]["start"], ws[-1]["end"], "".join(x["text"] for x in cur).strip()))
        cur.clear()

    for x in words:
        if x["type"] == "audio_event":
            flush()
            sentences.append((x["start"], x["end"], x["text"]))
            continue
        cur.append(x)
        if x["type"] == "word" and END.search(x["text"]):
            flush()
    flush()

    # 字幕:在逗号级标点处断,超长再按字数均分
    clause = []

    def emit(units):
        text = "".join(u["text"] for u in units).strip().strip(PUNCT)
        ws = [u for u in units if u["type"] == "word"]
        if text and ws:
            cues.append((ws[0]["start"], ws[-1]["end"], text))

    def flush_clause():
        n = sum(width(u["text"]) for u in clause if u["type"] == "word")
        if n <= max_chars:
            emit(clause)
        else:
            parts = -(-n // max_chars)
            per = n / parts
            buf, cnt = [], 0
            for u in clause:
                buf.append(u)
                cnt += width(u["text"]) if u["type"] == "word" else 0
                if cnt >= per:
                    emit(buf)
                    buf, cnt = [], 0
            if buf:
                emit(buf)
        clause.clear()

    for x in words:
        if x["type"] == "audio_event":
            flush_clause()
            continue
        clause.append(x)
        if x["type"] == "word" and CLAUSE.search(x["text"]):
            flush_clause()
    flush_clause()

    ws = [x for x in words if x["type"] == "word"]
    gaps = [(a["end"], b["start"] - a["end"]) for a, b in zip(ws, ws[1:]) if b["start"] - a["end"] >= pause]
    return sentences, cues, gaps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out")
    ap.add_argument("--keyterms", default="")
    ap.add_argument("--lang", default="zh", help="Scribe 语言代码,英文参考片用 en")
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--max-chars", type=int, default=16)
    ap.add_argument("--pause", type=float, default=0.5)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    keyterms = [k.strip() for k in a.keyterms.split(",") if k.strip()]

    for src in a.inputs:
        stem = os.path.splitext(os.path.basename(src))[0]
        d = a.out or os.path.dirname(os.path.abspath(src))
        os.makedirs(d, exist_ok=True)
        base = os.path.join(d, stem)
        js = base + ".scribe.json"
        if a.force or not os.path.exists(js):
            print(f"转写 {src} …")
            transcribe(src, js, keyterms, a.lang)
        else:
            print(f"复用已有 {js}")
        j = json.load(open(js, encoding="utf-8"))
        sentences, cues, gaps = build(j["words"], a.fps, a.max_chars, a.pause)
        f = lambda s: int(round(s * a.fps))

        with open(base + ".sentences.md", "w", encoding="utf-8") as o:
            o.write(f"# {stem} · 逐句文稿\n\n> Scribe v2 转写,时长 {j.get('audio_duration_secs', 0):.1f}s,帧号按 {a.fps:g}fps。方括号行是声音事件。\n\n")
            for i, (s, e, t) in enumerate(sentences, 1):
                o.write(f"{i:03d} [{ts(s)}–{ts(e)} | {f(s)}–{f(e)}] {t}\n")
            o.write(f"\n## 停顿 ≥ {a.pause:g}s(共 {len(gaps)} 处)\n\n")
            for s, g in gaps:
                o.write(f"- {ts(s)}(帧 {f(s)})停 {g:.2f}s\n")
        with open(base + ".srt", "w", encoding="utf-8") as o:
            for i, (s, e, t) in enumerate(cues, 1):
                o.write(f"{i}\n{srt_ts(s)} --> {srt_ts(e)}\n{t}\n\n")
        low = [w for w in j["words"] if w["type"] == "word" and w.get("logprob", 0) < -1.0]
        print(f"✓ {base}.sentences.md({len(sentences)} 句)· .srt({len(cues)} 条)· 停顿 {len(gaps)} 处 · 低置信 {len(low)} 处"
              + (f":{'、'.join(w['text'] for w in low[:8])}" if low else ""))


if __name__ == "__main__":
    main()
