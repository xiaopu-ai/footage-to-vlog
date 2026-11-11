#!/usr/bin/env python3
"""音效预处理:切掉头部静音(起音对准第 0 帧,timeline 里写的帧就是声音响的帧)、峰值统一到 −3 dBFS、转 48 kHz wav;
可选裁长度、淡出。生成的 / 下载的音效头上常有几十到几百毫秒空白,不切的话卡点会整体晚。

用法:
  python3 sfx_prep.py <in…> --out <proj>/public/audio/sfx [--peak -3] [--thresh -40] [--pre 5]
                       [--max-len S] [--fade-out S] [--mono] [--suffix ""]
  --thresh  起音判定:比文件自身峰值低多少 dB 以内算「有声音」(默认 −40 dB,且不低于 −60 dBFS)
  --pre     起音前保留几毫秒(保住瞬态的起始,默认 5 ms)
  --max-len 只留前 S 秒;超出时末尾自动加 10 ms 淡出防爆音(--fade-out 另给淡出长度)
  --mono    转单声道(默认保留原声道数,最多 2)

产物:<out>/<原文件名去扩展><suffix>.wav(48 kHz 24-bit PCM),逐个打印切掉多少、长度、增益。
峰值是采样峰值(不是真峰值),−3 dBFS 给混音留了余量。
依赖:numpy;ffmpeg(PATH 里,或环境变量 FFMPEG)。
"""
import argparse
import os
import shutil
import subprocess
import sys
import wave

import numpy as np

SR = 48000


def main():
    ap = argparse.ArgumentParser(description="音效:切头部静音 + 峰值归一 + 48k wav")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--peak", type=float, default=-3.0)
    ap.add_argument("--thresh", type=float, default=-40.0)
    ap.add_argument("--pre", type=float, default=5.0, help="起音前保留毫秒")
    ap.add_argument("--max-len", type=float, default=None)
    ap.add_argument("--fade-out", type=float, default=None)
    ap.add_argument("--mono", action="store_true")
    ap.add_argument("--suffix", default="")
    o = ap.parse_args()
    ff = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not ff:
        sys.exit("找不到 ffmpeg:装好放进 PATH,或设环境变量 FFMPEG")
    os.makedirs(o.out, exist_ok=True)
    for src in o.inputs:
        info = subprocess.run([ff, "-hide_banner", "-i", src], capture_output=True, text=True).stderr
        ch = 1 if (o.mono or " mono" in info) else 2
        raw = subprocess.run([ff, "-v", "error", "-i", src, "-vn", "-ac", str(ch), "-ar", str(SR), "-f", "f32le", "-"],
                             capture_output=True, check=True).stdout
        x = np.frombuffer(raw, np.float32).reshape(-1, ch).astype(np.float64)
        if len(x) == 0:
            print(f"✗ {src}:没有音频")
            continue
        env = np.abs(x).max(1)
        peak = env.max()
        if peak <= 0:
            print(f"✗ {src}:全是静音")
            continue
        thr = max(peak * 10 ** (o.thresh / 20), 10 ** (-60 / 20))
        first = int(np.argmax(env > thr))
        cut = max(0, first - int(o.pre / 1000 * SR))
        x = x[cut:]
        fade = o.fade_out
        if o.max_len and len(x) > o.max_len * SR:
            x = x[: int(o.max_len * SR)]
            fade = fade or 0.01
        if fade:
            nf = min(len(x), int(fade * SR))
            x[len(x) - nf:] *= np.linspace(1, 0, nf)[:, None]
        gain_db = o.peak - 20 * np.log10(np.abs(x).max() + 1e-12)
        x *= 10 ** (gain_db / 20)
        x = np.clip(x, -1, 1 - 2 ** -23)
        pcm = (x * (2 ** 23)).astype(np.int32)
        b24 = pcm.astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3].tobytes()  # 每个 int32 取低 3 字节
        stem = os.path.splitext(os.path.basename(src))[0] + o.suffix
        dst = os.path.join(o.out, stem + ".wav")
        with wave.open(dst, "wb") as w:
            w.setnchannels(ch)
            w.setsampwidth(3)
            w.setframerate(SR)
            w.writeframes(b24)
        print(f"✓ {dst}:切掉头部 {cut / SR * 1000:.0f} ms,长 {len(x) / SR:.2f} s,{ch} 声道,增益 {gain_db:+.1f} dB → 峰值 {o.peak:g} dBFS")


if __name__ == "__main__":
    main()
