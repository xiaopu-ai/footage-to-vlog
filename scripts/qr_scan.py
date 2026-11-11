#!/usr/bin/env python3
"""发布前扫二维码:成片 / 封面里能被扫出来的二维码 = 站外引流。
manus-2-cue 2026-10-01 发布后被抖音判「画面」违规、限制自己可见,原因就是 Cue 官方实拍里的二维码(能扫出 https://manus.im)。

用法:
  python3 scripts/qr_scan.py <视频或图片...> [--fps 5]
  例:python3 scripts/qr_scan.py <proj>/out/<成片>.mp4 <proj>/out/cover-*.png
  打码要的跟踪框:python3 scripts/qr_scan.py <没打码的渲染.mp4> --track 4950:5030 [--pad 16] [--step 3]
    → 打出每帧二维码的跟踪框(帧号相对 4950),可以拿去做跟踪打码。
    认不出码的帧(变形、遮挡)会跳过,最后一个关键帧按需要手动放大 / 延长。

做法:
- 每帧先用 OpenCV 找疑似二维码(定位角);
- 只看大致方形、边长 ≥ 36px 的候选,在每个位置裁一块、放大 3–5 倍再解码。小二维码整帧解不出来,放大才行,G6 那个就是放大 4 倍才解出;
- 只报「能解出内容」的,同一内容连续出现的帧合并成一段。
- 速度:多进程;全片先 2fps 粗扫,命中的时间段再细扫。3 分钟的片子约 1–2 分钟。
- 退出码:找到能扫的二维码 = 1,否则 0。
- 依赖:pip3 install --user opencv-python-headless(本机 2026-10-01 装过)。
"""
import argparse
import os
import subprocess
import sys

import cv2
import numpy as np

FF = (os.environ.get("FFMPEG") or __import__("shutil").which("ffmpeg") or "ffmpeg")
DET = cv2.QRCodeDetector()
ARUCO = cv2.QRCodeDetectorAruco() if hasattr(cv2, "QRCodeDetectorAruco") else None


def candidates(img):
    """疑似二维码的四边形:只留大致方形、边长 ≥ 36px 的(文字 / 界面误报多是扁的或很小)"""
    cands = []
    for det in filter(None, (DET, ARUCO)):
        try:
            ok, pts = det.detectMulti(img)
        except cv2.error:
            ok, pts = False, None
        if ok and pts is not None:
            cands += [p.reshape(-1, 2) for p in pts]
        try:  # 退化候选框会让 OpenCV 抛 cv2.error,跳过这个检测器,别让整个脚本崩掉(2026-10-10 personal-agent 素材扫描踩到)
            ok1, p1 = det.detect(img)
        except cv2.error:
            ok1, p1 = False, None
        if ok1 and p1 is not None:
            cands.append(p1.reshape(-1, 2))
    out = []
    for q in cands:
        sx, sy = float(np.ptp(q[:, 0])), float(np.ptp(q[:, 1]))
        if min(sx, sy) >= 36 and 0.6 < sx / max(sy, 1) < 1.7:
            out.append(q)
    return out


def decode_frame(img):
    """返回这一帧里能解出的内容集合"""
    found = set()
    h, w = img.shape[:2]
    for q in candidates(img):
        sx, sy = float(np.ptp(q[:, 0])), float(np.ptp(q[:, 1]))
        cx, cy = q.mean(0)
        span = (sx + sy) / 2
        done = False
        # 解码对裁切很敏感(G6 那个只有特定裁法 + 放大 4 倍才解得出):半边长取码宽 0.8 / 1.1 倍,中心抖动 ±10px,放大 3–5 倍
        for factor in (0.8, 1.1):
            half = max(120, factor * span)
            for dx, dy in ((0, 0), (-10, 6), (10, -6)):
                x0, y0 = int(max(0, cx + dx - half)), int(max(0, cy + dy - half))
                x1, y1 = int(min(w, cx + dx + half)), int(min(h, cy + dy + half))
                crop = img[y0:y1, x0:x1]
                for k in (4, 3, 5):
                    try:
                        data, _, _ = DET.detectAndDecode(cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC))
                    except cv2.error:
                        data = ""
                    if data:
                        found.add(data)
                        done = True
                        break
                if done:
                    break
            if done:
                break
    return found


def _work(args):
    t, buf, W, H = args
    return t, decode_frame(np.frombuffer(buf, np.uint8).reshape(H, W, 3))


def video_size(path):
    import re

    info = subprocess.run([FF, "-hide_banner", "-i", path], capture_output=True, text=True).stderr
    m = re.search(r"Video:.*?(\d{3,5})x(\d{3,5})", info)
    return int(m.group(1)), int(m.group(2))


