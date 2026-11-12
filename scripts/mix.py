#!/usr/bin/env python3
"""mix.py · 规范混音：按 src/timeline.json 的 audio 用 numpy 混出成片音轨（48 kHz 立体声 WAV）。

成片的音频以它为准：render.py 拿它限幅、标准化后封进成片；qa.py / audio_sync.py 拿它做音画对齐的基准。
不用 Remotion 带声音的直出：实测直出的 AAC 音轨比画面晚约 40 ms（两次 AAC 编码前置延迟没补偿）。

用法：
  python3 scripts/mix.py <proj> [--range a-b | a:b] [--stem all|music|sfx|voice] [--out f.wav] [--bits 24|32f]
  例：python3 scripts/mix.py <proj>                          # 整片 → <proj>/work/render/mix.wav
      python3 scripts/mix.py <proj> --range 0-299 --out /tmp/a.wav   # 成片第 0–299 帧（300 帧）
      python3 scripts/mix.py <proj> --stem voice            # 只混原声（纯人声版用）

范围写法（所有脚本通用）：`a-b` 是闭区间，和 Remotion 的 --frames 一样（0-299 = 300 帧）；`a:b` 是半开区间 [a, b)。
产物：WAV，48 kHz、立体声、默认 24 bit 整数；`--bits 32f` 写 32 位浮点（不会削波，render.py 的中间文件用它）。
      默认路径 <proj>/work/render/mix[-<stem>][-<a>-<b>].wav（b 为闭区间的末帧）。

算法（和 template/src/film/FilmAudio.tsx 一致，改一边就要改另一边）：
  - 配乐：<Sequence from=music.from 到片尾>，源从 trim_frames 帧开始；第 g 帧音量 = gain_keys 线性插值（没有 key 用 gain），
    再乘人声闪避：duck_ranges 每段 [a, b] 内压 duck_db，a 前 attack 帧、b 后 release 帧按 dB 线性过渡，多段取最深。
  - 音效：<Sequence from=round(from)>，有 dur 就在 dur 帧处截断，否则放到素材结束或片尾；音量 = gain；tone ≠ 1 时用
    和 Remotion 相同的 ffmpeg 链 asetrate → aresample → atempo 变调不变速。
  - 原声（voice）：<Sequence from 到 to>，片段从 clip_start_frame 帧开始；音量 = gain × voice_gain × 淡入淡出包络。
  - 音量是「每个视频帧一个值」：第 g 帧的音量作用于该段音频里 [g−0.5, g+0.5) / fps 的样本（Remotion 的 volume 表达式
    就是这么分段的，最后一帧的值延续到片段结束）。
  - 解码：ffmpeg 读成 32 位浮点、48 kHz、`-ac 2`（单声道按 ffmpeg 默认规则升成立体声，和 Remotion 一样），尊重文件时间戳。
  - 各轨直接相加（等于 Remotion 的 amix normalize=0），不做任何限幅。

和 Remotion 带声音直出的差别（都是 Remotion 那边的量化，不是算法差别；2026-11 用 4.0.469 实测）：
  ① 音量随帧变化时，Remotion 把每个值取整到 1/97 的格子（0.85 → 0.845，−0.05 dB）；
  ② 音量按 ffmpeg 音频块取值（WAV 源约 4096 样本 = 85 ms 一块），闪避的 6 帧下压变成两三级台阶；
  ③ 每条音频的起点延迟取整到毫秒（第 281 帧 = 9366.67 ms → 9367 ms）；④ 每轨先转成 16 bit。
  直接相减，整片残差约 −23 dB（集中在闪避边缘和非整毫秒起点）；把这四种量化在测试里模拟出来后，
  配乐 + 音效和 Remotion 的输出残差 −83 dB（最大 4 个 16 bit 量化级），原声（片段裁切 + 淡出）−58 dB。
  所以 mix.py 是 FilmAudio.tsx 算法的「不量化」版本，比 Remotion 自己混得更准。

检查边界：只负责「按 timeline 混」，不判断混得好不好听；峰值超过 0 dBFS 时提醒（24 bit 文件会削波，浮点文件不会）。
依赖：numpy；ffmpeg（PATH 里，或环境变量 FFMPEG）。兼容 Python 3.9。
也可以当库用：from mix import mix_timeline, write_wav, parse_range。
"""
import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 48000
CH = 2


