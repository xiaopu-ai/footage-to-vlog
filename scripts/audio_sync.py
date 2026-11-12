#!/usr/bin/env python3
"""audio_sync.py · 音画对齐：成片音轨和参考混音（mix.py 的规范混音）整体差了多少毫秒。

为什么要它：「切点附近找最近的起音」证明不了封装后的音轨没整体偏移——鼓点很密时，最近匹配会跳到旁边另一个起音
（实测：直出音轨晚 42.6 ms，起音命中率照样 100%）。这里直接拿整段波形做互相关。

用法：
  python3 scripts/audio_sync.py <成片.mp4> <参考.wav> [--max-ms 5] [--min-corr 0.9] [--window-ms 300] [--json out.json]
  例：python3 scripts/mix.py <proj> --range 0-299 --out /tmp/ref.wav
      python3 scripts/audio_sync.py out/preview.mp4 /tmp/ref.wav
  参考必须和成片覆盖同一段帧（整片对整片、预演对同范围的混音），都从各自第 0 个样本算起。

做法：
  1. ffmpeg 把两边都解码成单声道 float（成片音轨按容器的时间戳和编辑列表解码，AAC 前置延迟会被正确扣掉）；
  2. 粗测：降采样到 8 kHz，FFT 互相关，在 ±window 内找主峰；
  3. 细测：48 kHz 下在粗测结果 ±0.25 ms 内逐样本算点积，再做抛物线插值；
  4. 报告：时移（ms）、相关系数（主峰处两段波形的皮尔逊相关）、次峰/主峰比（> 0.95 说明有周期性歧义）、
     搜索窗、测量精度、两边时长。
符号：时移 > 0 = 成片里的音频比参考晚（声音落后画面）；< 0 = 声音抢在画面前面。
退出码：|时移| ≤ --max-ms 且相关 ≥ --min-corr → 0；否则 1；没有音轨 / 参考全静音 → 2。

检查边界：只测「整段音轨的整体时移」。测不出局部漂移（变速 / 丢样本会让相关系数下降，但不会定位到哪一段）、
测不出混音内容对不对（那是 mix.py 和用户试听的事）。限幅、响度标准化、AAC 编码都会让相关系数略低于 1，正常在 0.98 以上。
依赖：numpy；ffmpeg（PATH 里，或环境变量 FFMPEG）。兼容 Python 3.9。
也可以当库用：from audio_sync import measure。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

import numpy as np

COARSE_SR = 8000
FINE_SR = 48000


def _ff():
    ff = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not ff:
        sys.exit("✗ 找不到 ffmpeg：装好后放进 PATH，或设环境变量 FFMPEG")
    return ff


def decode_mono(path, sr):
    """解码第一条音轨为单声道 float32；没有音轨返回 None。"""
    r = subprocess.run([_ff(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-map", "0:a:0?",
                        "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError("解码失败：%s\n%s" % (path, r.stderr.decode("utf-8", "replace")[-400:]))
    x = np.frombuffer(r.stdout, np.float32).astype(np.float64)
    return x if len(x) else None


def _xcorr_window(x, y, w):
    """c[k] = Σ x[n+k]·y[n]，k ∈ [−w, w]（x 相对 y 晚 k 个样本时最大）。"""
    n = len(x) + len(y)
    nfft = 1 << int(np.ceil(np.log2(n)))
    c = np.fft.irfft(np.fft.rfft(x, nfft) * np.conj(np.fft.rfft(y, nfft)), nfft)
    lags = np.arange(-w, w + 1)
    return lags, c[lags % nfft]


def _dot_at(x, y, k):
    """Σ x[n+k]·y[n] 在重叠部分上"""
    if k >= 0:
        m = min(len(x) - k, len(y))
        return float(np.dot(x[k:k + m], y[:m])) if m > 0 else 0.0
    m = min(len(x), len(y) + k)
    return float(np.dot(x[:m], y[-k:-k + m])) if m > 0 else 0.0


def _pearson_at(x, y, k):
    if k >= 0:
        m = min(len(x) - k, len(y))
        a, b = x[k:k + m], y[:m]
    else:
        m = min(len(x), len(y) + k)
        a, b = x[:m], y[-k:-k + m]
    if m < 2:
        return 0.0
    a = a - a.mean()
    b = b - b.mean()
    den = np.sqrt(np.dot(a, a) * np.dot(b, b))
    return float(np.dot(a, b) / den) if den > 0 else 0.0


def measure(video, ref, max_ms=5.0, min_corr=0.9, window_ms=300.0):
    """返回结果 dict：status pass / fail / error，delay_ms（> 0 = 成片音频比参考晚），corr，… """
    res = {"video": str(video), "reference": str(ref), "max_ms": max_ms, "min_corr": min_corr,
           "definition": "delay_ms > 0：成片里的音频比参考混音晚（声音落后画面）；< 0：声音抢在画面前面",
           "search_window_ms": window_ms, "coarse_sr": COARSE_SR, "fine_sr": FINE_SR,
           "resolution_ms": round(1000.0 / FINE_SR, 4),
           "method": "8 kHz FFT 互相关找主峰 → 48 kHz 逐样本细搜 ±0.25 ms → 抛物线插值"}
    xc, yc = decode_mono(video, COARSE_SR), decode_mono(ref, COARSE_SR)
    if xc is None:
        res.update(status="error", reason="成片没有音轨")
        return res
    if yc is None or not np.any(yc):
        res.update(status="error", reason="参考混音没有声音，无从比较")
        return res
    res["video_audio_s"] = round(len(xc) / COARSE_SR, 4)
    res["reference_s"] = round(len(yc) / COARSE_SR, 4)
    if not np.any(xc):
        res.update(status="fail", reason="成片音轨全是静音")
        return res
    w = int(round(window_ms / 1000.0 * COARSE_SR))
    lags, c = _xcorr_window(xc, yc, w)
    norm = np.sqrt(np.dot(xc, xc) * np.dot(yc, yc))
    cn = c / norm if norm > 0 else c
    i = int(np.argmax(cn))
    coarse = int(lags[i])
    away = np.abs(lags - coarse) > int(0.002 * COARSE_SR)
    second = float(cn[away].max()) if np.any(away) else 0.0
    res["peak_ratio"] = round(second / cn[i], 4) if cn[i] > 0 else None
    res["coarse_ms"] = round(coarse * 1000.0 / COARSE_SR, 3)

    # 细测：48 kHz
    xf, yf = decode_mono(video, FINE_SR), decode_mono(ref, FINE_SR)
    ratio = FINE_SR // COARSE_SR
    centre = coarse * ratio
    span = int(round(0.00025 * FINE_SR)) + ratio
    ks = list(range(centre - span, centre + span + 1))
    vals = [_dot_at(xf, yf, k) for k in ks]
    j = int(np.argmax(vals))
    k = ks[j]
    frac = 0.0
    if 0 < j < len(vals) - 1:
        y0, y1, y2 = vals[j - 1], vals[j], vals[j + 1]
        den = y0 - 2 * y1 + y2
        if den < 0:
            frac = float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))
    delay_ms = (k + frac) * 1000.0 / FINE_SR
    corr = _pearson_at(xf, yf, k)
    res.update(delay_ms=round(delay_ms, 3), delay_frames_30fps=round(delay_ms * 30 / 1000.0, 3), corr=round(corr, 5),
               edge_of_window=abs(coarse) >= w - 1, length_diff_ms=round((len(xf) - len(yf)) * 1000.0 / FINE_SR, 2))
    problems = []
    if res["edge_of_window"]:
        problems.append("主峰落在搜索窗边上（真实偏移可能更大，加大 --window-ms 再测）")
    if abs(delay_ms) > max_ms:
        problems.append("|时移| %.2f ms > %.2g ms" % (abs(delay_ms), max_ms))
    if corr < min_corr:
        problems.append("相关系数 %.3f < %.2g（音轨和参考不是同一个混音，或者有局部漂移）" % (corr, min_corr))
    res["status"] = "fail" if problems else "pass"
    res["problems"] = problems
    if res["peak_ratio"] is not None and res["peak_ratio"] > 0.95:
        res["warning"] = "次峰和主峰几乎一样高（%.3f）：音乐周期性强，结果可能落在相邻周期上" % res["peak_ratio"]
    return res


def report(res):
    print("音画对齐：%s  vs  参考 %s" % (os.path.basename(res["video"]), os.path.basename(res["reference"])))
    if res["status"] == "error":
        print("  ✗ %s" % res["reason"])
        return
    if "delay_ms" not in res:
        print("  ✗ %s" % res.get("reason", ""))
        return
    d = res["delay_ms"]
    side = "音频比参考晚（声音落后画面）" if d > 0.05 else ("音频比参考早（声音抢在画面前）" if d < -0.05 else "对齐")
    print("  时移 %+.3f ms（≈ %+.2f 帧 @30fps），%s" % (d, res["delay_frames_30fps"], side))
    print("  相关系数 %.4f；次峰/主峰 %s" % (res["corr"], res["peak_ratio"]))
    print("  搜索窗 ±%g ms；%s；测量精度约 %.3f ms" % (res["search_window_ms"], res["method"], res["resolution_ms"]))
    print("  时长：成片音轨 %.3f s，参考 %.3f s（细测下差 %+.1f ms）" % (res["video_audio_s"], res["reference_s"], res["length_diff_ms"]))
    if res.get("warning"):
        print("  ⚠️ " + res["warning"])
    if res["status"] == "pass":
        print("  ✓ 通过（|时移| ≤ %g ms，相关 ≥ %g）" % (res["max_ms"], res["min_corr"]))
    else:
        for p in res["problems"]:
            print("  ✗ " + p)


def main():
    ap = argparse.ArgumentParser(description="成片音轨 vs 参考混音的整体时移")
    ap.add_argument("video")
    ap.add_argument("reference")
    ap.add_argument("--max-ms", type=float, default=5.0, help="允许的最大时移（默认 5 ms）")
    ap.add_argument("--min-corr", type=float, default=0.9, help="主峰处相关系数下限（默认 0.9）")
    ap.add_argument("--window-ms", type=float, default=300.0, help="搜索窗 ±毫秒（默认 300）")
    ap.add_argument("--json", help="结果另存 JSON")
    a = ap.parse_args()
    try:
        res = measure(a.video, a.reference, a.max_ms, a.min_corr, a.window_ms)
    except RuntimeError as e:
        sys.exit("✗ " + str(e))
    report(res)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    sys.exit({"pass": 0, "fail": 1}.get(res["status"], 2))


if __name__ == "__main__":
    main()
