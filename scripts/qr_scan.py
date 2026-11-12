#!/usr/bin/env python3
"""发布前扫二维码：成片 / 封面里能被扫出来的二维码 = 站外引流。
manus-2-cue 2026-10-01 发布后被抖音判「画面」违规、限制自己可见，原因就是 Cue 官方实拍里的二维码（能扫出 https://manus.im）。

用法：
  python3 scripts/qr_scan.py <视频或图片...> [--timeline <proj>/src/timeline.json] [--range a-b] [--workers 4]
                             [--fps 5] [--json out.json] [--no-queue]
  例：python3 scripts/qr_scan.py <proj>/out/film.mp4 --timeline <proj>/src/timeline.json
      python3 scripts/qr_scan.py <proj>/out/preview.mp4 --timeline <proj>/src/timeline.json --range 0-449
      python3 scripts/qr_scan.py <proj>/out/cover-*.png
  打码要的跟踪框：python3 scripts/qr_scan.py <没打码的渲染.mp4> --track 4950:5030 [--pad 16] [--step 3]
    → 打出每帧二维码的跟踪框（帧号相对 4950），可以拿去做跟踪打码。认不出码的帧（变形、遮挡）会跳过。

扫哪些帧（报告和 JSON 里逐帧列出）：
  - 均匀：每 round(fps/2) 帧一帧（30 fps 下每 15 帧 = 2 fps）；
  - 按镜头补帧（给了 --timeline）：每个镜头可见区间的首、中、尾三帧，7–8 帧的短镜也不会整镜跳过；
  - 命中细扫：有解码成功的帧，前 1 秒到后 1.5 秒按 --fps 再扫一遍（二维码很少只露半秒以内）。
  范围：`a-b` 闭区间 / `a:b` 半开，指成片帧号。视频帧数等于范围长度 → 视频就是这一段；否则只扫视频里的这些帧。
做法：每帧先用 OpenCV 找疑似二维码（定位角），只看大致方形、边长 ≥ 36 px 的候选，在候选位置裁一块、放大 3–5 倍再解码
  （小二维码整帧解不出来，放大才行）。同一内容连续出现的帧合并成一段。
报告分两类：
  - 解码成功：能扫出内容的 → 退出码 1；
  - 疑似位置：找到了像定位角的东西但解不出内容（多半是字标、反光、格纹）→ 只列出来让人看一眼，不算失败。
检查边界：抽样扫描，不是逐帧证明「没有二维码」；没扫到的帧里有一闪而过（< 1/2 秒且不在镜头首中尾）的码可能漏掉。
  只查二维码，电话、地图、商标、车牌要人看。
资源：--workers 个进程（默认 4），每个进程 OpenCV 单线程；视频解码是重活，自动钻进 scripts/queue.sh 排队。
依赖：numpy、opencv-python-headless；ffmpeg（PATH 里，或环境变量 FFMPEG）。兼容 Python 3.9。
退出码：找到能扫的二维码 = 1，否则 0。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

cv2.setNumThreads(1)  # 多进程时每个进程只用一个线程，总并发 = --workers

FF = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
DET = cv2.QRCodeDetector()
ARUCO = cv2.QRCodeDetectorAruco() if hasattr(cv2, "QRCodeDetectorAruco") else None


def candidates(img):
    """疑似二维码的四边形：只留大致方形、边长 ≥ 36px 的（文字 / 界面误报多是扁的或很小）"""
    cands = []
    for det in filter(None, (DET, ARUCO)):
        try:
            ok, pts = det.detectMulti(img)
        except cv2.error:
            ok, pts = False, None
        if ok and pts is not None:
            cands += [p.reshape(-1, 2) for p in pts]
        try:  # 退化候选框会让 OpenCV 抛 cv2.error，跳过这个检测器
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


def decode_frame(img, with_cands=False):
    """返回这一帧里能解出的内容集合（with_cands=True 时另返回候选框列表 [x0, y0, x1, y1]）"""
    found = set()
    h, w = img.shape[:2]
    cands = candidates(img)
    for q in cands:
        sx, sy = float(np.ptp(q[:, 0])), float(np.ptp(q[:, 1]))
        cx, cy = q.mean(0)
        span = (sx + sy) / 2
        done = False
        # 解码对裁切很敏感：半边长取码宽 0.8 / 1.1 倍，中心抖动 ±10px，放大 3–5 倍
        for factor in (0.8, 1.1):
            half = max(120, factor * span)
            for dx, dy in ((0, 0), (-10, 6), (10, -6)):
                x0, y0 = int(max(0, cx + dx - half)), int(max(0, cy + dy - half))
                x1, y1 = int(min(w, cx + dx + half)), int(min(h, cy + dy + half))
                crop = img[y0:y1, x0:x1]
                if crop.size == 0:
                    continue
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
    if with_cands:
        boxes = [[int(q[:, 0].min()), int(q[:, 1].min()), int(q[:, 0].max()), int(q[:, 1].max())] for q in cands]
        return found, boxes
    return found


def _work(args):
    f, buf, W, H = args
    found, boxes = decode_frame(np.frombuffer(buf, np.uint8).reshape(H, W, 3), with_cands=True)
    return f, sorted(found), boxes


def video_info(path):
    info = subprocess.run([FF, "-hide_banner", "-nostdin", "-i", path], capture_output=True, text=True).stderr
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", info)
    if not m:
        sys.exit("✗ %s 里没有视频流" % path)
    mf = re.search(r"Video:.*?([\d.]+)\s*fps", info)
    r = subprocess.run([FF, "-hide_banner", "-nostdin", "-i", path, "-map", "0:v:0", "-c", "copy", "-f", "null", "-"], capture_output=True, text=True)
    mn = re.findall(r"frame=\s*(\d+)", r.stderr)
    return int(m.group(1)), int(m.group(2)), float(mf.group(1)) if mf else 30.0, int(mn[-1]) if mn else 0


def scan_frames(path, frames, W, H, fps, workers):
    """解码视频里指定的帧（0 起的帧号），多进程逐帧找码。返回 {帧: (内容列表, 候选框)}。"""
    from multiprocessing import Pool

    frames = sorted(set(int(f) for f in frames if f >= 0))
    if not frames:
        return {}
    expr = "+".join("eq(n\\,%d)" % f for f in frames)
    with tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False) as lf:
        logpath = lf.name

    def run(sync_flag):
        cmd = [FF, "-hide_banner", "-nostdin", "-nostats", "-i", path, "-an", "-vf", "select=%s,showinfo" % expr] + sync_flag + \
              ["-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=open(logpath, "w"))

    out = {}
    for flag in (["-fps_mode", "passthrough"], ["-vsync", "0"]):
        p = run(flag)
        bufs = []

        def gen():
            k = 0
            while True:
                buf = p.stdout.read(W * H * 3)
                if len(buf) < W * H * 3:
                    return
                bufs.append(k)
                yield (k, buf, W, H)
                k += 1

        res = []
        with Pool(max(1, workers)) as pool:
            for r in pool.imap(_work, gen(), chunksize=2):
                res.append(r)
        rc = p.wait()
        log = open(logpath, encoding="utf-8", errors="replace").read()
        if rc != 0 and "fps_mode" in log and flag[0] == "-fps_mode":
            continue  # 老 ffmpeg 不认 -fps_mode，换 -vsync 0
        if rc != 0:
            sys.exit("✗ ffmpeg 解码失败：%s\n%s" % (path, log[-400:]))
        pts = [float(x) for x in re.findall(r"pts_time:\s*([\d.]+)", log)]
        for k, found, boxes in res:
            f = int(round(pts[k] * fps)) if k < len(pts) else frames[k]
            out[f] = (found, boxes)
        break
    try:
        os.unlink(logpath)
    except OSError:
        pass
    return out


def parse_range(spec, total=None):
    s = str(spec).strip()
    if ":" in s:
        x, y = s.split(":", 1)
        return int(x or 0), (int(y) if y.strip() else total)
    i = s.index("-", 1) if "-" in s[1:] else (0 if s.startswith("-") else -1)
    if i < 0:
        return int(s), int(s) + 1
    x, y = s[:i], s[i + 1:]
    return int(x or 0), (int(y) + 1 if y.strip() else total)


def plan(n, fps, tl, rng, fine_fps):
    """返回 (偏移, 均匀帧, 镜头帧, 说明)。帧号都是视频帧号。"""
    total = int(tl["duration"]) if tl else None
    offset, lo, hi, mode = 0, 0, n, "整段视频"
    if rng:
        a, b = rng
        b = b if b is not None else (total or n)
        if n == b - a:
            offset, lo, hi, mode = a, 0, n, "视频就是成片帧 %d–%d" % (a, b - 1)
        else:
            offset, lo, hi, mode = 0, max(0, a), min(n, b), "只扫视频帧 %d–%d" % (a, b - 1)
    step = max(1, int(round(fps / 2.0)))
    uniform = list(range(lo, hi, step))
    shot = []
    if tl:
        for s in tl.get("shots") or []:
            f0, f1 = int(s.get("from", s.get("cut", 0))), int(s.get("to", s.get("end", 0)))
            if f1 <= f0:
                continue
            for f in (f0, (f0 + f1 - 1) // 2, f1 - 1):
                v = f - offset
                if lo <= v < hi:
                    shot.append(v)
    return offset, uniform, sorted(set(shot) - set(uniform)), step, mode, (lo, hi)


def track_video(path, a, b, pad, step):
    """整片帧 a–b 逐帧找二维码位置（只找不解），打出 TrackedBlur 的 track（帧号相对 a）"""
    W, H, fps, _ = video_info(path)
    cmd = [FF, "-v", "error", "-ss", "%.4f" % (a / fps), "-i", path, "-frames:v", str(b - a + 1), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    rows, miss = [], []
    for i in range(b - a + 1):
        buf = p.stdout.read(W * H * 3)
        if len(buf) < W * H * 3:
            break
        qs = candidates(np.frombuffer(buf, np.uint8).reshape(H, W, 3))
        qs = [q for q in qs if q[:, 0].min() > 2 and q[:, 1].min() > 2 and q[:, 0].max() < W - 3 and q[:, 1].max() < H - 3]
        if not qs:
            miss.append(i)
            continue
        q = max(qs, key=lambda q: float(np.ptp(q[:, 0]) * np.ptp(q[:, 1])))
        rows.append([i, int(q[:, 0].min()) - pad, int(q[:, 1].min()) - pad, int(q[:, 0].max()) + pad, int(q[:, 1].max()) + pad])
    if rows:
        mw = float(np.median([r[3] - r[1] for r in rows]))
        mh = float(np.median([r[4] - r[2] for r in rows]))
        odd = [r for r in rows if not (mw / 1.6 < r[3] - r[1] < mw * 1.6 and mh / 1.6 < r[4] - r[2] < mh * 1.6)]
        miss = sorted(miss + [r[0] for r in odd])
        rows = [r for r in rows if r not in odd]
    if not rows:
        print("帧 %d–%d 里没找到二维码" % (a, b))
        return
    keep = [r for j, r in enumerate(rows) if j % step == 0 or j == len(rows) - 1]
    print("// qr-scan --track %d:%d（帧号相对 %d；%d 帧找到，%d 帧没找到%s）" % (
        a, b, a, len(rows), len(miss), (":" + ",".join(map(str, miss[:12])) + ("…" if len(miss) > 12 else "")) if miss else ""))
    print("const TRACK: TrackKey[] = [")
    for r in keep:
        print("  [%s]," % ", ".join(map(str, r)))
    print("];")


def scan_video(path, tl, rng, workers, fine_fps):
    W, H, fps, n = video_info(path)
    offset, uniform, shot, step, mode, (lo, hi) = plan(n, fps, tl, rng, fine_fps)
    res = scan_frames(path, uniform + shot, W, H, fps, workers)
    hit_frames = sorted(f for f, (found, _) in res.items() if found)
    fine = []
    if hit_frames and fine_fps > 0:
        fstep = max(1, int(round(fps / fine_fps)))
        want = set()
        for f in hit_frames:
            want.update(range(max(lo, f - int(fps)), min(hi, f + int(1.5 * fps)), fstep))
        fine = sorted(want - set(res))
        res.update(scan_frames(path, fine, W, H, fps, workers))
    hits = sorted((f, d) for f, (found, _) in res.items() for d in found)
    segs = []
    for f, d in hits:
        if segs and segs[-1]["data"] == d and f - segs[-1]["to"] <= fps:
            segs[-1]["to"] = f
        else:
            segs.append({"from": f, "to": f, "data": d})
    for s in segs:
        s["film_from"], s["film_to"] = s["from"] + offset, s["to"] + offset
    suspects = [{"frame": f, "film_frame": f + offset, "boxes": boxes} for f, (found, boxes) in sorted(res.items()) if boxes and not found]
    return {"path": path, "fps": fps, "frames_total": n, "mode": mode, "video_offset": offset,
            "uniform_step": step, "uniform_frames": len(uniform), "shot_frames": len(shot), "fine_frames": len(fine),
            "scanned": sorted(res), "decoded": segs, "suspects": suspects,
            "status": "fail" if segs else "pass",
            "coverage": "均匀每 %d 帧（≈ %.1f fps）%d 帧 + 镜头首中尾补 %d 帧 + 命中细扫 %d 帧 = %d 帧（视频共 %d 帧）" % (
                step, fps / step, len(uniform), len(shot), len(fine), len(res), n)}


def main():
    ap = argparse.ArgumentParser(description="扫二维码（视频 / 图片）")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--fps", type=float, default=5, help="命中段细扫的帧率（默认 5）")
    ap.add_argument("--workers", type=int, default=4, help="进程数（默认 4；每个进程 OpenCV 单线程）")
    ap.add_argument("--timeline", help="timeline.json：每个镜头补扫首、中、尾三帧")
    ap.add_argument("--range", help="成片帧范围（a-b 闭区间 / a:b 半开）")
    ap.add_argument("--json", help="结果另存 JSON（含扫过的帧号）")
    ap.add_argument("--track", metavar="A:B", help="量打码框：整片帧 A–B 逐帧找二维码，打出 TrackedBlur 的 track")
    ap.add_argument("--pad", type=int, default=16, help="--track：框四周外扩像素")
    ap.add_argument("--step", type=int, default=3, help="--track：每几帧留一个关键帧")
    ap.add_argument("--no-queue", action="store_true", help="不排队")
    a = ap.parse_args()
    if any(not p.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) for p in a.paths):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import ftvlib as L  # 只用 ensure_queued
        L.ensure_queued()
    if a.track:
        f0, f1 = (int(x) for x in a.track.split(":"))
        for path in a.paths:
            track_video(path, f0, f1, a.pad, a.step)
        return
    tl = json.load(open(a.timeline, encoding="utf-8")) if a.timeline else None
    rng = parse_range(a.range, int(tl["duration"]) if tl else None) if a.range else None
    bad, results = False, []
    for path in a.paths:
        if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            img = cv2.imread(path)
            if img is None:
                sys.exit("✗ 读不了图片：%s" % path)
            found, boxes = decode_frame(img, with_cands=True)
            print("%s：%s" % (path, "⚠️ 能扫出 " + ", ".join(sorted(found)) if found else "没有能扫的二维码"))
            if boxes and not found:
                print("  疑似位置（解不出内容，看一眼）：%s" % boxes)
            bad |= bool(found)
            results.append({"path": path, "status": "fail" if found else "pass", "decoded": sorted(found), "suspect_boxes": boxes if not found else []})
            continue
        r = scan_video(path, tl, rng, a.workers, a.fps)
        results.append(r)
        print("%s：%s；%s" % (path, r["mode"], r["coverage"]))
        if not tl:
            print("  （没给 --timeline：只均匀扫，短镜可能整镜跳过）")
        if r["decoded"]:
            bad = True
            for s in r["decoded"]:
                print("  ⚠️ 解码成功：视频帧 %d–%d（成片帧 %d–%d，%.2f–%.2f s）能扫出：%s" % (
                    s["from"], s["to"], s["film_from"], s["film_to"], s["from"] / r["fps"], s["to"] / r["fps"], s["data"]))
        else:
            print("  解码成功：无")
        if r["suspects"]:
            show = r["suspects"][:10]
            print("  疑似位置（找到定位角但解不出内容，多半是字标 / 反光，看一眼）：%d 帧，例 %s%s" % (
                len(r["suspects"]), "；".join("帧 %d %s" % (s["film_frame"], s["boxes"][0]) for s in show),
                "…" if len(r["suspects"]) > 10 else ""))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(results if len(results) > 1 else results[0], f, ensure_ascii=False, indent=1)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