# ───────────────────────── 通用小工具（其他脚本也 import） ─────────────────────────

def ffmpeg_bin():
    ff = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not ff:
        sys.exit("✗ 找不到 ffmpeg：装好后放进 PATH，或设环境变量 FFMPEG=/path/to/ffmpeg")
    return ff


def parse_range(spec, total=None):
    """'a-b' → 闭区间（Remotion --frames 语义）；'a:b' → 半开区间。返回半开区间 (a, b)。
    省略一端：'a-' / 'a:' 到片尾（需要 total），'-b' / ':b' 从 0 开始。"""
    if spec is None or spec == "":
        return None
    s = str(spec).strip()
    if ":" in s:
        x, y = s.split(":", 1)
        a = int(x) if x.strip() else 0
        b = int(y) if y.strip() else total
    elif "-" in s[1:] or s.startswith("-"):
        i = s.index("-", 1) if "-" in s[1:] else 0
        x, y = s[:i], s[i + 1:]
        a = int(x) if x.strip() else 0
        b = (int(y) + 1) if y.strip() else total
    else:
        a = int(s)
        b = a + 1
    if b is None:
        raise ValueError("范围 %r 省略了末端，需要知道总帧数" % spec)
    if a < 0 or b <= a:
        raise ValueError("范围 %r 不对：起点要 ≥ 0，终点要大于起点" % spec)
    return a, b


def range_label(a, b):
    """半开区间 [a, b) → 'a-(b-1)'（闭区间写法，和 Remotion --frames 一致）"""
    return "%d-%d" % (a, b - 1)


def jsround(x):
    """JavaScript Math.round（.5 往正无穷）。Python 的 round 是银行家舍入，不能混用。"""
    return int(math.floor(float(x) + 0.5))


