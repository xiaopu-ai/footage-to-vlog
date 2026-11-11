#!/usr/bin/env python3
"""编译剪辑决定:<proj>/edit.json → <proj>/src/timeline.json(全部换算成 30 fps 的整数帧)+ 校验(lint)+ 简表。

用法:
  python3 timeline.py <proj> [--out <proj>/src/timeline.json] [--quiet]

读:
  <proj>/edit.json          剪辑决定(唯一手写的剪辑文件;格式见 references/edit-schema.md)
  <proj>/project.json       fps / 尺寸 / footage_root / handles_s / profiles / look / fonts / brand
  <proj>/work/footage.json  素材清单(inventory.py 生成);缺了或查不到的素材临时用 ffmpeg -i 探测
  <proj>/beats.json         拍网格(tempo.py 生成;edit.json 里用了 beat / bar / eighth 就必需)
  <proj>/words.json         采访逐字时间(paper_edit.py 生成;用了 say 或开了字幕就必需)
  <proj>/work/dict.txt、work/fix.txt   字幕专有名词 / 错字表(可选)
写:
  <proj>/src/timeline.json  Remotion 模板、make_clips.py、审核台、synccheck.py、cuesheet.py 都只读它
退出码:lint 有错误 → 1(timeline.json 照样写出来,方便排查,但不能拿去渲染);只有警告 → 0。

要点(细节见 references/edit-schema.md;本文件补充的约定写在各函数说明里):
  - 时间点 T:帧 / {"s"} / {"beat"} / {"bar","beat"} / {"eighth"} / {"say","nth","edge"} / {"shot","edge"},都可加 "offset"。
    拍网格换算:帧 = round(拍点秒 × fps) − lead_frames(只对画面:镜头、文字;音效和音量关键点不减 lead)。
  - 时长 D:帧 / {"s"} / {"beats"} / {"bars"} / {"eighths"} / {"until": T}。按拍写的时长,结尾落在拍网格上
    (起点不在网格上时,离最近的 8 分音符 ≤ 2 帧就吸附过去),连续几镜按拍顺接不会累积取整误差。
  - 镜头 at 缺省 = 同层上一镜结尾;dur 缺省 = 同层下一镜写了 at 时取到它为止,主轨最后一镜取到全片结尾。
  - 片段 conform:保留全部原生帧、按 30 fps 播放;片段第 i 帧 = clip_in_s + i / 原生帧率;clip_in_s 对齐到原生帧
    (写出时向下取到微秒,clips[].from_frame 是同一帧的原生帧号,切片段时按帧号最稳)。
  - 转场以切点为中心:前半 pre = len // 2 帧在切点前,后半 post = len − pre 在切点后(punch:pre = round(len × 5/8),post = len;len 8 → 5 / 8,照发布片原版)。
    fade / wipe / sweep 两镜重叠:本镜 from = cut − pre,上一镜 to = cut + post;whip / punch / dip / cut 不重叠。
"""
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import captions as CAP  # noqa: E402

TR_LEN = {"cut": 0, "dip": 8, "fade": 12, "whip": 8, "punch": 8, "sweep": 10, "wipe": 12}
TR_OVERLAP = {"fade", "wipe", "sweep"}
BEAT_STYLES = {"hype", "launch", "manifesto"}
STYLES = {"testimonial", "launch", "hype", "manifesto", "custom"}
STYLE_VIGNETTE = {"testimonial": 0.15, "launch": 0.2, "hype": 0.25, "manifesto": 0.3, "custom": 0.2}
MOVES = {"none", "push", "pull", "drift-left", "drift-right", "rise", "interview"}
TEXT_DEFAULTS = {
    "slam": {"size": 420, "font": "brush", "shake": 14, "push": 0.06, "nudge": [0, 0]},
    "brushword": {"size": 200, "vertical": False, "align": "center"},
    "chapter": {"side": "left", "size": 150},
    "qcard": {"lines": [], "hi": None, "tag": None},
    "lower": {},
    "spec": {},
    "reveal": {},
    "lockup": {"lines": []},
    "endcard": {"logos": []},
    "image": {},
}
TEXT_DUR = {"slam": 15, "qcard": 115}  # 没写 dur 时的默认;lockup / endcard 默认到片尾
QCARD_MIN = 110
STD_FPS = (24000 / 1001, 30000 / 1001, 60000 / 1001, 120000 / 1001, 240000 / 1001)
EPS = 1e-6


def norm_fps(f):
    for s in STD_FPS:
        if abs(f - s) < 0.01:
            return s
    return float(f)


