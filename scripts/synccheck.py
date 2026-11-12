#!/usr/bin/env python3
"""synccheck.py · 成片卡点核对 + 意外黑场。

用法：
  python3 synccheck.py <成片.mp4> <proj> [--range a-b] [--json out.json] [--style hype] [--window 4]
                       [--band 1500-4000] [--rise 10] [--floor -40] [--black-th 0.10] [--black-d 0.10] [--min-jump 10]
  例：python3 synccheck.py out/preview.mp4 <proj> --range 0-449      # 15 秒预演（成片帧 0–449）
      python3 synccheck.py out/film.mp4 <proj>                       # 整片
      python3 synccheck.py out/film.mp4 <proj> --range 400-800       # 整片里只查这一段
  范围：`a-b` 闭区间（同 Remotion --frames），`a:b` 半开。视频帧数等于范围长度 → 视频就是这一段（预演）；
        等于 timeline 时长 → 视频是整片，只查这一段。不给 --range：视频应是整片。
  🔴 要解码整段视频，是重活：自动钻进 scripts/queue.sh 排队（--no-queue 跳过）。

符号（全 skill 统一）：
  lead_frames = (音频时间 − 画面切点时间) × fps；lead_frames > 0 = 画面先到（画面领先）。
  设计上画面领先 0–2 帧；「±1.5 帧」指相对 1 帧提前量的误差，即 lead ∈ [−0.5, 2.5]。

读 <proj>/src/timeline.json：
  cuts（timeline.py 写）：[{f, shot, type, layer}]；没有就按主轨（layer 0）镜头的 cut / in_tr 推出来。
  accents（可选）：[{f, t, s, kind}]，成片时间的重音，s = 在全曲起音里的强度百分位（0–1）。
  expected_dark（可选）：[[from, to, 原因]]，设计好的暗场（dip、src:null 黑场镜头、深底落版卡）；没有就按 dip 转场、
                       黑场镜头、endcard 推断，并在报告里注明「推断」。
  grid / sections（可选）：报拍位和所在段落。

分项（每项 pass / warn / fail / skipped，汇总取最差；任一 fail 退出码 1）：
  frames  视频帧数 = 范围帧数（或整片时长）。
  cuts    只测硬切换（cut / punch / whip）：设计帧 ±window 内帧差最大处 = 实测切点。cut 差 ≥ 2 帧 fail、差 1 帧 warn；
          punch / whip 的拖影让最大帧差位置不准，差 > 2 帧只 warn。fade / wipe / sweep / dip 是过程转场，单列不测；
          src:null 黑场镜头、帧差 < --min-jump（同画面接续，看不出切）也单列不计。
  accents 有 accents 时，和 timeline.py 同一口径：画面目标帧 = 重音帧（accents[].f，没减 lead）− grid.lead_frames；
          切点离某个强重音（s ≥ 0.7）的目标帧 ≤ 2 帧算命中。只算 layer 0 的硬切（黑场镜头除外）；sections 里有
          kind = drop 的段时只算 drop 段里的（加速段按 8 分音符切、暗场段没有鼓，算进去会把定版片误判），全片命中率另报。
          切点用实测的（看得出切且离设计帧 ≤ 2 帧），否则用设计帧。少于 4 个不判。
          hype：< 60% fail、< 75% warn；launch：< 60% warn（和 timeline.py 一致）；其他风格只报数。没有 accents 就 skipped。
          timeline 的 cuts[] 带了 accent / acc_d 时，逐镜对一遍，判得不一样的列出来。
  onsets  成片音轨 1.5–4 kHz 的起音（军鼓 / 拍手）离切点的 lead。鼓点密时最近匹配会跳到旁边的起音，所以只 warn 不 fail：
          中位数不在 [−0.5, 2.5] 或 |lead − 1| ≤ 1.5 的比例 < 85% → warn。整体时移用 audio_sync.py 测，不靠它。
  black   blackdetect 的黑场减去 expected_dark（前后各放宽 2 帧）后，剩下 ≥ --black-d 秒的段 = 意外黑场 → fail。
          不再因为「接近片头片尾」放行。
检查边界：只看画面帧差和音轨起音，不判断节奏好不好、镜头选得对不对；黑场阈值 pix_th 0.10，暗而不黑的画面不算黑场。
依赖：numpy；ffmpeg（PATH 里，或环境变量 FFMPEG）。兼容 Python 3.9。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ftvlib as L  # noqa: E402  只用 ensure_queued

W, H = 96, 54
SR = 22050
HARD = ("cut", "punch", "whip")
PROCESS = ("fade", "wipe", "sweep", "dip")
STRONG = 0.7     # 强重音：s ≥ 0.7（timeline.py STRONG_S）
HIT_WIN = 2      # 命中：离画面目标帧 ≤ 2 帧（timeline.py HIT_WIN）
HIT_MIN_N = 4    # 参与统计的硬切少于 4 个不判（timeline.py HIT_MIN_N）
DEFINITION = "lead_frames = (音频时间 − 画面切点时间) × fps；> 0 = 画面领先"
ORDER = {"pass": 0, "skipped": 0, "warn": 1, "fail": 2}


def parse_range(spec, total):
    s = str(spec).strip()
    if ":" in s:
        x, y = s.split(":", 1)
        a, b = int(x or 0), (int(y) if y.strip() else total)
    else:
        i = s.index("-", 1) if "-" in s[1:] else (0 if s.startswith("-") else -1)
        if i < 0:
            a, b = int(s), int(s) + 1
        else:
            x, y = s[:i], s[i + 1:]
            a, b = int(x or 0), (int(y) + 1 if y.strip() else total)
    if a < 0 or b <= a:
        raise ValueError("范围 %r 不对" % spec)
    return a, b


def worst(states):
    states = [s for s in states if s]
    return max(states, key=lambda s: ORDER.get(s, 0)) if states else "skipped"


def derive_cuts(tl):
    """timeline 没有 cuts 时：主轨镜头的切点（老 timeline 的行为）"""
    out = []
    for s in sorted([s for s in tl.get("shots") or [] if s.get("layer", 0) == 0], key=lambda s: s["cut"]):
        if s["cut"] <= 0:
            continue
        out.append({"f": s["cut"], "shot": s["id"], "type": (s.get("in_tr") or {}).get("type", "cut"), "layer": 0,
                    "pre": (s.get("in_tr") or {}).get("pre", 0), "post": (s.get("in_tr") or {}).get("post", 0)})
    return out


def derive_dark(tl, cuts):
    """timeline 没有 expected_dark 时的推断：dip 窗口、src:null 黑场镜头、endcard（深底面板）"""
    exp = []
    shots = tl.get("shots") or []
    by_id = {s["id"]: s for s in shots}
    for c in cuts:
        if c.get("type") == "dip":
            tr = (by_id.get(c.get("shot")) or {}).get("in_tr") or {}
            ln = tr.get("len", 8) or 8
            pre = c.get("pre", tr.get("pre", ln // 2))
            post = c.get("post", tr.get("post", ln - ln // 2))
            exp.append([c["f"] - pre, c["f"] + post, "dip 转场（推断）"])
    for s in shots:
        if not s.get("src") and not s.get("clip"):
            exp.append([s.get("from", s["cut"]), s.get("to", s.get("end", s["cut"])), "黑场镜头 %s（推断）" % s["id"]])
    main = sorted([s for s in shots if s.get("layer", 0) == 0], key=lambda s: s["cut"])
    if main and (main[0].get("in_tr") or {}).get("type") in ("dip", "fade"):
        exp.append([0, (main[0]["in_tr"].get("post") or 4) + 1, "片头淡入（推断）"])
    if main and (main[-1].get("out_tr") or {}).get("type") in ("dip", "fade"):
        exp.append([main[-1].get("end", main[-1]["to"]) - (main[-1]["out_tr"].get("pre") or 4), tl["duration"], "片尾淡出（推断）"])
    for t in tl.get("texts") or []:
        if t.get("type") == "endcard":
            exp.append([t["from"], t["to"], "endcard 深底落版（推断）"])
    return exp


def onsets(ff, path, band, rise, floor):
    lo, hi = (float(x) for x in band.split("-"))
    r = subprocess.run([ff, "-hide_banner", "-nostdin", "-v", "error", "-i", path, "-vn", "-map", "0:a:0?", "-ac", "1", "-ar", str(SR),
                        "-af", "highpass=f=%g,lowpass=f=%g" % (lo, hi), "-f", "f32le", "-"], capture_output=True)
    au = np.frombuffer(r.stdout, np.float32)
    if len(au) < 2000:
        return None
    hop = 22  # ≈ 1 ms
    M = 20 * np.log10(np.sqrt(np.add.reduceat(au.astype(np.float64) ** 2, np.arange(0, len(au) - hop, hop)) / hop) + 1e-7)
    t = np.arange(len(M)) * hop / SR
    cs = np.concatenate([[0], np.cumsum(M)])
    prev = np.full(len(M), np.inf)
    prev[10:] = (cs[10:-1] - cs[:-11]) / 10
    out = []
    for i in np.where((M - prev > rise) & (M > floor))[0]:
        if not out or t[i] - out[-1] > 0.08:
            out.append(t[i])
    return np.array(out)


def uncovered(seg, cover):
    """seg=[a,b) 减去 cover 里的区间，返回剩下的片段"""
    rest = [list(seg)]
    for c0, c1 in sorted(cover):
        nxt = []
        for a, b in rest:
            if c1 <= a or c0 >= b:
                nxt.append([a, b])
                continue
            if a < c0:
                nxt.append([a, c0])
            if c1 < b:
                nxt.append([c1, b])
        rest = nxt
    return [r for r in rest if r[1] > r[0]]


def analyze(a):
    ff = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not ff:
        sys.exit("找不到 ffmpeg：装好放进 PATH，或设环境变量 FFMPEG")
    tl = json.load(open(os.path.join(a.proj, "src", "timeline.json"), encoding="utf-8"))
    fps = float(tl.get("fps") or 30)
    total = int(tl["duration"])
    style = a.style or tl.get("style") or "custom"
    grid = tl.get("grid")
    if not grid and os.path.exists(os.path.join(a.proj, "beats.json")):
        b = json.load(open(os.path.join(a.proj, "beats.json"), encoding="utf-8"))
        grid = {"beat_s": b["beat_s"], "offset_s": b["offset_s"], "beats_per_bar": b.get("beats_per_bar", 4)}
    notes = []

    # ── 画面：一次解码，同时 blackdetect ──
    p = subprocess.run([ff, "-hide_banner", "-nostdin", "-nostats", "-v", "info", "-i", a.mp4, "-an",
                        "-vf", "blackdetect=d=%g:pix_th=%g,scale=%d:%d,format=gray" % (a.black_d, a.black_th, W, H),
                        "-f", "rawvideo", "-"], capture_output=True)
    if p.returncode != 0:
        sys.exit("ffmpeg 解码失败：" + p.stderr.decode("utf-8", "replace")[-400:])
    v = np.frombuffer(p.stdout, np.uint8).reshape(-1, H, W).astype(np.float32)
    n = len(v)
    d = np.abs(np.diff(v, axis=0)).mean(axis=(1, 2)) if n > 1 else np.zeros(0)  # d[i] = 帧 i → i+1
    blacks = [(float(x), float(y)) for x, y in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", p.stderr.decode("utf-8", "replace"))]

    # ── 范围：视频第 0 帧 = 成片第 offset 帧，查成片帧 [ra, rb) ──
    items = {}
    if a.range:
        ra, rb = parse_range(a.range, total)
        rb = min(rb, total)
        if n == rb - ra:
            offset, expect, mode = ra, rb - ra, "视频就是这一段"
        elif n == total:
            offset, expect, mode = 0, total, "视频是整片，只查这一段"
        elif abs(n - (rb - ra)) <= 3:
            offset, expect, mode = ra, rb - ra, "视频应是这一段，但帧数对不上"
        else:
            offset, expect, mode = 0, total, "视频帧数既不等于范围也不等于整片，按整片对待"
    else:
        ra, rb, offset, expect, mode = 0, total, 0, total, "整片"
    rb = min(rb, offset + n)
    items["frames"] = {"status": "pass" if n == expect else "fail", "video_frames": n, "expected": expect,
                       "detail": "视频 %d 帧，应为 %d 帧（%s）" % (n, expect, mode)}

    # ── 切点 ──
    cuts = tl.get("cuts")
    cuts_src = "timeline.cuts"
    if not isinstance(cuts, list) or not cuts:
        cuts, cuts_src = derive_cuts(tl), "按主轨镜头推断（timeline 没有 cuts）"
        notes.append("timeline 没有 cuts 字段：切点按主轨镜头推断")
    shots = {s["id"]: s for s in tl.get("shots") or []}
    sections = [s for s in tl.get("sections") or [] if isinstance(s, dict) and "from" in s and "to" in s]
    drop_secs = [s for s in sections if s.get("kind") == "drop"]
    accents = [x for x in tl.get("accents") or [] if isinstance(x, dict) and ("t" in x or "f" in x)]
    # accents[].f 是音频时刻（没减 lead）；画面目标帧 = f − lead_frames（和 timeline.py 同一口径）
    acc_f = np.array([float(x["f"]) if "f" in x else float(x["t"]) * fps for x in accents]) if accents else np.array([])
    acc_s = np.array([float(x.get("s", 0)) for x in accents]) if accents else np.array([])
    acc_k = [x.get("kind", "") for x in accents]
    lead_fr = int((grid or {}).get("lead_frames", 1)) if grid else 0
    att = onsets(ff, a.mp4, a.band, a.rise, a.floor)
    att_film = att + offset / fps if att is not None else None
    beat_f = grid["beat_s"] * fps if grid else None
    max_gap = (beat_f / 2) if beat_f else 8

    rows, process, outside = [], [], 0
    for c in sorted(cuts, key=lambda c: c["f"]):
        f, typ = int(round(c["f"])), c.get("type", "cut")
        sh = shots.get(c.get("shot"))
        if c.get("black") or (sh is not None and not sh.get("src") and not sh.get("clip")):
            typ = "black"
        if f <= ra or f >= rb:
            outside += 1
            continue
        r = {"shot": c.get("shot"), "type": typ, "layer": c.get("layer", 0), "design": f}
        if "accent" in c:  # timeline.py 按设计帧算的命中（accent 为 null = 没落在强重音上）
            r["tl_hit"] = c.get("accent") is not None
        for sec in sections:
            if sec["from"] <= f < sec["to"]:
                r["section"] = sec.get("name")
                r["section_kind"] = sec.get("kind")
        if grid:
            bp = (f / fps - grid["offset_s"]) / grid["beat_s"]
            near = round(bp * 2) / 2
            bpb = grid.get("beats_per_bar", 4)
            r["pos"] = "%d.%d%s" % (int(near // bpb) + 1, int(near % bpb) + 1, "+" if near % 1 else "")
            r["grid_off"] = round(f - (grid["offset_s"] + near * grid["beat_s"]) * fps, 2)
        if typ not in HARD and typ != "black":  # fade / wipe / sweep / dip，以及不认识的类型：过程转场，不测切点
            process.append(r)
            continue
        lf = f - offset
        lo_, hi_ = max(1, lf - a.window), min(n - 1, lf + a.window)
        if hi_ < lo_:
            outside += 1
            continue
        k = lo_ + int(np.argmax(d[lo_ - 1:hi_]))
        r.update(measured=k + offset, dv=k + offset - f, jump=round(float(d[k - 1]), 1))
        cut_t = (k + offset) / fps
        if att_film is not None and len(att_film):
            j = int(np.argmin(np.abs(att_film - cut_t)))
            lead = (att_film[j] - cut_t) * fps
            if abs(lead) <= max_gap:
                r["onset_lead"] = round(float(lead), 2)
        if typ == "black":
            r["note"] = "黑场镜头，不计"
        elif r["jump"] < a.min_jump:
            r["note"] = "帧差太小（同画面接续，看不出切），不计"
        rows.append(r)

    hard = [r for r in rows if r["type"] in HARD and "note" not in r]
    # cuts：设计 vs 实测
    bad = [r for r in hard if (r["type"] == "cut" and abs(r["dv"]) >= 2)]
    soft = [r for r in hard if (r["type"] == "cut" and abs(r["dv"]) == 1) or (r["type"] != "cut" and abs(r["dv"]) > 2)]
    dvs = [r["dv"] for r in hard]
    items["cuts"] = {"status": "skipped" if not hard else ("fail" if bad else ("warn" if soft else "pass")),
                     "hard": len(hard), "exact": sum(1 for x in dvs if x == 0),
                     "dv_hist": {"%+d" % k: dvs.count(k) for k in sorted(set(dvs))},
                     "off": [(r["shot"], r["design"], r["dv"]) for r in bad + soft],
                     "process_not_measured": len(process), "excluded": len(rows) - len(hard), "outside_range": outside,
                     "source": cuts_src,
                     "detail": "硬切 %d 个，实测切点 = 设计帧 %d 个；过程转场 %d 个不测；黑场 / 同画面 %d 个不计；范围外 %d 个" % (
                         len(hard), sum(1 for x in dvs if x == 0), len(process), len(rows) - len(hard), outside)}

    # accents：落在强重音 ±2 帧（口径和 timeline.py 一致）
    #   范围：layer 0 的硬切（cut / punch / whip，黑场镜头除外）；有 kind = drop 的段落时只算 drop 段里的。
    #   命中：切点离某个强重音（s ≥ 0.7）的画面目标帧（重音帧 − lead_frames）≤ 2 帧。
    #   切点用实测的（看得出切、且离设计帧 ≤ 2 帧），否则用设计帧。少于 4 个不判（timeline.py 的 HIT_MIN_N）。
    def in_drop(f):
        return any(sc["from"] <= f < sc["to"] for sc in drop_secs)

    strong_idx = np.where(acc_s >= STRONG)[0] if len(acc_s) else np.array([], int)
    for r in rows:
        if r["type"] not in HARD or not len(acc_f):
            continue
        cf = r["measured"] if ("measured" in r and "note" not in r and abs(r["dv"]) <= 2) else r["design"]
        r["accent_cut"] = cf
        j = int(np.argmin(np.abs(acc_f - cf)))
        r["accent_lead"] = round(float(acc_f[j] - cf), 2)
        r["accent_s"] = round(float(acc_s[j]), 3)
        r["accent_kind"] = acc_k[j]
        if len(strong_idx):
            tgt = acc_f[strong_idx] - lead_fr
            dist = np.abs(tgt - cf)
            near = np.where(dist <= HIT_WIN)[0]
            if len(near):  # 同距离取更强的
                js = strong_idx[sorted(near, key=lambda q: (dist[q], -acc_s[strong_idx[q]]))[0]]
            else:
                js = strong_idx[int(np.argmin(dist))]
            r["strong_lead"] = round(float(acc_f[js] - cf), 2)
            r["strong_d"] = round(float(cf - (acc_f[js] - lead_fr)), 2)
            r["strong_s"] = round(float(acc_s[js]), 3)
            r["strong_hit"] = abs(r["strong_d"]) <= HIT_WIN
    pool = [r for r in rows if r["type"] in HARD and r.get("layer", 0) == 0]
    judged = [r for r in pool if in_drop(r["design"])] if drop_secs else pool
    scope = "drop 段" if drop_secs else "全片"
    if not len(acc_f):
        items["accents"] = {"status": "skipped", "detail": "timeline 没有 accents（旧版 timeline.py 编译的，或 beats.json 没有重音表），没测重音命中"}
    elif not judged:
        items["accents"] = {"status": "skipped", "detail": "范围内没有%s的主轨硬切" % scope}
    else:
        hits = sum(1 for r in judged if r.get("strong_hit"))
        rate = hits / len(judged)
        all_hits = sum(1 for r in pool if r.get("strong_hit"))
        if len(judged) < HIT_MIN_N:
            st, rule = "skipped", "只有 %d 个硬切（< %d），命中率不判" % (len(judged), HIT_MIN_N)
        elif style == "hype":
            st = "fail" if rate < 0.6 else ("warn" if rate < 0.75 else "pass")
            rule = "hype：≥ 60% 才算通过，< 75% 告警"
        elif style == "launch":
            st = "warn" if rate < 0.6 else "pass"
            rule = "launch：< 60% 只告警（和 timeline.py 一致）"
        else:
            st, rule = "pass", "%s：只报数，不判" % style
        agree = [r for r in judged if "tl_hit" in r]
        differ = [r["shot"] for r in agree if bool(r.get("strong_hit")) != r["tl_hit"]]
        ds = [r["strong_d"] for r in judged if "strong_d" in r and r.get("strong_hit")]
        items["accents"] = {"status": st, "scope": scope, "hit_rate": round(rate, 3), "hits": hits, "n": len(judged),
                            "all_rate": round(all_hits / len(pool), 3) if pool else None, "all_hits": all_hits, "all_n": len(pool),
                            "miss": [r["shot"] for r in judged if not r.get("strong_hit")],
                            "strong_threshold": STRONG, "tolerance_frames": HIT_WIN, "lead_frames": lead_fr, "rule": rule,
                            "median_hit_d": round(float(np.median(ds)), 2) if ds else None,
                            "timeline_disagree": differ,
                            "accents_total": int(len(acc_f)), "accents_strong": int(len(strong_idx)),
                            "detail": "%s主轨硬切落在强重音（s ≥ %.1f，目标帧 = 重音帧 − %d）±%d 帧内：%d/%d = %.0f%%（全片 %d/%d）；%s%s" % (
                                scope, STRONG, lead_fr, HIT_WIN, hits, len(judged), rate * 100, all_hits, len(pool), rule,
                                ("；和 timeline.py 判得不一样：" + "、".join(differ)) if differ else "")}

    # onsets：成片音轨起音（只告警）
    leads = np.array([r["onset_lead"] for r in hard if "onset_lead" in r])
    if att is None:
        items["onsets"] = {"status": "skipped", "detail": "成片没有音轨"}
    elif not len(leads):
        items["onsets"] = {"status": "skipped" if not hard else "warn",
                           "detail": "切点附近没找到 %s Hz 的起音（没有鼓 / 拍手？试试 --band 30-150）" % a.band}
    else:
        med = float(np.median(leads))
        ok = float((np.abs(leads - 1.0) <= 1.5).mean())
        st = "pass" if (-0.5 <= med <= 2.5 and ok >= 0.85) else "warn"
        items["onsets"] = {"status": st, "median_lead": round(med, 2), "within_1p5_of_1": round(ok, 3), "matched": int(len(leads)),
                           "hard": len(hard), "onsets_found": int(len(att)),
                           "detail": "起音 lead 中位 %+.2f 帧（目标 0–2），|lead − 1| ≤ 1.5 的比例 %.0f%%（目标 ≥ 85%%），%d/%d 个切点附近有起音；"
                                     "最近匹配在鼓点密时会跳到旁边的起音，只告警，整体时移看 audio_sync" % (med, ok * 100, len(leads), len(hard))}

    # black：expected_dark 以外的黑场
    exp = tl.get("expected_dark")
    if isinstance(exp, list):
        exp_src = "timeline.expected_dark"
        exp = [list(e[:3]) + ([""] if len(e) < 3 else []) for e in exp if isinstance(e, (list, tuple)) and len(e) >= 2]
    else:
        exp, exp_src = derive_dark(tl, sorted(cuts, key=lambda c: c["f"])), "推断（timeline 没有 expected_dark）"
        notes.append("timeline 没有 expected_dark 字段：暗场按 dip / 黑场镜头 / endcard 推断")
    tol = 2
    cover = [(e[0] - tol, e[1] + tol) for e in exp]
    min_len = max(a.black_d * fps, 3)
    blk = []
    for s0, s1 in blacks:
        f0, f1 = s0 * fps + offset, s1 * fps + offset
        f0, f1 = max(f0, ra), min(f1, rb)
        if f1 <= f0:
            continue
        rest = [r for r in uncovered((f0, f1), cover) if r[1] - r[0] >= min_len]
        why = [e[2] for e in exp if e[0] - tol < f1 and e[1] + tol > f0]
        blk.append({"from": round(f0, 1), "to": round(f1, 1), "expected": not rest, "matches": why,
                    "unexpected": [[round(x, 1), round(y, 1)] for x, y in rest]})
    nbad = sum(1 for b in blk if not b["expected"])
    items["black"] = {"status": "fail" if nbad else "pass", "segments": blk, "expected_dark": exp, "source": exp_src,
                      "pix_th": a.black_th, "min_s": a.black_d,
                      "detail": "黑场 %d 段，意外 %d 段（声明的暗场前后各放宽 %d 帧；暗场来源：%s）" % (len(blk), nbad, tol, exp_src)}

    status = worst(it["status"] for it in items.values())
    return {"video": a.mp4, "proj": a.proj, "style": style, "fps": fps, "definition": DEFINITION,
            "range": [ra, rb], "range_note": "半开区间 [from, to)，成片帧号", "video_offset": offset, "mode": mode,
            "status": status, "items": items, "rows": rows, "process_transitions": process, "notes": notes}


def report(res):
    it = res["items"]
    print("%s：%d 帧（%s）· 成片帧 %d–%d · 风格 %s" % (os.path.basename(res["video"]), it["frames"]["video_frames"], res["mode"],
                                                 res["range"][0], res["range"][1] - 1, res["style"]))
    print("符号：" + res["definition"])
    for n in res["notes"]:
        print("  ⓘ " + n)
    print("镜头     类型   段落        设计  实测  差   帧差   拍位     起音lead  最近重音lead(强度)  强重音:切点−目标")
    for r in res["rows"]:
        print("%-8s %-6s %-10s %5d  %5s %3s %6s  %-7s  %8s  %14s  %10s%s" % (
            r["shot"], r["type"], (r.get("section") or "")[:10], r["design"], r.get("measured", "—"),
            ("%+d" % r["dv"]) if "dv" in r else "", r.get("jump", ""), r.get("pos", ""),
            ("%+.2f" % r["onset_lead"]) if "onset_lead" in r else "—",
            ("%+.2f(%.2f)" % (r["accent_lead"], r["accent_s"])) if "accent_lead" in r else "—",
            (("%+.0f" % r["strong_d"]) + (" ✓" if r.get("strong_hit") else "")) if "strong_d" in r else "—",
            ("  " + r["note"]) if r.get("note") else ""))
    if res["process_transitions"]:
        print("过程转场（不测切点）：" + "，".join("%s %s@%d" % (r["shot"], r["type"], r["design"]) for r in res["process_transitions"]))
    print()
    mark = {"pass": "✓", "warn": "⚠️", "fail": "✗", "skipped": "–"}
    for k in ("frames", "cuts", "accents", "onsets", "black"):
        print("%s %-7s %s" % (mark[it[k]["status"]], k, it[k]["detail"]))
    for b in it["black"]["segments"]:
        print("    黑场 帧 %.0f–%.0f %s" % (b["from"], b["to"], ("= " + "、".join(b["matches"])) if b["expected"]
                                            else "⚠️ 意外：" + "，".join("%.0f–%.0f" % tuple(u) for u in b["unexpected"])))
    print("汇总：%s" % res["status"])


def main():
    ap = argparse.ArgumentParser(description="成片卡点核对 + 意外黑场")
    ap.add_argument("mp4")
    ap.add_argument("proj")
    ap.add_argument("--range", help="成片帧范围：a-b 闭区间 / a:b 半开")
    ap.add_argument("--style", help="覆盖 timeline 的 style（决定重音命中率的门槛）")
    ap.add_argument("--window", type=int, default=4, help="在设计帧 ±几帧里找实测切点")
    ap.add_argument("--band", default="1500-4000", help="起音频段 Hz")
    ap.add_argument("--rise", type=float, default=10, help="起音判定：比前 10 ms 均值高出几 dB")
    ap.add_argument("--floor", type=float, default=-40, help="起音判定：电平至少几 dBFS")
    ap.add_argument("--black-th", type=float, default=0.10, help="blackdetect pix_th")
    ap.add_argument("--black-d", type=float, default=0.10, help="blackdetect 最短秒数")
    ap.add_argument("--min-jump", type=float, default=10, help="帧差（0–255 灰度均值）低于它的切点看不出切，不计入统计")
    ap.add_argument("--json", default=None, help="结果另存 JSON")
    ap.add_argument("--no-queue", action="store_true", help="不排队")
    a = ap.parse_args()
    L.ensure_queued()
    try:
        res = analyze(a)
    except ValueError as e:
        sys.exit("✗ " + str(e))
    report(res)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    sys.exit(1 if res["status"] == "fail" else 0)


if __name__ == "__main__":
    main()