def num(v, d):
    """FilmAudio.tsx 的 num()：有限数字才用，否则用默认值（布尔值不算数字）。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return d
    return float(v) if math.isfinite(v) else d


def lin(x, ab, cd):
    """util.ts 的 lin()：夹住的线性插值；a ≥ b 时按阶跃处理。"""
    a, b = ab
    c, d = cd
    if b <= a:
        return c if x < a else d
    if x <= a:
        return c
    if x >= b:
        return d
    return c + (d - c) * (x - a) / (b - a)


def load_timeline(proj, path=None):
    p = Path(path) if path else Path(proj) / "src" / "timeline.json"
    if not p.exists():
        sys.exit("✗ 找不到 %s（先跑 timeline.py 编译）" % p)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def resolve_public(proj, rel):
    """timeline 里的音频路径相对 public/；也接受绝对路径。"""
    if not rel:
        return None
    if os.path.isabs(rel):
        return Path(rel)
    r = str(rel).lstrip("/")
    if r.startswith("public/"):
        r = r[len("public/"):]
    return Path(proj) / "public" / r


# ───────────────────────── 解码 ─────────────────────────

_DECODE_CACHE = {}


def decode(path, tone=None):
    """整条音频解码成 float32 (n, 2)，48 kHz；文件开头的时间戳偏移补成静音（first_pts=0）。没有音轨返回空数组。"""
    key = (str(path), tone)
    if key in _DECODE_CACHE:
        return _DECODE_CACHE[key]
    af = "aresample=%d:first_pts=0" % SR
    if tone is not None and tone != 1:
        if not (0 < tone <= 2):
            sys.exit("✗ 音效 tone 要在 (0, 2] 之间（Remotion 的限制），现在是 %r" % tone)
        af += ",asetrate=%d*%s,aresample=%d,atempo=1/%s" % (SR, repr(float(tone)), SR, repr(float(tone)))
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-map", "0:a:0?",
           "-af", af, "-ac", str(CH), "-ar", str(SR), "-f", "f32le", "-"]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        sys.exit("✗ 解码失败：%s\n%s" % (path, r.stderr.decode("utf-8", "replace")[-600:]))
    x = np.frombuffer(r.stdout, np.float32).reshape(-1, CH)
    _DECODE_CACHE[key] = x
    return x


# ───────────────────────── 音量曲线（FilmAudio.tsx 原样） ─────────────────────────

def music_gain(mu, g):
    keys = [k for k in (mu.get("gain_keys") or []) if isinstance(k, (list, tuple)) and len(k) >= 2]
    keys = sorted(keys, key=lambda k: k[0])
    gain = num(mu.get("gain"), 1.0)
    if keys:
        if g <= keys[0][0]:
            gain = float(keys[0][1])
        elif g >= keys[-1][0]:
            gain = float(keys[-1][1])
        else:
            for i in range(len(keys) - 1):
                a, va = keys[i][0], float(keys[i][1])
                b, vb = keys[i + 1][0], float(keys[i + 1][1])
                if a <= g <= b:
                    gain = va + (vb - va) * (g - a) / (b - a) if b > a else vb
                    break
    db = num(mu.get("duck_db"), -13.0)
    at = max(0.0, num(mu.get("attack"), 6.0))
    rl = max(0.0, num(mu.get("release"), 8.0))
    d = 0.0
    for r in mu.get("duck_ranges") or []:
        if not isinstance(r, (list, tuple)) or len(r) < 2:
            continue
        a, b = r[0], r[1]
        if g < a - at or g > b + rl:
            continue
        if g < a:
            k = lin(g, (a - at, a), (0.0, 1.0))
        elif g > b:
            k = lin(g, (b, b + rl), (1.0, 0.0))
        else:
            k = 1.0
        d = max(d, k)
    return max(0.0, gain * math.pow(10.0, (db * d) / 20.0))


def voice_env(v, f, dur):
    fi, fo = num(v.get("fade_in"), 0.0), num(v.get("fade_out"), 0.0)
    a = lin(f, (0.0, fi), (0.0, 1.0)) if fi > 0 else 1.0
    b = lin(f, (dur - fo, dur), (1.0, 0.0)) if fo > 0 else 1.0
    return min(a, b)


# ───────────────────────── 混音 ─────────────────────────

def _frame_of_sample(s, fps):
    """film 样本号 → 最近的视频帧号（第 g 帧覆盖 [g−0.5, g+0.5) / fps）"""
    return np.floor(s.astype(np.float64) * fps / SR + 0.5).astype(np.int64)


def _place(out, out_s0, src, seq_f0, seq_f1, src_off, fps, gain_of_frame):
    """把一段音频按 <Sequence from=seq_f0 durationInFrames=seq_f1−seq_f0> 放进 out。
    out 覆盖成片样本 [out_s0, out_s0 + len(out))；成片样本 s 取 src[s − seq_s0 + src_off]；
    音量取 gain_of_frame(g)，g 是成片帧号，夹在 [seq_f0, seq_f1 − 1]（Remotion 把最后一帧的音量延续到结尾）。"""
    if seq_f1 <= seq_f0 or len(src) == 0:
        return
    seq_s0 = int(round(seq_f0 * SR / fps))
    seq_s1 = int(round(seq_f1 * SR / fps))
    lo = max(out_s0, seq_s0, seq_s0 - src_off)
    hi = min(out_s0 + len(out), seq_s1, seq_s0 - src_off + len(src))
    if hi <= lo:
        return
    s = np.arange(lo, hi, dtype=np.int64)
    g = np.clip(_frame_of_sample(s, fps), seq_f0, seq_f1 - 1)
    g0, g1 = int(g.min()), int(g.max())
    table = np.array([gain_of_frame(x) for x in range(g0, g1 + 1)], dtype=np.float64)
    gains = table[g - g0].astype(np.float32)
    seg = src[lo - seq_s0 + src_off: hi - seq_s0 + src_off]
    out[lo - out_s0: hi - out_s0] += seg * gains[:, None]


def mix_timeline(proj, tl, a=None, b=None, stem="all", log=None):
    """混成片帧 [a, b) 的音频，返回 (float32 数组 (n, 2), 信息 dict)。"""
    fps = float(tl.get("fps") or 30)
    total = int(round(num(tl.get("duration"), 1)))
    a = 0 if a is None else int(a)
    b = total if b is None else int(b)
    if not (0 <= a < b):
        raise ValueError("范围不对：[%d, %d)" % (a, b))
    if b > total:
        raise ValueError("范围 [%d, %d) 超出成片（%d 帧）" % (a, b, total))
    out_s0 = int(round(a * SR / fps))
    out_s1 = int(round(b * SR / fps))
    out = np.zeros((out_s1 - out_s0, CH), np.float32)
    au = tl.get("audio") or {}
    info = {"fps": fps, "range": [a, b], "samples": out_s1 - out_s0, "sr": SR, "stem": stem, "tracks": []}
    say = log or (lambda *_: None)

    mu = au.get("music")
    if stem in ("all", "music") and mu and mu.get("src"):
        p = resolve_public(proj, mu["src"])
        if not p.exists():
            raise FileNotFoundError("配乐不存在：%s" % p)
        src = decode(p)
        f0 = max(0, jsround(num(mu.get("from"), 0)))
        trim = max(0, jsround(num(mu.get("trim_frames"), 0)))
        _place(out, out_s0, src, f0, max(f0 + 1, total), int(round(trim * SR / fps)), fps, lambda g: music_gain(mu, g))
        info["tracks"].append({"kind": "music", "src": mu["src"], "from": f0, "trim_frames": trim, "src_s": len(src) / SR})
        say("配乐 %s：从第 %d 帧，源跳过 %d 帧" % (mu["src"], f0, trim))

    if stem in ("all", "sfx"):
        for i, s in enumerate(au.get("sfx") or []):
            if not s or not s.get("src"):
                continue
            p = resolve_public(proj, s["src"])
            if not p.exists():
                raise FileNotFoundError("音效不存在：%s" % p)
            tone = s.get("tone") if isinstance(s.get("tone"), (int, float)) and not isinstance(s.get("tone"), bool) else None
            src = decode(p, tone)
            f0 = jsround(num(s.get("from"), 0))
            dur = s.get("dur")
            f1 = f0 + jsround(dur) if isinstance(dur, (int, float)) and not isinstance(dur, bool) and dur > 0 else max(f0 + 1, total)
            g = num(s.get("gain"), 1.0)
            if f0 < 0:  # Sequence 从负帧开始：前面那段没放出来
                off = int(round(-f0 * SR / fps))
                _place(out, out_s0, src[off:], 0, f1, 0, fps, lambda _x, g=g: g)
            else:
                _place(out, out_s0, src, f0, f1, 0, fps, lambda _x, g=g: g)
            info["tracks"].append({"kind": "sfx", "id": s.get("id", i), "src": s["src"], "from": f0, "to": f1, "gain": g, "tone": tone})

    if stem in ("all", "voice"):
        vg = num(au.get("voice_gain"), 1.0)
        for i, v in enumerate(au.get("voice") or []):
            dur = jsround(num(v.get("to"), 0) - num(v.get("from"), 0))
            if not v.get("clip") or dur <= 0:
                continue
            p = resolve_public(proj, v["clip"])
            if not p.exists():
                raise FileNotFoundError("原声片段不存在：%s" % p)
            src = decode(p)
            f0 = jsround(num(v.get("from"), 0))
            cs = max(0, jsround(num(v.get("clip_start_frame"), 0)))
            gv = num(v.get("gain"), 1.0)
            _place(out, out_s0, src, f0, f0 + dur, int(round(cs * SR / fps)), fps,
                   lambda g, v=v, f0=f0, dur=dur, gv=gv: gv * vg * voice_env(v, g - f0, dur))
            info["tracks"].append({"kind": "voice", "shot": v.get("shot", i), "clip": v["clip"], "from": f0, "to": f0 + dur,
                                   "clip_start_frame": cs})

    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    info["peak"] = peak
    info["peak_dbfs"] = round(20 * math.log10(peak), 2) if peak > 0 else None
    info["silent"] = peak == 0.0
    return out, info


# ───────────────────────── 写 WAV ─────────────────────────

def write_wav(path, x, sr=SR, bits="24"):
    """写 WAV：bits = '24'（PCM 24 bit，超过 ±1 的样本会被夹住）/ '32f'（IEEE 浮点）/ '16'。返回被夹住的样本数。"""
    x = np.asarray(x, np.float32)
    if x.ndim == 1:
        x = x[:, None]
    n, ch = x.shape
    clipped = 0
    if bits == "32f":
        data = x.astype("<f4").tobytes()
        fmt_tag, width = 3, 4
    else:
        clipped = int(np.count_nonzero(np.abs(x) > 1.0))
        y = np.clip(x.astype(np.float64), -1.0, 1.0)
        if bits == "16":
            data = np.round(y * 32767).astype("<i2").tobytes()
            width = 2
        else:
            q = np.round(y * 8388607).astype("<i4")
            data = q.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
            width = 3
        fmt_tag = 1
    block = ch * width
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        fmt = struct.pack("<HHIIHH", fmt_tag, ch, sr, sr * block, block, width * 8)
        extra = b""
        if fmt_tag == 3:
            fmt += struct.pack("<H", 0)
            extra = b"fact" + struct.pack("<II", 4, n)
        f.write(b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + len(extra) + 8 + len(data)) + b"WAVE")
        f.write(b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra)
        f.write(b"data" + struct.pack("<I", len(data)) + data)
    return clipped


def default_out(proj, stem, a, b, total):
    name = "mix"
    if stem != "all":
        name += "-" + stem
    if (a, b) != (0, total):
        name += "-" + range_label(a, b)
    return Path(proj) / "work" / "render" / (name + ".wav")


def main():
    ap = argparse.ArgumentParser(description="规范混音：按 timeline.json 的 audio 混出成片音轨")
    ap.add_argument("proj")
    ap.add_argument("--range", help="成片帧范围：a-b 闭区间（同 Remotion --frames），a:b 半开区间；默认整片")
    ap.add_argument("--stem", default="all", choices=["all", "music", "sfx", "voice"])
    ap.add_argument("--out", help="输出 WAV 路径（默认 <proj>/work/render/mix….wav）")
    ap.add_argument("--bits", default="24", choices=["16", "24", "32f"], help="默认 24 bit 整数；32f = 浮点，不削波")
    ap.add_argument("--timeline", help="另一份 timeline.json（默认 <proj>/src/timeline.json）")
    a = ap.parse_args()
    tl = load_timeline(a.proj, a.timeline)
    total = int(round(num(tl.get("duration"), 1)))
    try:
        r = parse_range(a.range, total) or (0, total)
        x, info = mix_timeline(a.proj, tl, r[0], r[1], a.stem, log=print)
    except (ValueError, FileNotFoundError) as e:
        sys.exit("✗ " + str(e))
    out = Path(a.out) if a.out else default_out(a.proj, a.stem, r[0], r[1], total)
    clipped = write_wav(out, x, SR, a.bits)
    print("✓ %s：帧 %s（%d 帧，%.3f 秒，%d 样本），stem=%s，%d 条音轨，峰值 %s dBFS" % (
        out, range_label(*r), r[1] - r[0], len(x) / SR, len(x), a.stem, len(info["tracks"]),
        info["peak_dbfs"] if info["peak_dbfs"] is not None else "−∞（全静音）"))
    if clipped:
        print("⚠️ %d 个样本超过 0 dBFS，24 bit 文件里被夹住了；要原样保留用 --bits 32f（render.py 自动这样做）" % clipped)


if __name__ == "__main__":
    main()