def jload(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class Lint:
    def __init__(self):
        self.errors, self.warnings, self.info = [], [], []

    def err(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)


# ───────────────────────── 拍网格 ─────────────────────────

class Grid:
    """beats.json → 成片帧。offset_s 是音乐从成片第 0 帧、原曲 trim_s 秒开始放时第 0 小节头的成片秒数;
    edit.json 的 audio.music.at / trim_s 和测速时不同,会自动平移。"""

    def __init__(self, beats, fps, music_at_frames=0, music_trim_s=None):
        self.beat_s = float(beats["beat_s"])
        self.bpb = int(beats.get("beats_per_bar", 4))
        self.lead = int(beats.get("lead_frames", 1))
        self.fps = fps
        bt = float(beats.get("trim_s", 0) or 0)
        mt = bt if music_trim_s is None else float(music_trim_s)
        self.offset = float(beats["offset_s"]) + music_at_frames / fps - (mt - bt)
        self.bpm = 60.0 / self.beat_s
        self.beat_f = self.beat_s * fps

    def t(self, b):
        return self.offset + b * self.beat_s

    def frame(self, b, lead=True):
        return int(round(self.t(b) * self.fps)) - (self.lead if lead else 0)

    def bp_of_frame(self, f, lead=True):
        return ((f + (self.lead if lead else 0)) / self.fps - self.offset) / self.beat_s

    def snap(self, bp, tol_frames=2.0):
        r = round(bp * 2) / 2
        return r if abs(bp - r) * self.beat_f <= tol_frames else bp

    def label(self, f, lead=True):
        """帧 → 「小节.拍」(从 1.1 起数,「+」= 后半拍)"""
        bp = self.bp_of_frame(f + 0.5, lead)
        bar = math.floor(bp / self.bpb)
        inbar = bp - bar * self.bpb
        return f"{bar + 1}.{int(inbar) + 1}" + ("+" if inbar % 1 >= 0.45 else "")


# ───────────────────────── 时间解析 ─────────────────────────

PUNCT_SP = re.compile(r"[\s，。、？！；：,.?!;:…「」『』“”\"'()（）]")


def _norm_say(s):
    return PUNCT_SP.sub("", s).lower()


class Ctx:
    def __init__(self, fps, lint):
        self.fps = fps
        self.lint = lint
        self.grid = None
        self.words = []
        self.shots = {}  # 已编译的镜头 id → dict

    def say(self, spec, where):
        phrase = str(spec.get("say", ""))
        nth = int(spec.get("nth", 0))
        edge = spec.get("edge", "start")
        if not self.words:
            self.lint.err(f"[短语] {where}:用了 say「{phrase}」,但没有 words.json(先跑 paper_edit.py)")
            return None
        target = _norm_say(phrase)
        chars, owner = [], []
        for i, w in enumerate(self.words):
            c = _norm_say(w["t"])
            chars.append(c)
            owner += [i] * len(c)
        full = "".join(chars)
        pos, k = -1, -1
        while k < nth:
            pos = full.find(target, pos + 1)
            if pos < 0:
                break
            k += 1
        if not target or pos < 0:
            cnt = full.count(target) if target else 0
            self.lint.err(f"[短语] {where}:口播里找不到「{phrase}」第 {nth} 次出现(共出现 {cnt} 次)")
            return None
        w0, w1 = self.words[owner[pos]], self.words[owner[pos + len(target) - 1]]
        return int(round(w0["f"] if edge != "end" else w1["e"]))

    def T(self, spec, where, lead=True):
        """时间点 → (帧, 拍位或 None)。解析失败返回 (None, None) 并记错误。"""
        if spec is None:
            return None, None
        if isinstance(spec, bool):
            self.lint.err(f"[时间] {where}:看不懂的时间点 {spec!r}")
            return None, None
        if isinstance(spec, (int, float)):
            return int(round(spec)), None
        if not isinstance(spec, dict):
            self.lint.err(f"[时间] {where}:看不懂的时间点 {spec!r}")
            return None, None
        off = spec.get("offset", 0) or 0
        f, bp = None, None
        if "s" in spec:
            f = int(round(float(spec["s"]) * self.fps))
        elif "say" in spec:
            f = self.say(spec, where)
        elif "shot" in spec:
            sh = self.shots.get(spec["shot"])
            if not sh:
                self.lint.err(f"[时间] {where}:引用的镜头 {spec['shot']} 不存在或还没排(只能引用主轨、或排在前面的镜头)")
            else:
                f = sh["end"] if spec.get("edge") == "end" else sh["cut"]
        elif "bar" in spec or "beat" in spec or "eighth" in spec:
            if not self.grid:
                self.lint.err(f"[时间] {where}:按拍写时间需要 beats.json(先跑 tempo.py)")
                return None, None
            if "bar" in spec:
                bp = float(spec["bar"]) * self.grid.bpb + float(spec.get("beat", 0))
            elif "beat" in spec:
                bp = float(spec["beat"])
            else:
                bp = float(spec["eighth"]) / 2
            f = self.grid.frame(bp, lead)
            if f < 0 <= self.grid.frame(bp, False):
                f = 0  # 第 0 拍减 lead 会变成 −1,夹到 0
        else:
            self.lint.err(f"[时间] {where}:看不懂的时间点 {json.dumps(spec, ensure_ascii=False)}")
        if f is None:
            return None, None
        return int(round(f + off)), bp

    def D(self, spec, start, start_bp, where, lead=True):
        """时长 → (结尾帧, 结尾拍位或 None)。"""
        if spec is None:
            return None, None
        if isinstance(spec, (int, float)) and not isinstance(spec, bool):
            return start + int(round(spec)), None
        if not isinstance(spec, dict):
            self.lint.err(f"[时长] {where}:看不懂的时长 {spec!r}")
            return None, None
        if "until" in spec:
            return self.T(spec["until"], where, lead)
        if "s" in spec:
            return start + int(round(float(spec["s"]) * self.fps)), None
        nb = None
        if "beats" in spec:
            nb = float(spec["beats"])
        elif "bars" in spec:
            nb = float(spec["bars"]) * (self.grid.bpb if self.grid else 4)
        elif "eighths" in spec:
            nb = float(spec["eighths"]) / 2
        if nb is None:
            self.lint.err(f"[时长] {where}:看不懂的时长 {json.dumps(spec, ensure_ascii=False)}")
            return None, None
        if not self.grid:
            self.lint.err(f"[时长] {where}:按拍写时长需要 beats.json(先跑 tempo.py)")
            return None, None
        bp = start_bp if start_bp is not None else self.grid.snap(self.grid.bp_of_frame(start, lead))
        return max(0, self.grid.frame(bp + nb, lead)), bp + nb


# ───────────────────────── 素材 ─────────────────────────

def ffmpeg_bin():
    return os.environ.get("FFMPEG") or shutil.which("ffmpeg")


def probe(path):
    """没有 footage.json 时的兜底:ffmpeg -i 读文件头(不解码)。"""
    ff = ffmpeg_bin()
    if not ff or not os.path.exists(path):
        return None
    r = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True)
    s = r.stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", s)
    v = re.search(r"Stream #[^\n]*Video:[^\n]*?([\d.]+) fps", s)
    if not m or not v:
        return None
    return {"duration": int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)),
            "fps": float(v.group(1)), "has_audio": "Audio:" in s, "profile": None, "probed": True}


