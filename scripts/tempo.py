#!/usr/bin/env python3
"""稳健测速:音乐 → beats.json(BPM、拍长、第一个小节头的相位、前几小节的拍点帧号)。

用法:
  python3 tempo.py <music.wav|mp3> [--from S] [--to S] [--trim_s S] [--out beats.json]
                   [--min-bpm 70] [--max-bpm 180] [--bpb 4] [--lead 1] [--fps 30]
                   [--downbeat S] [--bars 16]

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

依赖:numpy;ffmpeg(PATH 里,或环境变量 FFMPEG)。只解码音频,是轻任务。
"""
import argparse
import json
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
    o = ap.parse_args()

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
    print(f"✓ {path}")


if __name__ == "__main__":
    main()
