#!/usr/bin/env python3
"""稳健测速 + 重音表:音乐 → beats.json(BPM、拍长、第一个小节头的相位、前几小节的拍点帧号、重音表 accents、起音密度)。

用法:
  python3 tempo.py <music.wav|mp3> [--from S] [--to S] [--trim_s S] [--out beats.json]
                   [--min-bpm 70] [--max-bpm 180] [--bpb 4] [--lead 1] [--fps 30]
                   [--downbeat S] [--bars 16] [--min-s 0.5] [--accents-only]

  --accents-only  只重测重音表和起音密度,拍网格(bpm / offset_s / 小节头)沿用 --out 指向的已有 beats.json。
                  用在:网格已经用 --downbeat 手动定好,或者按小节重剪(bar_edit.py)后的配乐拼接处拍网格拟合变差,
                  但重音要按剪好的这一版重新量。不加这个参数时,网格和重音一起重测(剪过的配乐照常整体重跑一遍即可)。
  --min-s         重音表只写强度百分位 s ≥ 这个值的(默认 0.5)。

  --from / --to   只拟合这一段(原曲秒数)。生成的配乐常常不按计划走(前奏没鼓、中间断音、结尾散拍),
                  只拿鼓点稳定的段落拟合,网格会自动外推到全曲。
  --trim_s        成片第 0 帧对应原曲第几秒(和 edit.json 的 audio.music.trim_s 一致)。按小节重剪过的
                  music.wav 一般是 0。
  --downbeat S    已知某个小节头在原曲第 S 秒(比如听出来的 drop 第一下),用它定小节相位,不再自动判断。
  --lead          切点比拍点提前几帧(写进 beats.json,timeline.py 用;默认 1)。

做法:
  1. 粗测:全频谱通量 + 低 / 中 / 高频段起音包络各做自相关,对每个候选 BPM 取 1–8 拍滞后处的自相关均值,
     几个频段归一化后相加,乘一个以 120 BPM 为中心的速度先验,取前几个峰,再补上它们的 ×2、×½、×4/3、×¾、×3/2、×⅔ 当候选。
  2. 细拟合:每个候选在 1.5–4 kHz(军鼓 / 拍手)和 30–150 Hz(底鼓)的起音峰上做加权最小二乘
     (共用拍长、两个频段各自相位),迭代收紧容差剔除离拍起音;候选之间按「拍点上的起音强度 ÷ 平均起音强度」× 先验选。
     旧的 grid.py / beat.py 在 示例项目的三首配乐上测成 96 / 83 / 99 / 164,都是没做这一步、被 3:4 / 2:3 的切分节奏骗了。
  3. 相位:用 1.5–4 kHz 的拟合相位(瞬态最尖);底鼓相对它晚多少 ms 一并报告(底鼓听感起音晚约 60 ms)。
     两者差出半拍左右,说明高频锁在了反拍的踩镲上(旋律 house 常见),改用底鼓相位。
  4. 小节头:每拍算一个「低频重音」(拍点附近低频能量比前 0.5 s 均值跳多少),取最大的 8 拍按拍位投票。
     段落开头、drop、落版重击都在小节头上;逐拍的起音强度不行(军鼓在 2、4 拍,会把小节头投到反拍)。
     最高票不到第二名的 1.5 倍时提示「小节头不明显」,这时听一下,用 --downbeat 指定。

产物 beats.json(默认写在音乐旁边的 beats.json;--out 指定):
  {"bpm", "beat_s", "offset_s", "beats_per_bar", "lead_frames", "fps", "src", "trim_s",
   "fit": {...拟合细节...}, "preview": [{"bar": 0, "frames": [第 1–4 拍的帧号]}, ...]}
  offset_s = 第 0 小节第 1 拍在「成片时间」里的秒数(= 原曲秒数 − trim_s;音乐从成片第 0 帧开始放)。
             取值在 [−0.1 拍, 1 小节 − 0.1 拍) 之间。
  第 n 拍(从 0 数)的起音时刻 = offset_s + n × beat_s 秒;preview 里是 round(秒 × fps),没减 lead。
  timeline.py 的画面切点 = round(秒 × fps) − lead_frames。

重音表(切点要落在重音上,不只是落在网格上;配乐每秒十几次起音时,8 分音符网格上处处「有起音」,只查网格等于没查):
  "accents": [{"t": 13.534, "s": 0.997, "kind": "hit", "eighth": 56, "dev_ms": -2}, ...]   按时间排序
    t       起音时刻,**音乐文件时间**(秒,和 --trim_s 无关;timeline.py 按 edit.json 的 audio.music.at / trim_s 换算到成片)
    s       强度在全曲起音里的百分位(0–1)。只写 s ≥ --min-s 的;s ≥ 0.7 算「强重音」(timeline.py 的命中率、snap 都用它)
    kind    kick = 底鼓频段(30–150 Hz)起音最突出;snare = 军鼓 / 拍手频段(1.5–4 kHz)最突出;
            hit = 宽频大冲击(段落砸点、drop、落版重击:总能量比前 2 s 的常态高出一截,且至少两个频段同时跳)
    eighth  离它最近的 8 分音符序号(成片时间网格,和 edit.json 的 {"eighth": n} 同一套编号;offset_s 之前为负)
    dev_ms  起音 − 那个 8 分音符(毫秒,正 = 起音在网格后面)
    timeline.py 里写 {"accent": n} 就是这张表的第 n 项(从 0 数)。
  "accent_stats": {"pool": 起音候选数, "pool_per_s", "listed": 写进表的个数, "strong_per_s": s ≥ 0.7 的每秒个数, "min_s", "strong_s": 0.7}
  "onset_density": {"mean": 有声段平均每秒起音数, "max": 1 秒窗内最多, "active_s": 有声段秒数, "method": "…"}
    和 bands.py 的 onsets/s 是同一套算法:2 kHz 以上频谱通量的峰,只数这一频段能量在全曲高位 30 dB 以内的时段
    (静音、只有铺底的段落不数,避免把底噪当起音)。

重音的算法(在 brp 三首配乐上校过:燃剪的暗黑失真贝斯、口号片的四拍 house、发布片的混合预告片风格):
  1. 候选:底鼓频段、军鼓 / 拍手频段各自找起音峰(能量 dB 的上升沿,±60 ms 内最大,上升 ≥ 1.5 dB)。
     同一下打击在两个频段各有一个峰(底鼓的「咔」在高频先到,低频能量上升沿晚 30–70 ms),70 ms 内合成一个,
     时刻取组里够强的峰中最早的那个。kind:组里底鼓频段的峰够突出就是 kick,否则 snare。
  2. 强度 = 加权总能量(低频 −6 dB、1.5–4 kHz +3 dB,近似人耳)在这一下的上升量 × 响度权重(比全曲高位每低 20 dB 权重减 1,
     安静段降权)。底鼓、军鼓让整体响度跳一下,踩镲 / 沙锤几乎不动总响度;只看单个频段的上升量,
     四拍 house 的反拍开镲会排到底鼓前面,失真贝斯铺满低频的燃剪里底鼓又会被淹掉(都实测过)。
  3. 宽频大冲击:加权总能量峰后 50 ms 比前 2 s 的 90 分位高 ≥ 7 dB、四个频段里至少两个各自高 ≥ 4 dB、
     响度在全曲高位 10 dB 以内,且是 ±1 s 内跳得最高的那个 → kind = hit,强度排最前。
  4. s = 强度在全部起音候选里的百分位。

依赖:numpy;ffmpeg(PATH 里,或环境变量 FFMPEG)。只解码音频,是轻任务。bands.py 会 import 本文件的起音 / 重音函数。
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys

import numpy as np

SR = 22050
NFFT = 1024
HOP = 110  # ≈ 5 ms
DT = HOP / SR
BANDS = {"low": (30, 150), "mid": (150, 1500), "hi": (1500, 4000), "air": (4000, 11000)}


def ffmpeg_bin():
    ff = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not ff:
        sys.exit("找不到 ffmpeg:装好放进 PATH,或设环境变量 FFMPEG=/path/to/ffmpeg")
    return ff


def load(path):
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def analyze(x):
    """分块 STFT。返回 (各频段能量 dB, 全频谱通量, 中频谱通量),步长 DT。"""
    nf = 1 + (len(x) - NFFT) // HOP
    win = np.hanning(NFFT).astype(np.float32)
    fr = np.fft.rfftfreq(NFFT, 1 / SR)
    masks = {k: (fr >= a) & (fr < b) for k, (a, b) in BANDS.items()}
    E = {k: np.empty(nf, np.float32) for k in BANDS}
    flux = np.zeros(nf, np.float32)
    midflux = np.zeros(nf, np.float32)
    prev = None
    blk = 2048
    for s in range(0, nf, blk):
        e = min(nf, s + blk)
        idx = np.arange(NFFT)[None, :] + HOP * np.arange(s, e)[:, None]
        M = np.abs(np.fft.rfft(x[idx] * win, axis=1)).astype(np.float32)
        P = M ** 2
        for k, m in masks.items():
            E[k][s:e] = 10 * np.log10(P[:, m].sum(1) + 1e-10)
        L = np.log1p(100 * M)
        L2 = np.vstack([(prev if prev is not None else L[0])[None], L])
        d = np.maximum(np.diff(L2, axis=0), 0)
        flux[s:e] = d.sum(1)
        midflux[s:e] = d[:, masks["mid"]].sum(1)
        prev = L[-1]
    return E, flux, midflux


def onset_from_db(e):
    """频段能量(dB)→ 起音强度:平滑后 2 步差分取正;比全曲峰值低 50 dB 的段落置零(静音里的噪声不算起音)。"""
    es = np.convolve(e, np.ones(3) / 3, "same")
    d = np.zeros_like(es)
    d[2:] = es[2:] - es[:-2]
    d = np.maximum(d, 0)
    d[es < es.max() - 50] = 0
    return d


def detrend(v, sec=1.0):
    k = max(3, int(sec / DT))
    v = v - np.convolve(v, np.ones(k) / k, "same")
    return np.maximum(v, 0)


def smooth(v, n=7):
    w = np.hanning(n + 2)[1:-1]
    return np.convolve(v, w / w.sum(), "same")


def acorr(v):
    v = v - v.mean()
    n = len(v)
    F = np.fft.rfft(v, 2 * n)
    ac = np.fft.irfft(F * np.conj(F))[:n]
    return ac / (ac[0] + 1e-12)


def comb(ac, bpms, kmax=8):
    lag = np.arange(len(ac))
    L = (60.0 / bpms / DT)[:, None] * np.arange(1, kmax + 1)[None, :]
    v = np.interp(L.ravel(), lag, ac, right=0).reshape(L.shape)
    return v.mean(1)


def prior(bpm, center=120.0, width=0.55):
    return np.exp(-0.5 * (np.log2(np.asarray(bpm) / center) / width) ** 2)


def maxfilt(v, w):
    m = v.copy()
    for s in range(1, w + 1):
        m[s:] = np.maximum(m[s:], v[:-s])
        m[:-s] = np.maximum(m[:-s], v[s:])
    return m


def peaks(env, t, pct=88, win=0.04):
    """起音峰:±win 内的局部最大,且高于分位数阈值。返回 (时刻, 强度)。"""
    nz = env[env > 0]
    if len(nz) < 10:
        return np.array([]), np.array([])
    thr = np.percentile(nz, pct)
    m = (env == maxfilt(env, max(1, int(win / DT)))) & (env > thr)
    return t[m], env[m]


def comb_phase(env_s, t, P, step=0.002):
    """给定拍长,扫相位,返回梳齿上包络均值最大的相位(0..P)。"""
    best, bphi = -1, 0.0
    t0, t1 = t[0], t[-1]
    for phi in np.arange(0, P, step):
        ts = np.arange(t0 + ((phi - t0) % P), t1, P)
        if len(ts) < 2:
            continue
        v = np.interp(ts, t, env_s).mean()
        if v > best:
            best, bphi = v, phi
    return bphi, best


def lsq_fit(P, phis, sets, tols=(0.2, 0.12, 0.07)):
    """共用拍长、各频段各自相位的加权最小二乘。sets: [(times, weights), ...]。返回 (P, phis, 统计)。"""
    phis = list(phis)
    stats = []
    for tol in tols:
        rows, ys, ws, used, stats = [], [], [], [], []
        for bi, (tt, ww) in enumerate(sets):
            if len(tt) == 0:
                stats.append((0, float("nan")))
                continue
            n = np.round((tt - phis[bi]) / P)
            dev = tt - phis[bi] - n * P
            m = np.abs(dev) < tol * P
            stats.append((int(m.sum()), float(np.sqrt(np.mean(dev[m] ** 2)) * 1000) if m.any() else float("nan")))
            if m.sum() < 6:
                continue
            used.append(bi)
            for ni, ti, wi in zip(n[m], tt[m], ww[m]):
                rows.append((ni, bi))
                ys.append(ti)
                ws.append(wi)
        if not used or len(rows) < 8:
            break
        cols = {b: i + 1 for i, b in enumerate(used)}
        A = np.zeros((len(rows), 1 + len(used)))
        for r, (ni, bi) in enumerate(rows):
            A[r, 0] = ni
            A[r, cols[bi]] = 1
        sw = np.sqrt(np.asarray(ws))
        sol, *_ = np.linalg.lstsq(A * sw[:, None], np.asarray(ys) * sw, rcond=None)
        P = float(sol[0])
        for b in used:
            phis[b] = float(sol[cols[b]])
    return P, phis, stats


def salience(env_s, t, P, phi=None):
    """拍点上的包络均值 ÷ 全段均值(给定相位;不给就扫最优相位)。"""
    if phi is None:
        phi, v = comb_phase(env_s, t, P, step=0.004)
    else:
        ts = np.arange(t[0] + ((phi - t[0]) % P), t[-1], P)
        v = np.interp(ts, t, env_s).mean()
    return v / (env_s.mean() + 1e-12), phi


def accent(e_db, beat_t, pre=0.5):
    """每拍的低频重音:拍点 −30…+70 ms 内,能量(dB)比前 pre 秒均值高出多少。段落开头、drop、重击都在小节头上。"""
    e = np.maximum(e_db, e_db.max() - 60)
    k = int(pre / DT)
    cs = np.concatenate([[0.0], np.cumsum(e, dtype=np.float64)])
    out = np.zeros(len(beat_t))
    for n, tb in enumerate(beat_t):
        i = int(round(tb / DT))
        i0, i1 = max(1, i - int(0.03 / DT)), min(len(e), i + int(0.07 / DT))
        if i1 <= i0:
            continue
        js = np.arange(i0, i1)
        hi_ = np.maximum(1, js - 2)
        lo = np.maximum(0, hi_ - k)
        prevm = (cs[hi_] - cs[lo]) / np.maximum(1, hi_ - lo)
        out[n] = (e[i0:i1] - prevm).max()
    return out


def wrap(d, P):
    return (d + P / 2) % P - P / 2


# ───────────────────────── 起音密度(bands.py 共用) ─────────────────────────

ONSET_METHOD = "2 kHz 以上频谱通量的峰(NFFT 1024 / hop 256),阈值 = 有声段中位数 × 4;只数该频段能量在全曲 95 分位 30 dB 以内的时段"


def onset_times(x, sr=SR):
    """密集起音(和 bands.py 的 onsets/s 同一算法)。返回 (起音时刻数组, 帧时刻, 有声掩码)。
    有声 = 2 kHz 以上能量在全曲 95 分位 30 dB 以内;静音、只有铺底的段落不数(否则底噪的小起伏会被数成每秒二三十个起音)。"""
    n, hop = 1024, 256
    nf = 1 + (len(x) - n) // hop
    if nf < 3:
        return np.array([]), np.array([]), np.array([], bool)
    win = np.hanning(n).astype(np.float32)
    f = np.fft.rfftfreq(n, 1 / sr)
    hs = np.empty(nf)
    for s in range(0, nf, 4096):
        e = min(nf, s + 4096)
        idx = np.arange(n)[None, :] + hop * np.arange(s, e)[:, None]
        S = np.abs(np.fft.rfft(x[idx] * win, axis=1))
        hs[s:e] = S[:, f >= 2000].sum(1)
    flux = np.maximum(0, np.diff(np.log(hs + 1e-9), prepend=np.log(hs[0] + 1e-9)))
    t = np.arange(nf) * hop / sr
    hdb = 20 * np.log10(hs + 1e-9)
    act = hdb > np.percentile(hdb, 95) - 30
    if not act.any():
        return np.array([]), t, act
    thr = np.median(flux[act]) * 4
    pk = np.zeros(nf, bool)
    pk[1:-1] = (flux[1:-1] > flux[:-2]) & (flux[1:-1] >= flux[2:]) & (flux[1:-1] > thr)
    pk &= act
    return t[pk], t, act


def onset_density(x, sr=SR):
    on, t, act = onset_times(x, sr)
    if not len(t):
        return {"mean": 0.0, "max": 0, "active_s": 0.0, "method": ONSET_METHOD}
    dur = t[-1]
    nb = max(1, int(math.ceil(dur)))
    cnt = np.histogram(on, bins=np.arange(nb + 1))[0]
    dt = t[1] - t[0] if len(t) > 1 else 0.0116
    act_s = float(act.sum() * dt)
    # 1 秒窗只统计大半是有声段的窗(片头片尾的半截窗不算最大值)
    full = np.array([act[(t >= i) & (t < i + 1)].mean() > 0.5 if ((t >= i) & (t < i + 1)).any() else False for i in range(nb)])
    mx = int(cnt[full].max()) if full.any() else int(cnt.max())
    return {"mean": round(len(on) / act_s, 1) if act_s > 0 else 0.0, "max": mx, "active_s": round(act_s, 1), "method": ONSET_METHOD}


# ───────────────────────── 重音表 ─────────────────────────

ACC_BANDS = (("low", "kick"), ("hi", "snare"))
STRONG_S = 0.7
HIT_JUMP = 7.0  # dB:加权总能量比前 2 s 的 90 分位高这么多,才算宽频大冲击(6 dB 时每个乐句头的镲片都算进来)


def _band_peaks(e_db, env, t):
    """一个频段的起音峰:±60 ms 内最大的上升沿。返回 (帧号, 上升 dB, 峰后能量 − 该频段 95 分位)。"""
    W = int(0.06 / DT)
    idx = np.where((env == maxfilt(env, W)) & (env > 0.3))[0]
    if not len(idx):
        return idx, np.array([]), np.array([])
    p95 = np.percentile(e_db, 95)
    pre0, pre1, post = int(0.20 / DT), int(0.03 / DT), int(0.06 / DT)
    rise = np.empty(len(idx))
    lvl = np.empty(len(idx))
    for k, i in enumerate(idx):
        a, b = max(0, i - pre0), max(1, i - pre1)
        if b <= a:
            b = a + 1
        pk = e_db[i:min(len(e_db), i + post)].max()
        rise[k] = pk - e_db[a:b].mean()
        lvl[k] = pk - p95
    keep = (rise >= 1.5) & (lvl > -40)
    return idx[keep], rise[keep], lvl[keep]


def hit_jumps(E, totw, merged):
    """每个候选的「宽频大冲击」跳变量(dB),不算冲击的是 0:加权总能量峰后 50 ms 的最大值比前 2 s 的 90 分位高 ≥ HIT_JUMP,
    四个频段里至少两个也各自高 ≥ 4 dB,且响度在全曲高位 10 dB 以内。(低频铺底很厚时只看总能量会漏掉高频整片砸下来的落版重击,
    所以用加权总能量。)"""
    loud = np.percentile(totw, 95)
    post, pre0, pre1 = int(0.05 / DT), int(2.0 / DT), int(0.05 / DT)

    def jump(e, i):
        after = e[i:min(len(e), i + post)].max()
        a, b = max(0, i - pre0), max(0, i - pre1)
        before = np.percentile(e[a:b], 90) if b - a >= 4 else e.min()
        return after - before, after

    out = np.zeros(len(merged))
    for k, c in enumerate(merged):
        j, after = jump(totw, c["i"])
        nb = sum(1 for b in ("low", "mid", "hi", "air") if jump(E[b], c["i"])[0] >= 4)
        if j >= HIT_JUMP and nb >= 2 and after > loud - 10:
            out[k] = j
    return out


def detect_accents(E):
    """E:analyze() 的各频段能量(dB,步长 DT)。返回 (候选列表[{t, strength, kind}], 候选总数)。
    强度见文件头「重音的算法」;s(百分位)由调用方按 strength 排。"""
    n = len(E["low"])
    t = np.arange(n) * DT
    cands = []
    for band, kind in ACC_BANDS:
        e = E[band]
        idx, rise, lvl = _band_peaks(e, onset_from_db(e), t)
        if not len(idx):
            continue
        ti = t[idx]
        g75 = np.percentile(rise, 75)
        loc = np.maximum(np.array([np.percentile(rise[np.abs(ti - tt) <= 3.0], 75) for tt in ti]), 0.5 * g75)
        w = np.clip(1 + lvl / 20.0, 0.05, 1.0)
        # 频段内的突出度(全曲 × 局部 的几何平均),只用来定 kind 和时刻
        st = np.sqrt(rise / max(g75, 1e-6) * rise / np.maximum(loc, 1e-6)) * w
        cands += [{"i": int(i), "t": float(t[i]), "band_s": float(s_), "kind": kind} for i, s_ in zip(idx, st)]
    cands.sort(key=lambda c: c["t"])
    # 同一下打击在两个频段各有一个峰(底鼓的「咔」在高频先到,低频能量的上升沿晚 30–70 ms):70 ms 内合成一个
    groups = []
    for c in cands:
        if groups and c["t"] - groups[-1][0]["t"] <= 0.07 and c["kind"] not in {g["kind"] for g in groups[-1]}:
            groups[-1].append(c)
        else:
            groups.append([c])
    if not groups:
        return [], 0
    # 强度 = 加权总能量的上升量(低频 −6 dB、1.5–4 kHz +3 dB,近似人耳):底鼓、军鼓让整体响度跳一下,踩镲几乎不动总响度
    wts = {"low": -6.0, "mid": 0.0, "hi": 3.0, "air": 0.0}
    totw = 10 * np.log10(sum(10 ** ((E[k] + wts.get(k, 0.0)) / 10) for k in E) + 1e-12)
    loudw = np.percentile(totw, 95)
    pre0, pre1, post = int(0.20 / DT), int(0.03 / DT), int(0.06 / DT)
    merged = []
    for g in groups:
        top_ = max(g, key=lambda c: c["band_s"])
        low = [c for c in g if c["kind"] == "kick"]
        kind = "kick" if low and low[0]["band_s"] >= 0.6 * top_["band_s"] else top_["kind"]
        first = min((c for c in g if c["band_s"] >= 0.5 * top_["band_s"]), key=lambda c: c["t"])
        i0, i1 = min(c["i"] for c in g), max(c["i"] for c in g)
        a, b = max(0, i0 - pre0), max(1, i0 - pre1)
        if b <= a:
            b = a + 1
        after = totw[i0:min(n, i1 + post)].max()
        rise = after - totw[a:b].mean()
        w = float(np.clip(1 + (after - loudw) / 20.0, 0.05, 1.0))
        merged.append({"i": first["i"], "t": first["t"], "strength": max(0.0, float(rise)) * w, "kind": kind})
    if not merged:
        return [], 0
    jumps = hit_jumps(E, totw, merged)
    top = max(c["strength"] for c in merged)
    tt = np.array([c["t"] for c in merged])
    for k, c in enumerate(merged):
        if jumps[k] > 0 and jumps[k] >= jumps[np.abs(tt - c["t"]) <= 1.0].max():
            c["kind"] = "hit"
            c["strength"] = top * 1.01 + jumps[k] / 100.0
    return merged, len(merged)


def ranked_accents(E):
    """→ (候选列表, 每个候选的百分位 s 数组)。bands.py 也用它。"""
    cands, _ = detect_accents(E)
    if not cands:
        return [], np.array([])
    st = np.array([c["strength"] for c in cands])
    order = np.argsort(st, kind="stable")
    s = np.empty(len(st))
    s[order] = (np.arange(len(st)) + 0.5) / len(st)
    return cands, s


def accent_table(x, E, P, off_film, trim, min_s=0.5):
    """→ (accents 列表, accent_stats)。P / off_film / trim 用来标最近的 8 分音符(成片时间网格)。"""
    cands, s = ranked_accents(E)
    dur = len(x) / SR
    if not cands:
        return [], {"pool": 0, "pool_per_s": 0.0, "listed": 0, "strong_per_s": 0.0, "min_s": min_s, "strong_s": STRONG_S}
    out = []
    half = P / 2
    for c, sv in zip(cands, s):
        if sv < min_s:
            continue
        tf = c["t"] - trim
        k = int(round((tf - off_film) / half))
        out.append({"t": round(c["t"], 3), "s": round(float(sv), 3), "kind": c["kind"], "eighth": k,
                    "dev_ms": int(round((tf - (off_film + k * half)) * 1000))})
    stats = {"pool": len(cands), "pool_per_s": round(len(cands) / dur, 2), "listed": len(out),
             "strong_per_s": round(float((s >= STRONG_S).sum()) / dur, 2), "min_s": min_s, "strong_s": STRONG_S}
    return out, stats


def eighth_label(k, bpb):
    """8 分音符序号 → 「小节.拍」(从 1.1 起,「+」= 后半拍)。"""
    bar, r = divmod(int(k), 2 * bpb)
    return f"{bar + 1}.{r // 2 + 1}" + ("+" if r % 2 else "")


def print_accents(acc, stats, dens, off_film, P, bpb, trim, fps, k=12):
    print(f"起音密度:有声段平均 {dens['mean']}/s,1 秒窗最多 {dens['max']}(有声段 {dens['active_s']} s)"
          + ("  ⚠️ > 15/s:鼓组很密,按 4 分音符和强重音切,8 分音符留给动作内部" if dens["mean"] > 15 else ""))
    strong = [a for a in acc if a["s"] >= STRONG_S]
    print(f"重音表:{len(acc)} 个(s ≥ {stats['min_s']}),其中强重音 s ≥ {STRONG_S} {len(strong)} 个 = {stats['strong_per_s']}/s;"
          f"kick {sum(a['kind'] == 'kick' for a in acc)} / snare {sum(a['kind'] == 'snare' for a in acc)} / hit {sum(a['kind'] == 'hit' for a in acc)}")
    idx = {id(a): i for i, a in enumerate(acc)}
    top = sorted(sorted(acc, key=lambda a: -a["s"])[:k], key=lambda a: a["t"])
    if top:
        print(f"  最强的 {len(top)} 个(序号 = edit.json 里 {{\"accent\": n}} 的 n;帧 = 成片帧,未减 lead):")
    for a in top:
        tf = a["t"] - trim
        print(f"    #{idx[id(a)]:<4d} {a['t']:7.3f}s  帧 {tf * fps:7.1f}  {eighth_label(a['eighth'], bpb):7s} 8分#{a['eighth']:<4d} {a['dev_ms']:+4d}ms  s={a['s']:.3f}  {a['kind']}")


def accents_only(o):
    path = o.out or os.path.join(os.path.dirname(os.path.abspath(o.audio)), "beats.json")
    if not os.path.exists(path):
        sys.exit(f"--accents-only 要沿用已有的拍网格,但 {path} 不存在(先不加 --accents-only 跑一遍)")
    bj = json.load(open(path, encoding="utf-8"))
    x = load(o.audio)
    E, _, _ = analyze(x)
    P, off, trim = float(bj["beat_s"]), float(bj["offset_s"]), float(bj.get("trim_s", 0) or 0)
    acc, stats = accent_table(x, E, P, off, trim, o.min_s)
    dens = onset_density(x)
    if os.path.abspath(bj.get("src", "")) != os.path.abspath(o.audio):
        print(f"注意:beats.json 的 src 是 {bj.get('src')},这次量的是 {o.audio};拍网格沿用原值,重音按这次的音频量")
    bj.update({"accents": acc, "accent_stats": stats, "onset_density": dens, "accents_src": o.audio})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bj, f, ensure_ascii=False, indent=1)
    print_accents(acc, stats, dens, off, P, int(bj.get("beats_per_bar", 4)), trim, float(bj.get("fps", 30)))
    print(f"✓ {path}(只更新了 accents / accent_stats / onset_density)")


def main():
    ap = argparse.ArgumentParser(description="稳健测速 → beats.json")
    ap.add_argument("audio")
    ap.add_argument("--from", dest="a", type=float, default=None, help="拟合区间起点(原曲秒)")
    ap.add_argument("--to", dest="b", type=float, default=None, help="拟合区间终点(原曲秒)")
    ap.add_argument("--trim_s", "--trim", dest="trim", type=float, default=0.0, help="成片第 0 帧对应原曲第几秒")
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-bpm", type=float, default=70)
    ap.add_argument("--max-bpm", type=float, default=180)
    ap.add_argument("--bpb", type=int, default=4, help="每小节几拍")
    ap.add_argument("--lead", type=int, default=1, help="切点比拍点提前几帧(写进 beats.json)")
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--downbeat", type=float, default=None, help="已知一个小节头的原曲秒数")
    ap.add_argument("--bars", type=int, default=16, help="preview 列前几小节")
    ap.add_argument("--min-s", type=float, default=0.5, help="重音表只写强度百分位 ≥ 这个值的")
    ap.add_argument("--accents-only", action="store_true", help="只重测重音表和起音密度,拍网格沿用已有 beats.json")
    o = ap.parse_args()
    if o.accents_only:
        return accents_only(o)

    x = load(o.audio)
    dur = len(x) / SR
    if dur < 4:
        sys.exit("音频太短(< 4 s),测不了")
    E, flux, midflux = analyze(x)
    t = np.arange(len(flux)) * DT
    a = max(0.0, o.a if o.a is not None else 0.0)
    b = min(dur, o.b if o.b is not None else dur)
    if b - a < 4:
        sys.exit("拟合区间太短(< 4 s)")
    sel = (t >= a) & (t < b)
    tw = t[sel]

    env = {
        "flux": detrend(flux),
        "low": onset_from_db(E["low"]),
        "mid": onset_from_db(E["mid"]),
        "hi": onset_from_db(E["hi"]),
    }
    envs = {k: smooth(v) for k, v in env.items()}

    # ── 1. 粗测 ──
    bpms = np.arange(o.min_bpm * 0.5, o.max_bpm * 2 + 0.01, 0.05)
    total = np.zeros(len(bpms))
    for k, w in (("flux", 1.0), ("low", 1.0), ("hi", 1.0), ("mid", 0.5)):
        s = comb(acorr(envs[k][sel]), bpms)
        rng = s.max() - s.min()
        if rng > 0:
            total += w * (s - s.min()) / rng
    inr = (bpms >= o.min_bpm) & (bpms <= o.max_bpm)
    score = total * prior(bpms)
    loc = [i for i in range(1, len(bpms) - 1) if inr[i] and score[i] >= score[i - 1] and score[i] >= score[i + 1]]
    loc.sort(key=lambda i: -score[i])
    cands = []
    for i in loc:
        if all(abs(bpms[i] / c - 1) > 0.02 for c in cands):
            cands.append(float(bpms[i]))
        if len(cands) >= 5:
            break
    for c in list(cands[:2]):
        for r in (2, 0.5, 4 / 3, 0.75, 1.5, 2 / 3):
            v = c * r
            if o.min_bpm <= v <= o.max_bpm and all(abs(v / q - 1) > 0.02 for q in cands):
                cands.append(v)

    # ── 2. 细拟合 ──
    pk = {k: peaks(env[k][sel], tw) for k in ("hi", "low", "flux")}
    results = []
    for c in cands:
        P = 60.0 / c
        phis = [comb_phase(envs[k][sel], tw, P)[0] for k in ("hi", "low", "flux")]
        P2, phis2, st = lsq_fit(P, phis, [pk["hi"], pk["low"], pk["flux"]])
        if not (0.5 * P < P2 < 1.5 * P):
            P2, phis2 = P, phis
        sal, _ = salience(envs["flux"][sel], tw, P2)
        bpm2 = 60.0 / P2
        results.append({"bpm": bpm2, "P": P2, "phis": phis2, "stats": st, "sal": float(sal),
                        "prior": float(prior(bpm2)), "score": float(sal * prior(bpm2)), "coarse": c})
    results.sort(key=lambda r: -r["score"])
    best = results[0]
    P = best["P"]
    phi_hi, phi_low, phi_flux = best["phis"]
    n_hi, rms_hi = best["stats"][0] if best["stats"] else (0, float("nan"))
    n_low = best["stats"][1][0] if len(best["stats"]) > 1 else 0
    d_low = wrap(phi_low - phi_hi, P)  # 底鼓相对高频的偏移;正常是 0 到 +60 ms 左右
    if n_hi >= 8 and -0.08 * P <= d_low <= 0.2 * P:
        phi, phase_src = phi_hi, "1.5–4 kHz"
    elif n_low >= 8:
        phi = phi_low
        phase_src = "30–150 Hz(高频起音落在反拍上,改用底鼓相位)" if n_hi >= 8 else "30–150 Hz"
    else:
        phi, phase_src = phi_flux, "全频谱通量"
    low_delay = d_low * 1000

    # ── 3. 小节头 ──
    phi0 = phi % P
    if phi0 > 0.9 * P:
        phi0 -= P
    nb = int((t[-1] - phi0) / P) + 1
    beat_t = phi0 + np.arange(nb) * P
    acc = accent(E["low"], beat_t)
    inwin = np.where((beat_t >= a - 0.05) & (beat_t < b))[0]
    top = sorted(inwin, key=lambda n: -acc[n])[:8]
    cls = np.zeros(o.bpb)
    for n in top:
        cls[n % o.bpb] += max(0.0, acc[n])
    if o.downbeat is not None:
        r = int(round((o.downbeat - phi0) / P)) % o.bpb
        bar_src = f"--downbeat {o.downbeat}"
        clear = True
    else:
        r = int(np.argmax(cls))
        srt = np.sort(cls)[::-1]
        clear = bool(srt[0] >= 1.5 * srt[1])
        bar_src = "低频重音投票(每拍低频能量比前 0.5 s 跳多少,取最大的 8 拍按拍位加总)"
    bar_s = P * o.bpb
    first_db = phi0 + r * P - o.trim
    off = first_db % bar_s
    if off >= bar_s - 0.1 * P:
        off -= bar_s
    bpm = 60.0 / P

    preview = []
    for bar in range(o.bars):
        fr = [int(round((off + (bar * o.bpb + k) * P) * o.fps)) for k in range(o.bpb)]
        preview.append({"bar": bar, "frames": fr})
    acc, acc_stats = accent_table(x, E, P, off, o.trim, o.min_s)
    dens = onset_density(x)
    out = {
        "bpm": round(bpm, 3), "beat_s": round(P, 6), "offset_s": round(off, 4),
        "beats_per_bar": o.bpb, "lead_frames": o.lead, "fps": o.fps,
        "src": o.audio, "trim_s": o.trim,
        "beat_frames": round(P * o.fps, 4), "bar_frames": round(bar_s * o.fps, 4),
        "fit": {
            "from": round(a, 3), "to": round(b, 3), "phase_from": phase_src,
            "inliers_hi": n_hi, "rms_ms_hi": None if np.isnan(rms_hi) else round(rms_hi, 1),
            "inliers_low": n_low,
            "low_delay_ms": round(float(low_delay), 1),
            "downbeat_from": bar_src, "downbeat_scores": [round(float(v), 3) for v in cls],
            "downbeat_clear": bool(clear),
            "candidates": [{"bpm": round(r_["bpm"], 2), "salience": round(r_["sal"], 3), "prior": round(r_["prior"], 3),
                            "score": round(r_["score"], 3)} for r_ in results[:6]],
        },
        "preview": preview,
        "onset_density": dens,
        "accent_stats": acc_stats,
        "accents": acc,
    }
    path = o.out or os.path.join(os.path.dirname(os.path.abspath(o.audio)), "beats.json")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"音频 {dur:.2f}s  拟合区间 {a:.2f}–{b:.2f}s")
    print(f"BPM {bpm:.3f}  拍长 {P:.5f}s = {P * o.fps:.3f} 帧  小节 {bar_s * o.fps:.3f} 帧")
    print(f"相位来自 {phase_src}")
    print(f"  1.5–4 kHz {n_hi} 个起音在拍上(均方根偏差 {rms_hi:.1f} ms);30–150 Hz {n_low} 个,底鼓起音比高频{'晚' if low_delay >= 0 else '早'} {abs(low_delay):.0f} ms")
    print("候选:" + "  ".join(f"{r_['bpm']:.2f}(显著 {r_['sal']:.2f} × 先验 {r_['prior']:.2f})" for r_ in results[:5]))
    print(f"小节头:{bar_src};各拍位分数 " + " / ".join(f"{v:.2f}" for v in cls) + f" → 第 {r + 1} 拍位"
          + ("" if clear else "  ⚠️ 小节头不明显,建议听一下后用 --downbeat 指定"))
    print(f"offset_s = {off:.4f}(成片时间,trim_s={o.trim})  第 0 小节头 = 帧 {off * o.fps:.2f}")
    for p in preview[:4]:
        print(f"  小节 {p['bar']:2d}: {p['frames']}")
    print_accents(acc, acc_stats, dens, off, P, o.bpb, o.trim, o.fps)
    print(f"✓ {path}")


if __name__ == "__main__":
    main()