class Footage:
    def __init__(self, proj, root, lint):
        data = jload(os.path.join(proj, "work", "footage.json"), [])
        if isinstance(data, dict):
            data = data.get("footage") or data.get("items") or data.get("files") or []
        self.items = {d["path"]: d for d in data if isinstance(d, dict) and "path" in d}
        self.lower = {k.lower(): v for k, v in self.items.items()}
        base = {}
        for k, v in self.items.items():
            base.setdefault(os.path.basename(k).lower(), []).append(v)
        self.base = base
        self.root = root
        self.lint = lint
        self.cache = {}
        self.has_file = bool(data)

    def get(self, src, where):
        if src in self.cache:
            return self.cache[src]
        it = self.items.get(src) or self.lower.get(src.lower())
        if it is None:
            b = self.base.get(os.path.basename(src).lower(), [])
            if len(b) == 1:
                it = b[0]
                self.lint.warn(f"[素材] {where}:{src} 在 footage.json 里按文件名匹配到 {it['path']},建议改成完整相对路径")
        if it is None:
            path = os.path.join(self.root or "", src)
            it = probe(path)
            if it:
                self.lint.warn(f"[素材] {where}:{src} 不在 footage.json 里,临时用 ffmpeg 探测(时长 {it['duration']:.2f}s,{it['fps']:g} fps)")
            else:
                self.lint.err(f"[素材] {where}:找不到素材 {src}(footage.json 里没有,{path} 也打不开)")
        self.cache[src] = it
        return it


# ───────────────────────── 变速积分 ─────────────────────────

def speed_fn(speed, dur):
    """返回 (S, vmin, vmax, varies):S(x) = ∫0^x v(u) du(u 是镜头内帧,可以是负数或超过 dur,两头按端点速度外推)。"""
    if isinstance(speed, (int, float)) and not isinstance(speed, bool):
        v = float(speed)
        return (lambda x: v * x), v, v, False
    pts = sorted((float(p) * dur, float(v)) for p, v in speed)
    if pts[0][0] > 0:
        pts.insert(0, (0.0, pts[0][1]))
    if pts[-1][0] < dur:
        pts.append((float(dur), pts[-1][1]))
    cum = [0.0]
    for (u0, v0), (u1, v1) in zip(pts, pts[1:]):
        cum.append(cum[-1] + (v0 + v1) / 2 * (u1 - u0))

    def S(x):
        if x <= pts[0][0]:
            return pts[0][1] * (x - pts[0][0])
        for i in range(len(pts) - 1):
            (u0, v0), (u1, v1) = pts[i], pts[i + 1]
            if x <= u1:
                if u1 == u0:
                    return cum[i]
                vx = v0 + (v1 - v0) * (x - u0) / (u1 - u0)
                return cum[i] + (v0 + vx) / 2 * (x - u0)
        return cum[-1] + pts[-1][1] * (x - pts[-1][0])

    vs = [v for _, v in pts]
    return S, min(vs), max(vs), len(set(vs)) > 1


def norm_tr(tr, where, lint):
    if tr is None:
        tr = {"type": "cut"}
    if isinstance(tr, str):
        tr = {"type": tr}
    t = tr.get("type", "cut")
    if t not in TR_LEN:
        lint.err(f"[转场] {where}:不认识的转场 {t}(可用 {' / '.join(TR_LEN)})")
        t = "cut"
    ln = int(tr.get("len", TR_LEN[t]))
    if t == "cut":
        ln = 0
    pre = ln // 2
    post = ln - pre
    if t == "punch":  # 发布片原版:上一镜最后 5 帧加速推进,本镜 8 帧回落(len 8 → pre 5 / post 8)
        pre, post = (ln * 5 + 4) // 8, ln
    out = dict(tr)
    out.update({"type": t, "len": ln, "pre": pre, "post": post})
    return out


# ───────────────────────── 编译 ─────────────────────────