def track_video(path, a, b, pad, step, fps=30):
    """整片帧 a–b 逐帧找二维码位置(只找不解),打出 TrackedBlur 的 track(帧号相对 a)"""
    W, H = video_size(path)
    cmd = [FF, "-v", "error", "-ss", f"{a / fps:.4f}", "-i", path, "-frames:v", str(b - a + 1), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    rows, miss = [], []
    for i in range(b - a + 1):
        buf = p.stdout.read(W * H * 3)
        if len(buf) < W * H * 3:
            break
        qs = candidates(np.frombuffer(buf, np.uint8).reshape(H, W, 3))
        # 贴着画面边缘的大框是误检(整块界面被当成码)
        qs = [q for q in qs if q[:, 0].min() > 2 and q[:, 1].min() > 2 and q[:, 0].max() < W - 3 and q[:, 1].max() < H - 3]
        if not qs:
            miss.append(i)
            continue
        q = max(qs, key=lambda q: float(np.ptp(q[:, 0]) * np.ptp(q[:, 1])))
        rows.append([i, int(q[:, 0].min()) - pad, int(q[:, 1].min()) - pad, int(q[:, 0].max()) + pad, int(q[:, 1].max()) + pad])
    # 去掉尺寸离谱的误检(码变形 / 消失时,检测器会把整块界面当成码):宽或高偏离中位数 1.6 倍以上的不要
    if rows:
        mw = float(np.median([r[3] - r[1] for r in rows]))
        mh = float(np.median([r[4] - r[2] for r in rows]))
        odd = [r for r in rows if not (mw / 1.6 < r[3] - r[1] < mw * 1.6 and mh / 1.6 < r[4] - r[2] < mh * 1.6)]
        miss = sorted(miss + [r[0] for r in odd])
        rows = [r for r in rows if r not in odd]
    if not rows:
        print(f"帧 {a}–{b} 里没找到二维码")
        return
    keep = [r for j, r in enumerate(rows) if j % step == 0 or j == len(rows) - 1]
    print(f"// qr-scan --track {a}:{b}(帧号相对 {a};{len(rows)} 帧找到,{len(miss)} 帧没找到{':' + ','.join(map(str, miss[:12])) + ('…' if len(miss) > 12 else '') if miss else ''})")
    print("const TRACK: TrackKey[] = [")
    for r in keep:
        print(f"  [{', '.join(map(str, r))}],")
    print("];")


def scan_video(path, fps, t0=None, t1=None):
    from multiprocessing import Pool

    W, H = video_size(path)
    cmd = [FF, "-v", "error"]
    if t0 is not None:
        cmd += ["-ss", str(t0)]
    cmd += ["-i", path]
    if t1 is not None:
        cmd += ["-t", str(t1 - (t0 or 0))]
    cmd += ["-vf", f"fps={fps}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)

    def frames():
        i = 0
        while True:
            buf = p.stdout.read(W * H * 3)
            if len(buf) < W * H * 3:
                return
            yield ((t0 or 0) + i / fps, buf, W, H)
            i += 1

    hits, n = [], 0
    with Pool(max(2, (os.cpu_count() or 4) - 2)) as pool:
        for t, found in pool.imap(_work, frames(), chunksize=4):
            n += 1
            hits += [(t, d) for d in found]
    return hits, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--fps", type=float, default=5)
    ap.add_argument("--track", metavar="A:B", help="量打码框:整片帧 A–B(30fps)逐帧找二维码,打出 TrackedBlur 的 track")
    ap.add_argument("--pad", type=int, default=16, help="--track:框四周外扩像素")
    ap.add_argument("--step", type=int, default=3, help="--track:每几帧留一个关键帧")
    a = ap.parse_args()
    if a.track:
        f0, f1 = (int(x) for x in a.track.split(":"))
        for path in a.paths:
            track_video(path, f0, f1, a.pad, a.step)
        return
    bad = False
    for path in a.paths:
        if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            found = decode_frame(cv2.imread(path))
            print(f"{path}: {'⚠️ 能扫出 ' + ', '.join(sorted(found)) if found else '没有能扫的二维码'}")
            bad |= bool(found)
            continue
        # 先 2fps 粗扫全片;有命中的时间段前后各 1s 再按 --fps 细扫(二维码很少只露半秒以内)
        hits, n = scan_video(path, min(2, a.fps))
        if hits and a.fps > 2:
            fine = []
            for t in sorted({round(t) for t, _ in hits}):
                fine += scan_video(path, a.fps, max(0, t - 1), t + 1.5)[0]
            hits = sorted(set(hits + fine))
        print(f"{path}: 扫了 {n} 帧(2fps 粗扫,命中段 {a.fps}fps 细扫)")
        if not hits:
            print("  没有能扫的二维码")
            continue
        bad = True
        # 同一内容、间隔 ≤ 1s 的命中合并成一段(解码对裁切敏感,同一个码中间会漏掉几帧)
        segs = []
        for t, d in sorted(hits):
            if segs and segs[-1][2] == d and t - segs[-1][1] <= 1.0:
                segs[-1][1] = t
            else:
                segs.append([t, t, d])
        for t0, t1, d in segs:
            print(f"  ⚠️ {t0:7.2f}–{t1:7.2f}s(帧 {int(t0 * 30)}–{int(t1 * 30)} @30fps)能扫出:{d}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
