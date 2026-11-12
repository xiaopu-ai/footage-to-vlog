#!/usr/bin/env python3
"""qa.py · 渲后检查一条命令跑完：逐项给 pass / warn / fail / skipped，写清每项测了什么、没测什么。

用法：
  python3 scripts/qa.py <proj> <视频.mp4> [--range a-b] [--kind preview|film] [--style S] [--skip qr,jitter] [--no-queue]
  例：python3 scripts/qa.py <proj> out/preview.mp4 --kind preview
      python3 scripts/qa.py <proj> out/品牌燃剪-v1.mp4 --kind film
  范围：视频对应成片哪几帧。不给就按顺序找：① work/render/*.json 里登记过这个文件（render.py 渲的）→ 用清单里的范围；
        ② 视频帧数 = timeline 时长 → 整片；③ 都不是 → frames 项 fail（加 --range）。`a-b` 闭区间 / `a:b` 半开。
  --kind 默认：范围是整片 → film，否则 preview。film 还要求分辨率等于 timeline 的宽高。

产物：<proj>/work/qa/<视频名>.QA.json（每项的结果、标准、覆盖范围，加上各子脚本的原始 JSON），终端打印汇总表。
退出码：任一项 fail → 1；只有 warn / skipped → 0。

检查项（顺序执行，都是真解码真测量）：
  frames    帧数 = 范围帧数；帧率 = timeline；音轨时长和画面差 ≤ 1 帧；film 分辨率 = timeline。
  color     yuv420p、tv 范围、BT.709 的 matrix / primaries / transfer 三项齐全（读码流标签）。
  av_sync   audio_sync.py：成片音轨对 mix.py 同范围的规范混音做互相关，|时移| ≤ 5 ms 且相关 ≥ 0.9。
  loudness  −14 LUFS ±0.5，真峰值 ≤ −1.5 dBTP（loudnorm 测量）。
  beats     synccheck.py：设计切点 = 实测切点（只测 cut / punch / whip）；有 accents 时强重音命中率，口径和 timeline.py 一致
            （有 kind = drop 的段落只算 drop 段的主轨硬切；s ≥ 0.7；离「重音帧 − lead_frames」≤ 2 帧；少于 4 个不判；
            hype < 60% fail、< 75% warn；launch < 60% warn，和 timeline.py 一致），并列出 timeline.json stats.accent_hit 的自报值；起音 lead 只告警。
  black     synccheck.py：timeline 声明的暗场（expected_dark，没有就推断）以外没有黑场。
  jitter    jitter_scan.py：没有 A-B-A 来回跳。
  qr        qr_scan.py：均匀 2 fps + 每镜首中尾，解不出内容才算通过；疑似位置单列。
  coverage  scripts/coverage.py <proj> --json --strict：退出码 1（源区间重叠 / 镜头日志缺漏）→ fail；有其他 problems → warn；
            缺 footage.json（退出码 2）或脚本不存在 → skipped。
🔴 整个检查是一个重任务（要解码几遍视频）：自动钻进 scripts/queue.sh 排队。
依赖：numpy、opencv-python-headless（扫码）、ffmpeg。兼容 Python 3.9。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import audio_sync as AS  # noqa: E402
import ftvlib as L  # noqa: E402
import mix as M  # noqa: E402
import render as R  # noqa: E402

MARK = {"pass": "✓", "warn": "⚠️", "fail": "✗", "skipped": "–"}
RANK = {"pass": 0, "skipped": 0, "warn": 1, "fail": 2}


def item(id_, name, status, measured, criteria, covered, not_covered, detail=None, raw=None):
    d = {"id": id_, "name": name, "status": status, "measured": measured, "criteria": criteria,
         "covered": covered, "not_covered": not_covered}
    if detail:
        d["detail"] = detail
    if raw is not None:
        d["raw"] = raw
    return d


def sub(cmd, json_path):
    """跑子脚本（已持锁，子脚本不会再排队），读它写的 JSON。返回 (退出码, JSON 或 None, 输出尾巴)。"""
    r = subprocess.run([sys.executable] + [str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace")
    data = None
    if json_path and os.path.exists(json_path):
        try:
            data = json.load(open(json_path, encoding="utf-8"))
        except ValueError:
            data = None
    return r.returncode, data, "\n".join(out.strip().splitlines()[-6:])


def find_manifest(proj, video):
    """在 work/render/*.json 里找登记过这个文件的清单"""
    rd = Path(proj) / "work" / "render"
    if not rd.exists():
        return None, None
    vp = Path(video).resolve()
    for mf in sorted(rd.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            m = json.loads(mf.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        for k, o in (m.get("outputs") or {}).items():
            if isinstance(o, dict) and o.get("path") and Path(o["path"]).resolve() == vp:
                return m, k
    return None, None


def main():
    ap = argparse.ArgumentParser(description="渲后检查汇总")
    ap.add_argument("proj")
    ap.add_argument("video")
    ap.add_argument("--range", help="视频对应的成片帧范围：a-b 闭区间 / a:b 半开（默认从渲染清单或帧数推断）")
    ap.add_argument("--kind", choices=["preview", "film"])
    ap.add_argument("--style", help="覆盖 timeline 的 style（决定重音命中率门槛）")
    ap.add_argument("--skip", default="", help="跳过的项，逗号分隔：qr,jitter,beats,black,coverage,av_sync,loudness")
    ap.add_argument("--max-ms", type=float, default=5.0, help="音画对齐阈值（默认 5 ms）")
    ap.add_argument("--no-queue", action="store_true", help="不排队")
    a = ap.parse_args()
    proj = Path(a.proj).resolve()
    video = Path(a.video)
    if not video.exists() and (proj / a.video).exists():
        video = proj / a.video
    if not video.exists():
        sys.exit("✗ 找不到视频：%s" % a.video)
    video = video.resolve()
    L.ensure_queued()
    t0 = time.time()
    tl = M.load_timeline(proj)
    fps = float(tl.get("fps") or 30)
    total = int(round(M.num(tl.get("duration"), 1)))
    style = a.style or tl.get("style") or "custom"
    skip = {s.strip() for s in a.skip.split(",") if s.strip()}
    qa_dir = proj / "work" / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="ftv-qa-"))
    items, notes = [], []

    # ── 范围 ──
    n = R.count_frames(video)
    pv = R.probe_video(video)
    man, man_key = find_manifest(proj, video)
    rng_src = None
    if a.range:
        rng = M.parse_range(a.range, total)
        rng_src = "--range"
    elif man:
        rng = (man["range"]["from"], man["range"]["to"])
        rng_src = "渲染清单 work/render/%s.json（%s）" % (man["name"], man_key)
        sha_now = R.sha256(video)
        if sha_now != (man["outputs"][man_key] or {}).get("sha256"):
            notes.append("文件和渲染清单里登记的 sha256 不一样（渲完后被改过？）")
    elif n == total:
        rng = (0, total)
        rng_src = "帧数 = timeline 时长，按整片"
    else:
        rng = (0, min(n, total)) if n else (0, total)
        rng_src = "推断不出（没有渲染清单，帧数 %d ≠ timeline %d）：按第 0 帧起算" % (n, total)
    fa, fb = rng
    kind = a.kind or ("film" if (fa, fb) == (0, total) else "preview")
    rlabel = M.range_label(fa, fb)
    print("QA %s · 成片帧 %s（%d 帧，%s）· %s · 风格 %s" % (video.name, rlabel, fb - fa, rng_src, kind, style))

    # ── 1 帧数 / 时长 ──
    exp = fb - fa
    bad = []
    if n != exp:
        bad.append("帧数 %d ≠ %d" % (n, exp))
    if "推断不出" in rng_src:
        bad.append("不知道这段视频对应成片哪几帧（加 --range）")
    if pv.get("fps") and abs(pv["fps"] - fps) > 0.01:
        bad.append("帧率 %s ≠ %s" % (pv["fps"], fps))
    if kind == "film" and (pv.get("width"), pv.get("height")) != (tl.get("width", 1920), tl.get("height", 1080)):
        bad.append("分辨率 %s×%s ≠ %s×%s" % (pv.get("width"), pv.get("height"), tl.get("width", 1920), tl.get("height", 1080)))
    frames_item = item("frames", "帧数和时长", "fail" if bad else "pass",
                       {"frames": n, "expected": exp, "fps": pv.get("fps"), "size": [pv.get("width"), pv.get("height")],
                        "container_s": pv.get("duration_s")},
                       "帧数 = 范围帧数；帧率 = timeline；音轨时长和画面差 ≤ 1 帧；整片分辨率 = timeline",
                       "整个文件（流复制数包）", "不看画面内容", "；".join(bad) or "%d 帧 = %.3f 秒" % (n, n / fps))
    items.append(frames_item)

    # ── 2 色彩 ──
    cb = R.color_ok(pv)
    items.append(item("color", "色彩标签", "fail" if cb else "pass",
                      {k: pv.get(k) for k in ("pix_fmt", "color_range", "color_space", "color_primaries", "color_transfer")},
                      "yuv420p、tv 范围、BT.709 matrix / primaries / transfer 三项齐全",
                      "码流 / 容器标签（%s）" % pv.get("via"),
                      "不逐像素验证 RGB→YUV 矩阵转换本身（render.py 用 PNG 帧 + Remotion zscale BT.709，转换在那一步）",
                      "；".join(cb) or "%s(%s, %s)" % (pv.get("pix_fmt"), pv.get("color_range"), pv.get("color_space"))))

    # ── 3 音画对齐 ──
    has_audio_tl = bool((tl.get("audio") or {}).get("music") or (tl.get("audio") or {}).get("sfx") or (tl.get("audio") or {}).get("voice"))
    sync = None
    if "av_sync" in skip:
        items.append(item("av_sync", "音画对齐", "skipped", None, "|时移| ≤ %g ms" % a.max_ms, "—", "用户跳过"))
    elif not pv.get("has_audio"):
        st = "fail" if (kind == "film" and has_audio_tl) else "skipped"
        items.append(item("av_sync", "音画对齐", st, None, "|时移| ≤ %g ms" % a.max_ms, "—", "文件没有音轨",
                          "文件没有音轨" + ("，但 timeline 有声音" if has_audio_tl else "")))
    else:
        try:
            x, minfo = M.mix_timeline(proj, tl, fa, fb, "all")
            if minfo["silent"]:
                items.append(item("av_sync", "音画对齐", "skipped", None, "|时移| ≤ %g ms" % a.max_ms, "—",
                                  "这一段 timeline 没有声音，没有参考可比"))
            else:
                ref = qa_dir / ("%s.ref-mix.wav" % video.stem)
                M.write_wav(ref, x, M.SR, "32f")
                sync = AS.measure(video, ref, max_ms=a.max_ms)
                st = sync["status"] if sync["status"] in ("pass", "fail") else "fail"
                if st == "pass" and sync.get("warning"):
                    st = "warn"
                items.append(item("av_sync", "音画对齐", st,
                                  {"delay_ms": sync.get("delay_ms"), "corr": sync.get("corr"), "peak_ratio": sync.get("peak_ratio")},
                                  "|时移| ≤ %g ms 且相关 ≥ 0.9（时移 > 0 = 声音落后画面）" % a.max_ms,
                                  "整段音轨 vs mix.py 同范围规范混音（%s），搜索窗 ±%g ms，精度约 %.3f ms" % (
                                      ref.name, sync.get("search_window_ms"), sync.get("resolution_ms")),
                                  "只测整体时移；局部漂移只体现在相关系数里；不判断混音好不好听",
                                  "；".join(sync.get("problems") or []) or ("%+.2f ms，相关 %.4f" % (sync["delay_ms"], sync["corr"])),
                                  raw=sync))
                if sync.get("video_audio_s") is not None:
                    dv = sync["video_audio_s"] - n / fps
                    frames_item["measured"]["audio_s"] = sync["video_audio_s"]
                    if abs(dv) > 1.0 / fps + 1e-6:
                        frames_item["status"] = "fail"
                        frames_item["detail"] += "；音轨 %.3f s，和画面差 %+.0f ms（> 1 帧）" % (sync["video_audio_s"], dv * 1000)
                    else:
                        frames_item["detail"] += "；音轨和画面差 %+.0f ms" % (dv * 1000)
        except Exception as e:  # noqa: BLE001
            items.append(item("av_sync", "音画对齐", "fail", None, "|时移| ≤ %g ms" % a.max_ms, "—", "—", "测不了：%s" % e))

    # ── 4 响度 ──
    if "loudness" in skip or not pv.get("has_audio"):
        items.append(item("loudness", "响度", "skipped" if "loudness" in skip or kind != "film" or not has_audio_tl else "fail",
                          None, "−14 LUFS ±0.5，真峰值 ≤ −1.5 dBTP", "—", "没有音轨" if not pv.get("has_audio") else "用户跳过"))
    else:
        try:
            lm = R.loud_measure(video)
            okI = abs(lm["input_i"] - R.I_TARGET) <= 0.5
            okT = lm["input_tp"] <= R.TP_TARGET
            items.append(item("loudness", "响度", "pass" if okI and okT else "fail",
                              {"I": lm["input_i"], "TP": lm["input_tp"], "LRA": lm["input_lra"]},
                              "−14 LUFS ±0.5，真峰值 ≤ −1.5 dBTP", "整段音轨（EBU R128 积分响度，4 倍过采样真峰值）",
                              "不看段落之间的响度起伏和混音比例",
                              "%.2f LUFS，真峰值 %.2f dBTP%s" % (lm["input_i"], lm["input_tp"],
                                                              "" if okI and okT else "（不达标：用 render.py 重新封装）")))
        except RuntimeError as e:
            items.append(item("loudness", "响度", "fail", None, "−14 LUFS ±0.5，真峰值 ≤ −1.5 dBTP", "—", "—", str(e)))

    # ── 5、6 卡点 + 黑场（synccheck） ──
    if "beats" in skip and "black" in skip:
        sc = None
        items.append(item("beats", "卡点", "skipped", None, "—", "—", "用户跳过"))
        items.append(item("black", "意外黑场", "skipped", None, "—", "—", "用户跳过"))
    else:
        jp = tmp / "sync.json"
        cmd = [HERE / "synccheck.py", video, proj, "--range", rlabel, "--json", jp] + (["--style", a.style] if a.style else [])
        rc, sc, tail = sub(cmd, str(jp))
        if sc is None:
            for k, nm in (("beats", "卡点"), ("black", "意外黑场")):
                items.append(item(k, nm, "fail", None, "—", "—", "—", "synccheck.py 没跑完：%s" % tail))
    if sc:
        it = sc["items"]
        if "beats" not in skip:
            st = max((it["cuts"]["status"], it["accents"]["status"], "warn" if it["onsets"]["status"] == "warn" else "pass"),
                     key=lambda s: RANK.get(s, 0))
            tst = (tl.get("stats") or {}).get("accent_hit") or None
            det = " | ".join(it[k]["detail"] for k in ("cuts", "accents", "onsets"))
            if tst and tst.get("rate") is not None:
                det += " | timeline.py 自报（按设计帧）：%s %d/%d = %.0f%%" % (
                    "drop 段" if tst.get("scope") == "drop" else "全片", tst.get("hits", 0), tst.get("n", 0), tst["rate"] * 100)
            items.append(item("beats", "卡点", st,
                              {"hard_cuts": it["cuts"].get("hard"), "exact": it["cuts"].get("exact"), "dv_hist": it["cuts"].get("dv_hist"),
                               "accent_scope": it["accents"].get("scope"), "accent_hit_rate": it["accents"].get("hit_rate"),
                               "accent_hits": [it["accents"].get("hits"), it["accents"].get("n")], "accent_all_rate": it["accents"].get("all_rate"),
                               "accent_miss": it["accents"].get("miss"), "timeline_accent_hit": tst,
                               "onset_median_lead": it["onsets"].get("median_lead")},
                              "硬切实测 = 设计帧；强重音命中（有 drop 段只算 drop 段的主轨硬切，s ≥ 0.7，±2 帧，目标帧 = 重音帧 − lead_frames）%s；"
                              "起音 lead 中位 0–2 帧（只告警）" % (it["accents"].get("rule") or ""),
                              "硬切（cut / punch / whip）%d 个；过程转场 %d 个单列不测；%s" % (
                                  it["cuts"].get("hard", 0), it["cuts"].get("process_not_measured", 0), sc["definition"]),
                              "fade / wipe / sweep / dip 不测切点；起音最近匹配在鼓点密时不可靠；节奏体感要人看",
                              det, raw={k: it[k] for k in ("cuts", "accents", "onsets")}))
        else:
            items.append(item("beats", "卡点", "skipped", None, "—", "—", "用户跳过"))
        if "black" in skip:
            items.append(item("black", "意外黑场", "skipped", None, "—", "—", "用户跳过"))
        else:
            items.append(item("black", "意外黑场", it["black"]["status"],
                              {"segments": len(it["black"]["segments"]),
                               "unexpected": [b for b in it["black"]["segments"] if not b["expected"]]},
                              "声明的暗场（expected_dark）以外没有 ≥ %g s 的黑场" % it["black"]["min_s"],
                              "blackdetect pix_th %g；暗场来源：%s" % (it["black"]["pix_th"], it["black"]["source"]),
                              "暗而不黑的画面、白闪、落版 Logo 是否可见不查", it["black"]["detail"], raw=it["black"]))
        notes += sc.get("notes") or []

    # ── 7 抖动 ──
    if "jitter" in skip:
        items.append(item("jitter", "抖动", "skipped", None, "没有 A-B-A 来回跳", "—", "用户跳过"))
    else:
        jp = tmp / "jitter.json"
        rc, jj, tail = sub([HERE / "jitter_scan.py", video, "--base", fa, "--json", jp], str(jp))
        if jj is None:
            items.append(item("jitter", "抖动", "fail", None, "没有 A-B-A 来回跳", "—", "—", "jitter_scan.py 没跑完：%s" % tail))
        else:
            items.append(item("jitter", "抖动", jj["status"], {"single_hits": jj["single_hits"], "flagged_seconds": len(jj["flagged_seconds"])},
                              "同一秒内 A-B-A 来回跳 < 3 次", "全部 %d 帧（480×270 灰度）" % jj["frames_scanned"],
                              "只查 A-B-A 型来回跳；重复帧、卡顿、变速发顿、编码马赛克不查",
                              "单次来回跳 %d 次，≥ 3 次 / 秒的 %d 秒" % (jj["single_hits"], len(jj["flagged_seconds"])), raw=jj))

    # ── 8 二维码 ──
    if "qr" in skip:
        items.append(item("qr", "二维码", "skipped", None, "解不出内容", "—", "用户跳过"))
    else:
        jp = tmp / "qr.json"
        rc, qj, tail = sub([HERE / "qr_scan.py", video, "--timeline", proj / "src" / "timeline.json", "--range", rlabel, "--json", jp], str(jp))
        if qj is None:
            items.append(item("qr", "二维码", "fail", None, "解不出内容", "—", "—", "qr_scan.py 没跑完：%s" % tail))
        else:
            dec = qj.get("decoded") or []
            items.append(item("qr", "二维码", qj["status"],
                              {"scanned": len(qj.get("scanned") or []), "decoded": dec, "suspects": len(qj.get("suspects") or [])},
                              "扫过的帧里解不出二维码内容", qj.get("coverage", ""),
                              "抽样扫描：两次抽样之间一闪而过（< 0.5 s 且不在镜头首中尾）的码可能漏；电话、地图、商标、车牌要人看",
                              ("能扫出：" + "；".join("成片帧 %d–%d %s" % (s["film_from"], s["film_to"], s["data"]) for s in dec)) if dec else
                              ("解码成功 0 处；疑似位置 %d 帧（人工看一眼）" % len(qj.get("suspects") or [])), raw=qj))

    # ── 9 素材覆盖 ──
    cov = HERE / "coverage.py"
    if "coverage" in skip:
        items.append(item("coverage", "素材覆盖", "skipped", None, "—", "—", "用户跳过"))
    elif not cov.exists():
        items.append(item("coverage", "素材覆盖", "skipped", None, "没有源区间重叠复用", "—", "scripts/coverage.py 不存在，没查"))
    else:
        r = subprocess.run([sys.executable, str(cov), str(proj), "--json", "--strict"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        txt = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace").strip()
        try:
            data = json.loads(txt)
        except ValueError:
            data = None
        crit = "同一素材的源区间不重叠复用、镜头日志不缺（coverage.py --strict）"
        if r.returncode == 2 or (r.returncode != 0 and data is None):
            items.append(item("coverage", "素材覆盖", "skipped" if r.returncode == 2 else "fail", None, crit, "—",
                              "coverage.py 没法算：%s" % (err.splitlines()[-1] if err else "退出码 %d" % r.returncode),
                              "coverage.py 退出码 %d：%s" % (r.returncode, err.splitlines()[-1] if err else txt.strip()[-200:])))
        else:
            data = data or {}
            probs = data.get("problems") or []
            bad_ov = [o for o in data.get("overlaps") or [] if not o.get("allowed")]
            st = "fail" if r.returncode == 1 else ("warn" if probs else "pass")
            meas = {k: data.get(k) for k in ("footage_total", "used", "used_share", "unused", "shots_with_src")}
            meas.update(reuse=len(data.get("reuse") or []), overlaps=len(bad_ov), problems=probs)
            items.append(item("coverage", "素材覆盖", st, meas, crit,
                              "timeline 全部镜头 vs work/footage.json 全部素材、work/shotlog.md（coverage.py，不解码视频）",
                              "镜头选得好不好、复用是否合理、素材用得够不够（C 级建议）要人看",
                              "用了 %s/%s 条（%s），复用 ≥ 2 次 %d 条，未豁免的源区间重叠 %d 处%s" % (
                                  data.get("used"), data.get("footage_total"), data.get("used_share"), meas["reuse"], len(bad_ov),
                                  ("；问题：" + "、".join(probs)) if probs else ""), raw=data))

    status = max((it["status"] for it in items), key=lambda s: RANK.get(s, 0))
    status = "fail" if status == "fail" else ("warn" if status == "warn" else "pass")
    res = {"video": str(video), "sha256": R.sha256(video), "proj": str(proj), "created": datetime.now().isoformat(timespec="seconds"),
           "kind": kind, "style": style, "range": {"from": fa, "to": fb, "frames_arg": rlabel, "source": rng_src},
           "status": status, "items": items, "notes": notes, "seconds": round(time.time() - t0, 1),
           "timeline_stats": tl.get("stats"),
           "how_to_report": "只说跑过的项通过了，并带上 covered；warn / skipped / not_covered 单独列；数值检查通过 ≠ 用户批准"}
    out = qa_dir / ("%s.QA.json" % video.stem)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    for p in tmp.glob("*"):
        p.unlink()
    tmp.rmdir()

    print()
    print("%-4s %-10s %s" % ("", "检查", "结果"))
    for it in items:
        print("%-4s %-10s %s" % (MARK[it["status"]], it["name"], it.get("detail") or it["not_covered"]))
        print("%-4s %-10s   覆盖：%s" % ("", "", it["covered"]))
    for n_ in notes:
        print("ⓘ " + n_)
    print("\n汇总：%s（%s）· %s · %.0f 秒" % (status, "、".join("%s %d" % (k, sum(1 for i in items if i["status"] == k))
                                                          for k in ("pass", "warn", "fail", "skipped") if any(i["status"] == k for i in items)),
                                         os.path.relpath(out, proj), res["seconds"]))
    sys.exit(1 if status == "fail" else 0)


if __name__ == "__main__":
    main()