def compile_project(proj, quiet=False):
    lint = Lint()
    edit = jload(os.path.join(proj, "edit.json"))
    if edit is None:
        sys.exit(f"找不到 {os.path.join(proj, 'edit.json')}")
    pj = jload(os.path.join(proj, "project.json"), None)
    if pj is None:
        lint.warn("[项目] 没有 project.json,按 30 fps 1920×1080、handles 0.5 s 处理")
        pj = {}
    fps = int(pj.get("fps", 30))
    handles = float(pj.get("handles_s", 0.5))
    style = edit.get("style", "custom")
    if style not in STYLES:
        lint.warn(f"[风格] style「{style}」不认识,按 custom 处理")
    ctx = Ctx(fps, lint)

    # 音乐的 at / trim(拍网格要用)
    audio = edit.get("audio", {}) or {}
    music = audio.get("music")
    m_at = 0
    if music:
        at = music.get("at", 0)
        if isinstance(at, dict) and "s" in at:
            m_at = int(round(at["s"] * fps)) + int(at.get("offset", 0))
        elif isinstance(at, (int, float)):
            m_at = int(round(at))
        else:
            lint.err("[音乐] audio.music.at 只能写帧或 {\"s\": 秒}(拍网格本身由音乐决定)")
    beats = jload(os.path.join(proj, "beats.json"))
    if beats:
        ctx.grid = Grid(beats, fps, m_at, music.get("trim_s", 0) if music else None)
    elif style in BEAT_STYLES:
        lint.warn(f"[拍] 卡点类风格({style})没有 beats.json,切点不做卡拍检查")

    words_raw = jload(os.path.join(proj, "words.json"), []) or []
    words_raw = sorted(words_raw, key=lambda w: w["f"])
    ctx.words = words_raw

    footage = Footage(proj, pj.get("footage_root"), lint)
    if not footage.has_file:
        lint.warn("[素材] 没有 work/footage.json(先跑 inventory.py),素材信息临时用 ffmpeg 探测")

    # 全片时长(auto 要等主轨排完)
    dur_spec = edit.get("duration", "auto")
    duration = None
    if dur_spec != "auto":
        if isinstance(dur_spec, dict) and any(k in dur_spec for k in ("bars", "beats", "eighths")):
            if ctx.grid:
                nb = float(dur_spec.get("bars", 0)) * ctx.grid.bpb + float(dur_spec.get("beats", 0)) + float(dur_spec.get("eighths", 0)) / 2
                duration = int(round(ctx.grid.t(nb) * fps))
            else:
                lint.err("[时长] duration 按拍写需要 beats.json")
        else:
            duration, _ = ctx.D(dur_spec, 0, None, "duration")

    # ── 镜头:先主轨,再按层号往上 ──
    raw = edit.get("shots", []) or []
    seen = set()
    for i, s in enumerate(raw):
        if "id" not in s:
            s["id"] = f"S{i + 1:02d}"
            lint.warn(f"[镜头] 第 {i + 1} 镜没写 id,自动起名 {s['id']}")
        if s["id"] in seen:
            lint.err(f"[镜头] id 重复:{s['id']}")
        seen.add(s["id"])
    layers = sorted({int(s.get("layer", 0)) for s in raw})
    placed = []  # 每项:dict(原始 s, cut, end, cut_bp, end_bp)
    for L in layers:
        seq = [s for s in raw if int(s.get("layer", 0)) == L]
        prev_end, prev_bp = (0, None)
        for k, s in enumerate(seq):
            where = f"镜头 {s['id']}"
            if "at" in s:
                cut, cut_bp = ctx.T(s["at"], where)
            elif L == 0 or k > 0:
                cut, cut_bp = prev_end, prev_bp
            else:
                lint.err(f"[镜头] {where}:layer {L} 的第一镜要写 at")
                cut, cut_bp = None, None
            if cut is None:
                cut, cut_bp = prev_end, prev_bp
            end, end_bp = None, None
            if "dur" in s:
                end, end_bp = ctx.D(s["dur"], cut, cut_bp, where)
            else:
                nxt = next((x for x in seq[k + 1:]), None)
                if nxt is not None and "at" in nxt:
                    end, end_bp = ctx.T(nxt["at"], f"镜头 {nxt['id']}")
                elif nxt is None and L == 0 and duration is not None:
                    end, end_bp = duration, None
                else:
                    lint.err(f"[镜头] {where}:没写 dur,下一镜也没写 at,算不出结尾")
            if end is None:
                end = cut + 30
            if end <= cut:
                lint.err(f"[镜头] {where}:结尾 {end} 不晚于切点 {cut}")
                end = cut + 1
            rec = {"s": s, "layer": L, "cut": cut, "end": end, "cut_bp": cut_bp, "end_bp": end_bp}
            placed.append(rec)
            ctx.shots[s["id"]] = {"cut": cut, "end": end}
            prev_end, prev_bp = end, end_bp
        if L == 0:
            if duration is None:
                duration = max((r["end"] for r in placed if r["layer"] == 0), default=0)
            else:
                # 全片按小节算时不减 lead,主轨最后一镜按拍算会早 lead 帧结束:补齐,不算空隙
                last = max((r for r in placed if r["layer"] == 0), key=lambda r: r["end"], default=None)
                lead = ctx.grid.lead if ctx.grid else 0
                if last and 0 < duration - last["end"] <= lead:
                    last["end"] = duration
                    ctx.shots[last["s"]["id"]]["end"] = duration
            # 主轨排完,按镜头重算逐字时间(纸面剪辑后插了 B-roll / 问题卡,后面的采访整体后移)
            ctx.words = remap_words(words_raw, [r for r in placed if r["layer"] == 0], fps, lint)

    if duration is None or duration <= 0:
        lint.err("[时长] 算不出全片时长")
        duration = 1

    # ── 转场、可见区间 ──
    by_layer = {}
    for r in placed:
        by_layer.setdefault(r["layer"], []).append(r)
    for L, seq in by_layer.items():
        seq.sort(key=lambda r: (r["cut"], r["end"]))
        for i, r in enumerate(seq):
            s = r["s"]
            r["in_tr"] = norm_tr(s.get("in_tr"), f"镜头 {s['id']}", lint)
            r["prev"] = seq[i - 1] if i > 0 and seq[i - 1]["end"] == r["cut"] else None
            r["next"] = seq[i + 1] if i + 1 < len(seq) and seq[i + 1]["cut"] == r["end"] else None
            if i > 0:
                p = seq[i - 1]
                if r["cut"] < p["end"]:
                    lint.err(f"[重叠] layer {L}:{p['s']['id']}(到 {p['end']})和 {s['id']}(从 {r['cut']})重叠 {p['end'] - r['cut']} 帧")
                elif r["cut"] > p["end"] and L == 0:
                    lint.err(f"[空隙] 主轨 {p['s']['id']} 结尾 {p['end']} 到 {s['id']} 切点 {r['cut']} 之间空了 {r['cut'] - p['end']} 帧")
        if L == 0 and seq:
            if seq[0]["cut"] > 0:
                lint.err(f"[空隙] 主轨开头空了 {seq[0]['cut']} 帧(第一镜 {seq[0]['s']['id']} 从 {seq[0]['cut']} 开始;要黑场就放一个 src 为 null 的镜头)")
            if seq[-1]["end"] < duration:
                lint.err(f"[空隙] 主轨最后一镜 {seq[-1]['s']['id']} 在 {seq[-1]['end']} 结束,全片到 {duration},片尾空了 {duration - seq[-1]['end']} 帧")
            elif seq[-1]["end"] > duration:
                lint.warn(f"[时长] 主轨最后一镜 {seq[-1]['s']['id']} 到 {seq[-1]['end']},超出全片 {duration},会被截掉")
    for L, seq in by_layer.items():
        for r in seq:
            s = r["s"]
            if r["next"] is not None:
                r["out_tr"] = dict(r["next"]["in_tr"])
                if s.get("out_tr") is not None:
                    lint.warn(f"[转场] 镜头 {s['id']}:后面紧接 {r['next']['s']['id']},out_tr 以下一镜的 in_tr 为准,忽略自己写的 out_tr")
            else:
                r["out_tr"] = norm_tr(s.get("out_tr"), f"镜头 {s['id']}", lint)
            r["from"] = r["cut"] - (r["in_tr"]["pre"] if r["in_tr"]["type"] in TR_OVERLAP else 0)
            r["to"] = r["end"] + (r["out_tr"]["post"] if r["out_tr"]["type"] in TR_OVERLAP else 0)
            r["from"] = max(0, r["from"])
            if L > 0 and r["to"] > duration:
                lint.warn(f"[时长] 插入镜头 {s['id']} 到 {r['to']},超出全片 {duration},会被截掉")
            r["to"] = min(duration, r["to"])
            if r["to"] <= r["from"]:
                r["to"] = r["from"] + 1

    # ── 每镜:变速、定格、片段帧 ──
    shots_out, clips_out = [], []
    vign_default = STYLE_VIGNETTE.get(style, 0.2)
    look = pj.get("look")
    profiles = pj.get("profiles", {}) or {}
    for r in sorted(placed, key=lambda r: (r["layer"], r["cut"])):
        s = r["s"]
        sid = s["id"]
        where = f"镜头 {sid}"
        dur = r["end"] - r["cut"]
        speed = s.get("speed", 1)
        if isinstance(speed, list):
            ok = all(isinstance(p, (list, tuple)) and len(p) == 2 for p in speed) and speed
            if not ok:
                lint.err(f"[速度] {where}:速度关键点要写成 [[进度0–1, 速度], …]")
                speed = 1
        S, vmin, vmax, varies = speed_fn(speed, dur)
        if vmin <= 0:
            lint.err(f"[速度] {where}:速度必须大于 0(不支持倒放 / 静止,定格用 hold)")
        hold_local = None
        if s.get("hold"):
            hf = s["hold"].get("from") if isinstance(s["hold"], dict) else s["hold"]
            he, _ = ctx.D(hf, r["cut"], r["cut_bp"], where + " 的 hold")
            if he is not None:
                hold_local = he - r["cut"]
                if not 0 <= hold_local <= dur:
                    lint.err(f"[定格] {where}:hold.from = 镜内第 {hold_local} 帧,不在 0–{dur} 之内")
        audio_cfg = s.get("audio", False)
        if audio_cfg is True:
            audio_cfg = {"gain": 1.0, "fade_in": 2, "fade_out": 2}
        elif isinstance(audio_cfg, dict):
            a2 = {"gain": 1.0, "fade_in": 2, "fade_out": 2}
            a2.update(audio_cfg)
            audio_cfg = a2
        else:
            audio_cfg = None
        if audio_cfg and (varies or abs(vmin - 1) > EPS):
            lint.err(f"[速度] {where}:播放原声的镜头(audio)只能原速,现在速度是 {speed}")

        move_t = s.get("move", "none") or "none"
        if move_t not in MOVES:
            lint.warn(f"[运镜] {where}:不认识的运镜 {move_t},按 none 处理")
            move_t = "none"
        amt = float(s.get("move_amt", 0.04 if move_t == "interview" else (0 if move_t == "none" else 0.06)))
        move = {"type": move_t, "amt": amt, "run_from": r["from"], "run_to": r["to"], "tight": False}
        if s.get("make_room"):
            move["make_room"] = s["make_room"]
        frame = {"scale": 1.0, "x": 0, "y": 0}
        frame.update(s.get("frame", {}) or {})

        out = {
            "id": sid, "layer": r["layer"], "cut": r["cut"], "end": r["end"], "from": r["from"], "to": r["to"],
            "clip": None, "clip_frames": [], "src": s.get("src"), "native_fps": None,
            "src_in_s": None, "src_out_s": None, "clip_in_s": None, "clip_out_s": None,
            "speed": speed, "frame": frame, "move": move, "in_tr": r["in_tr"], "out_tr": r["out_tr"],
            "sky": float(s.get("sky", 0.0)), "vignette": float(s.get("vignette", vign_default)),
            "hold_from": (r["cut"] + hold_local) if hold_local is not None else None,
            "audio": audio_cfg, "fix": s.get("fix", {}) or {}, "note": s.get("note", ""),
        }
        for k in ("color", "make_room", "light"):  # light:镜内光扫(模板扩展字段,帧号相对本镜切点)
            if k in s:
                out[k] = s[k]
        r["out"] = out
        shots_out.append(out)
        if s.get("src") is None:
            if audio_cfg:
                lint.err(f"[原声] {where}:没有素材(src 为 null)却开了 audio")
            continue

        info = footage.get(s["src"], where)
        if "in" not in s:
            lint.err(f"[镜头] {where}:缺 in(源素材秒数)")
        if not info:
            continue
        nfps = norm_fps(float(info.get("fps") or 30))
        sdur = float(info.get("duration") or 0)
        min_speed = min(1.0, fps / nfps)
        if vmin < min_speed * 0.995:
            lint.err(f"[速度] {where}:最慢 {vmin:g} 倍,{nfps:.2f} fps 素材最慢只能 {min_speed:.3g} 倍(再慢会重复帧发顿)")
        if audio_cfg and info.get("has_audio") is False:
            lint.err(f"[原声] {where}:开了 audio,但素材 {s['src']} 没有音轨")
        t_in = float(s.get("in", 0))

        def src_t(local):
            x = local if hold_local is None else min(local, hold_local)
            return t_in + S(x) / fps

        locs = range(r["from"] - r["cut"], r["to"] - r["cut"])
        ts = [src_t(x) for x in locs]
        need_lo, need_hi = min(ts), max(ts) + 1.0 / nfps
        if need_lo < -EPS or need_hi > sdur + EPS:
            lint.err(f"[越界] {where}:要用源素材 {need_lo:.3f}–{need_hi:.3f}s(含转场重叠),素材只有 0–{sdur:.3f}s")
        c_in = max(0.0, need_lo - handles)
        c_in = math.floor(c_in * nfps + 1e-6) / nfps
        c_out = min(sdur, need_hi + handles)
        c_out = max(c_out, c_in + 1.0 / nfps)
        c_out = math.ceil(c_out * nfps - 1e-6) / nfps
        n_frames = max(1, int(round((c_out - c_in) * nfps)))
        # 写出的秒数:起点向下、终点向上取到微秒,保证「from_s 之后的第一个原生帧」就是 from_frame 那一帧
        c_in_w, c_out_w = math.floor(c_in * 1e6) / 1e6, math.ceil(c_out * 1e6) / 1e6
        cf = [min(n_frames - 1, max(0, int(math.floor((t - c_in) * nfps + 0.5)))) for t in ts]
        out.update({
            "clip": f"clips/{sid}.mp4", "clip_frames": cf, "native_fps": round(nfps, 5),
            "src_in_s": round(t_in, 4), "src_out_s": round(src_t(dur), 4),
            "clip_in_s": c_in_w, "clip_out_s": c_out_w,
        })
        profile = profiles.get(s["src"]) or info.get("profile") or "rec709"
        clip = {
            "id": sid, "src": s["src"], "from_s": c_in_w, "to_s": c_out_w,
            "from_frame": int(round(c_in * nfps)), "frames": n_frames,
            "profile": profile, "native_fps": round(nfps, 5), "fix": out["fix"], "look": look,
            "audio": bool(audio_cfg),
        }
        clip["hash"] = hashlib.sha1(json.dumps(clip, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
        clips_out.append(clip)

    # ── 采访机位运行:主轨上首尾相接、同一素材的 interview 镜头 ──
    main = sorted([r for r in placed if r["layer"] == 0], key=lambda r: r["cut"])
    runs, cur = [], []
    for r in main:
        is_iv = (r["s"].get("move") == "interview")
        if is_iv and cur and cur[-1]["end"] == r["cut"] and cur[-1]["s"].get("src") == r["s"].get("src"):
            cur.append(r)
        else:
            if cur:
                runs.append(cur)
            cur = [r] if is_iv else []
    if cur:
        runs.append(cur)
    for run in runs:
        tight = False
        for i, r in enumerate(run):
            if i > 0 and (r["end"] - r["cut"]) >= 15:
                tight = not tight
            r["out"]["move"].update({"run_from": run[0]["from"], "run_to": run[-1]["to"], "tight": tight})

    # 采访说话中只能硬切
    for i in range(1, len(main)):
        a, b = main[i - 1], main[i]
        if a["s"].get("move") == "interview" and b["s"].get("move") == "interview" and b["in_tr"]["type"] != "cut":
            msg = f"[转场] {b['s']['id']}:采访说话中(两段 interview 之间)用了 {b['in_tr']['type']},只能硬切"
            (lint.err if style == "testimonial" else lint.warn)(msg)

    # 卡拍检查
    if ctx.grid and style in BEAT_STYLES:
        g = ctx.grid
        for r in main[1:]:
            bp = g.bp_of_frame(r["cut"])
            near = round(bp * 2) / 2
            d = r["cut"] - g.frame(near)
            if abs(d) > 2:
                lint.warn(f"[卡拍] {r['s']['id']}:切点 {r['cut']} 离最近的 8 分音符({g.label(g.frame(near))},帧 {g.frame(near)})差 {d:+d} 帧")

    # ── 文字 ──
    texts_out = []
    for i, tx in enumerate(edit.get("texts", []) or []):
        tid = tx.get("id") or f"T{i + 1:02d}"
        where = f"文字 {tid}"
        typ = tx.get("type")
        if typ not in TEXT_DEFAULTS:
            lint.err(f"[文字] {where}:不认识的类型 {typ}(可用 {' / '.join(TEXT_DEFAULTS)})")
        f0, bp0 = ctx.T(tx.get("at", 0), where)
        if f0 is None:
            continue
        if "dur" in tx:
            f1, _ = ctx.D(tx["dur"], f0, bp0, where)
        elif typ in TEXT_DUR:
            f1 = f0 + TEXT_DUR[typ]
        elif typ in ("lockup", "endcard"):
            f1 = duration
        else:
            lint.err(f"[文字] {where}:缺 dur")
            f1 = f0 + 30
        if f1 is None:
            continue
        o = {"id": tid, "type": typ, "from": f0, "to": f1, "z": tx.get("z", 10)}
        o.update(TEXT_DEFAULTS.get(typ, {}))
        o.update({k: v for k, v in tx.items() if k not in ("id", "type", "at", "dur")})
        texts_out.append(o)
        if f0 < 0 or f1 > duration:
            lint.err(f"[文字] {where}:{f0}–{f1} 超出全片 0–{duration}")
        if f1 <= f0:
            lint.err(f"[文字] {where}:结尾不晚于开头")
        if typ == "qcard" and f1 - f0 < QCARD_MIN:
            lint.err(f"[问题卡] {where}:只停 {f1 - f0} 帧,至少 {QCARD_MIN} 帧(≈3.7 s)才读得完")

    # ── 字幕 ──
    cap_cfg = edit.get("captions", {}) or {}
    caps = []
    cap_style = None
    if cap_cfg.get("enabled"):
        mx = float(cap_cfg.get("max", 17))
        cap_style = {k: v for k, v in cap_cfg.items() if k != "enabled"}
        cap_style.setdefault("max", 17)
        cap_style.setdefault("size", 46)
        cap_style.setdefault("bottom", 90)
        if not ctx.words:
            lint.err("[字幕] 开了字幕,但 words.json 是空的或主轨上没有对应的采访镜头")
        else:
            caps = CAP.make_captions(ctx.words, mx, float(cap_cfg.get("gap", 9)),
                                     CAP.load_dict(proj, ",".join(cap_cfg.get("dict", []) or [])),
                                     CAP.parse_fix("", proj))
            for c in caps:
                if CAP.width(c["text"]) > mx:
                    lint.warn(f"[字幕] {c['from']}–{c['to']}「{c['text']}」宽 {CAP.width(c['text']):.1f} 字,超过 {mx:g}")
                if c["to"] > duration:
                    lint.warn(f"[字幕] 「{c['text']}」到 {c['to']},超出全片")
            for t in texts_out:
                if t["type"] == "qcard":
                    ov = [c for c in caps if c["from"] < t["to"] and c["to"] > t["from"]]
                    if ov:
                        lint.warn(f"[字幕] 问题卡 {t['id']}({t['from']}–{t['to']})上叠着 {len(ov)} 条字幕:「{ov[0]['text']}」…")

    # ── 声音 ──
    voice = []
    for r in sorted(placed, key=lambda r: r["cut"]):
        o = r.get("out")
        if not o or not o["audio"] or not o["clip"]:
            continue
        cs = (o["src_in_s"] + (o["from"] - o["cut"]) / fps - o["clip_in_s"]) * fps
        voice.append({"shot": o["id"], "layer": o["layer"], "clip": o["clip"], "from": o["from"], "to": o["to"],
                      "clip_start_frame": int(round(cs)), "clip_start_s": round(cs / fps, 4),
                      "gain": o["audio"]["gain"], "fade_in": o["audio"]["fade_in"], "fade_out": o["audio"]["fade_out"]})
    audio_out = {"music": None, "sfx": [], "voice": voice, "voice_gain": float(audio.get("voice_gain", 1.0))}
    if music:
        gain = float(music.get("gain", 1.0))
        keys = []
        for kk in music.get("keys", []) or []:
            kf, _ = ctx.T(kk[0], "音乐音量关键点", lead=False)
            if kf is not None:
                keys.append([kf, float(kk[1])])
        keys.sort()
        if not keys or keys[0][0] > 0:
            keys.insert(0, [0, gain])
        duck = music.get("duck")
        ranges = []
        if duck:
            pre, post = int(duck.get("pad_pre", 3)), int(duck.get("pad_post", 6))
            merge = int(duck.get("merge", int(duck.get("attack", 6)) + int(duck.get("release", 8))))
            spans = sorted((math.floor(w["f"]) - pre, math.ceil(w["e"]) + post) for w in ctx.words)
            for a0, b0 in spans:
                a0, b0 = max(0, a0), min(duration, b0)
                if b0 <= a0:
                    continue
                if ranges and a0 - ranges[-1][1] <= merge:
                    ranges[-1][1] = max(ranges[-1][1], b0)
                else:
                    ranges.append([a0, b0])
        trim_s = float(music.get("trim_s", 0))
        audio_out["music"] = {
            "src": music.get("src"), "from": m_at, "trim_s": trim_s, "trim_frames": int(round(trim_s * fps)),
            "gain": gain, "gain_keys": keys, "duck_ranges": ranges,
            "duck_db": float(duck.get("db", -13)) if duck else 0.0,
            "attack": int(duck.get("attack", 6)) if duck else 6, "release": int(duck.get("release", 8)) if duck else 8,
        }
        for k in ("edl", "note"):
            if k in music:
                audio_out["music"][k] = music[k]
        if music.get("src") and not os.path.exists(os.path.join(proj, "public", music["src"])):
            lint.warn(f"[音乐] public/{music['src']} 不存在")
    for i, fx in enumerate(audio.get("sfx", []) or []):
        fid = fx.get("id") or f"sfx{i + 1}"
        f0, _ = ctx.T(fx.get("at", 0), f"音效 {fid}", lead=False)
        if f0 is None:
            continue
        o = {"id": fid, "src": fx.get("src"), "from": f0, "gain": float(fx.get("gain", 1.0))}
        o.update({k: v for k, v in fx.items() if k not in ("id", "src", "at", "gain")})
        audio_out["sfx"].append(o)
        if not 0 <= f0 < duration:
            lint.err(f"[音效] {fid}:帧 {f0} 不在全片 0–{duration} 之内")
        if fx.get("src") and not os.path.exists(os.path.join(proj, "public", fx["src"])):
            lint.warn(f"[音效] {fid}:public/{fx['src']} 不存在")
    audio_out["sfx"].sort(key=lambda x: x["from"])

    # ── 段落 ──
    sections = []
    for i, sc in enumerate(edit.get("sections", []) or []):
        f0, _ = ctx.T(sc.get("at", 0), f"段落 {sc.get('name', i)}")
        if f0 is not None:
            sections.append({"name": sc.get("name", f"段 {i + 1}"), "from": f0})
    sections.sort(key=lambda x: x["from"])
    for a_, b_ in zip(sections, sections[1:] + [{"from": duration}]):
        a_["to"] = b_["from"]

    grid_out = None
    if ctx.grid:
        g = ctx.grid
        grid_out = {"bpm": round(g.bpm, 3), "beat_s": g.beat_s, "offset_s": round(g.offset, 4), "beats_per_bar": g.bpb,
                    "lead_frames": g.lead, "beat_frames": round(g.beat_f, 4)}
    tl = {
        "fps": fps, "width": int(pj.get("width", 1920)), "height": int(pj.get("height", 1080)),
        "duration": duration, "style": style, "title": pj.get("title", edit.get("title", "")),
        "fonts": pj.get("fonts", {}), "brand": pj.get("brand", {}), "grid": grid_out, "sections": sections,
        "shots": sorted(shots_out, key=lambda o: (o["layer"], o["cut"])),
        "texts": sorted(texts_out, key=lambda o: o["from"]),
        "captions": caps, "caption_style": cap_style,
        "audio": audio_out, "clips": clips_out,
        "lint": {"errors": lint.errors, "warnings": lint.warnings},
    }
    return tl, lint


def remap_words(words, main_rec, fps, lint):
    """words.json 带 shot / ss / se(paper_edit.py 写的)时,按主轨上这一镜的实际位置重算 f / e;
    镜头被删掉或剪短的字直接丢掉。没有 shot 字段的照原样用。"""
    recs = {r["s"]["id"]: r for r in main_rec}
    out, dropped, moved = [], 0, 0
    for w in words:
        sid = w.get("shot")
        if sid is None or "ss" not in w:
            out.append(w)
            continue
        r = recs.get(sid)
        if r is None:
            dropped += 1
            continue
        t_in = float(r["s"].get("in", 0))
        f = r["cut"] + (float(w["ss"]) - t_in) * fps
        e = r["cut"] + (float(w["se"]) - t_in) * fps
        if f < r["cut"] - 0.5 or f >= r["end"]:
            dropped += 1
            continue
        e = min(e, r["end"])
        if abs(f - w["f"]) > 0.5:
            moved += 1
        nw = dict(w)
        nw["f"], nw["e"] = round(f, 1), round(e, 1)
        out.append(nw)
    if dropped:
        lint.warn(f"[逐字] words.json 里 {dropped} 个字所在的镜头不在主轨上或被剪掉了,已忽略")
    if moved:
        lint.info.append(f"words.json 里 {moved} 个字的帧号和当前时间线不同(纸面剪辑后插了镜头),已按镜头位置重算")
    return sorted(out, key=lambda w: w["f"])


def tc(f, fps):
    return f"{int(f // fps // 60)}:{f / fps % 60:05.2f}"


def summary(tl, lint, quiet=False):
    fps, D = tl["fps"], tl["duration"]
    main = [s for s in tl["shots"] if s["layer"] == 0]
    ins = [s for s in tl["shots"] if s["layer"] > 0]
    n = len(main)
    print(f"全片 {D} 帧 = {tc(D, fps)}({D / fps:.2f} s)· 风格 {tl['style']} · 主轨 {n} 镜、插入 {len(ins)} 镜 · "
          f"平均镜长 {D / max(1, n):.1f} 帧({D / max(1, n) / fps:.2f} s)· 文字 {len(tl['texts'])} · 字幕 {len(tl['captions'])} · "
          f"音效 {len(tl['audio']['sfx'])} · 原声段 {len(tl['audio']['voice'])}")
    secs = tl["sections"]
    if not secs:
        g = tl.get("grid")
        step = int(round(g["beat_frames"] * g["beats_per_bar"] * 8)) if g else 30 * fps
        secs = [{"name": f"{tc(a, fps)}", "from": a, "to": min(D, a + step)} for a in range(0, D, max(1, step))]
    if not quiet:
        print("  段落              帧            镜数  平均镜长   每分钟切点")
        for sc in secs:
            cuts = [s for s in main if sc["from"] <= s["cut"] < sc["to"]]
            ln = sc["to"] - sc["from"]
            avg = (sum(min(s["end"], sc["to"]) - s["cut"] for s in cuts) / len(cuts)) if cuts else 0
            print(f"  {sc['name'][:14]:14s} {sc['from']:5d}–{sc['to']:<5d}  {len(cuts):5d}  {avg / fps:6.2f} s   {len(cuts) / max(1, ln) * fps * 60:7.1f}")
    for m in lint.info:
        print(f"  ℹ️ {m}")
    for w in lint.warnings:
        print(f"  ⚠️ {w}")
    for e in lint.errors:
        print(f"  ❌ {e}")


def main():
    ap = argparse.ArgumentParser(description="edit.json → src/timeline.json + lint")
    ap.add_argument("proj")
    ap.add_argument("--out", default=None)
    ap.add_argument("--quiet", action="store_true", help="不打印分段表")
    a = ap.parse_args()
    proj = os.path.abspath(a.proj)
    tl, lint = compile_project(proj, a.quiet)
    out = a.out or os.path.join(proj, "src", "timeline.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(tl, fh, ensure_ascii=False, indent=1)
    summary(tl, lint, a.quiet)
    if lint.errors:
        print(f"✗ {len(lint.errors)} 个错误、{len(lint.warnings)} 个警告 → {out}(已写出,但修好错误前不能渲染)")
        sys.exit(1)
    print(f"✓ {len(lint.warnings)} 个警告 → {out}")


if __name__ == "__main__":
    main()
