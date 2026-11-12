#!/usr/bin/env python3
"""make_clips.py · 按 timeline.json 的 clips 切出已调色的片段:<proj>/public/clips/<id>.mp4 + 清单 clips.json。

用法:
  python3 scripts/make_clips.py <proj> [--draft] [--only S01,S07] [--force] [--prune] [--no-queue]
  python3 scripts/make_clips.py <proj> --check       # 不转码,只查片段是否齐全、参数是否最新、是否都是 final;不是就退出码 1
  会自动钻进 scripts/queue.sh 排队;脚本内部一条一条串行转,同一时间只有一个 ffmpeg。

输入:
  <proj>/src/timeline.json   clips[]:{id, src, from_s, to_s, from_frame, frames, profile, native_fps, fix, look, audio, hash}
                             (timeline.py 编译;src 为 null 的镜头跳过)
  <proj>/project.json        footage_root、luts、profiles(手动改判,优先级最高)、look、levels_target
  <proj>/work/footage.json   有就用它的 range / matrix / profile(inventory.py 产物),没有就现场分析

色彩链(和 grab.py / measure.py 同一套代码):
  解码(-ss / -t 放在 -i 前)→ setpts=N/(30*TB) conform(保留全部原生帧,按 30 fps 放)→ 缩放到 1920×1080 rgb48le
  (显式 in_range / in_color_matrix)→ 技术 LUT → fix.exposure → fix.warm → fix.levels → HALD look
  → tv range BT.709 yuv420p → libx264 crf 14(draft:960 宽、ultrafast、crf 23)
  按帧切:-ss from_s(timeline.py 保证第一帧就是 from_frame)+ trim=end_frame=frames,片段第 i 帧 = 源素材第 from_frame + i 帧,
  帧数必须正好等于 frames(写进清单核对);没有 frames 字段时按 from_s–to_s 切,帧数 ≈ (to_s − from_s) × native_fps。
  audio 为真时带原速原声(AAC 192k,时长 frames / native_fps,不受 conform 影响)。
  曲线优先级:project.json profiles > footage.json > 现场判断;timeline.json 里抄的 profile 和它们不一致时提示并以前者为准。
  macOS 上 4K HEVC 自动用 videotoolbox 硬解(同一格式先自测 2 秒 PSNR,inf 才用;失败自动回退软解)。

产物:
  public/clips/<id>.mp4
  public/clips/clips.json    {"all_final": bool, "clips": {id: {file, hash, quality: final|draft, frames, expected_frames,
                             levels(auto 量出来的 p5/p95), profile, lut, look, fix, audio, hwaccel, seconds, …}}}
  参数 hash 不变就跳过(hash 含素材大小 / 修改时间、区间、曲线、LUT 和色表文件内容、fix、音频;不含质量)。
  已有 final 时跑 --draft 不会降级覆盖。成片渲染前跑 --check,必须 all_final。

切完每条都自检(final 和 draft 都做,结果写进清单):
  调色越线:片段可见区间(去掉 handles)25% / 50% / 75% 抽 3 帧量亮度,任一帧 p5 < 0.02 或 p95 > 0.96 →
    开着 fix.levels:标「色表和 levels 冲突」(levels 拉满后强对比色表又压一次);没开:标「压死 / 削顶」。
    清单里 clips.<id>.grade = {p5, p50, p95}(3 帧里最差的 p5 / p95)、clips.<id>.warn = [说明…],终端打印,最后汇总。
    只是警告,不改退出码;改法:去掉 levels 改用 exposure / warm,或把 levels_target 收到 [0.06, 0.86](measure.py 会量好两种改法)。
  耗时:每条打印用时;单条超过预期 10 倍(按本次 / 上次同质量片段的「秒 / 帧」中位数估)时提示「可能是休眠」。
    另外用墙钟和单调时钟的差直接测系统休眠了多久(macOS / Linux 的单调时钟在休眠时不走)。
    起因:用户离开时系统休眠,ffmpeg 被挂起,3 条片段各空转 900+ 秒。queue.sh 已默认 caffeinate -i 防休眠。

缓存:硬解自测结果默认放 <proj>/work/cache(写不进只提示,见 ftvlib.py)。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ftvlib as L  # noqa: E402

MAKE_VERSION = "2"
SLOW_FACTOR = 10      # 单条超过预期多少倍算异常
SLOW_MIN_S = 30       # 低于这个秒数不报
SLEEP_MIN_S = 5       # 墙钟比单调时钟多出这么多秒 = 系统休眠过


def grade_selfcheck(path, nframes, fps, handles_frames, fix, look):
    """量已切好的片段:可见区间 25 / 50 / 75% 三帧。返回 (grade, warn 列表)。"""
    n = max(1, int(nframes or 0))
    h = int(handles_frames)
    a, b = (h, n - h) if n - 2 * h > 6 else (0, n)
    times = [(a + (b - a) * k) / float(fps) for k in (0.25, 0.5, 0.75)]
    ss = L.file_frame_stats(path, times)
    g = {"p5": round(min(x["p5"] for x in ss), 4), "p50": round(sorted(x["p50"] for x in ss)[1], 4),
         "p95": round(max(x["p95"] for x in ss), 4)}
    chk = L.grade_check(g["p5"], g["p95"], fix, look)
    return g, ([chk["msg"] + "。改法:" + chk["advice"]] if chk else [])


def expected_rate(rates):
    xs = sorted(rates)
    return xs[len(xs) // 2] if len(xs) >= 2 else None


def enc_args(draft, fps):
    if draft:
        q = ["-preset", "ultrafast", "-crf", "23"]
    else:
        q = ["-preset", "medium", "-crf", "14"]
    return ["-c:v", "libx264"] + q + ["-g", str(fps), "-pix_fmt", "yuv420p", "-colorspace", "bt709",
                                      "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
                                      "-movflags", "+faststart"]


_FFMAJOR = None


def fps_mode(fps):
    """ffmpeg ≥ 5.1 用 -fps_mode,更老的用 -vsync。"""
    global _FFMAJOR
    if _FFMAJOR is None:
        m = re.search(r"version n?(\d+)\.(\d+)", L._ff_version())
        _FFMAJOR = (int(m.group(1)), int(m.group(2))) if m else (7, 0)
    flag = "-fps_mode" if _FFMAJOR >= (5, 1) else "-vsync"
    return [flag, "cfr", "-r", str(fps)]


def count_frames(path):
    r = subprocess.run([L.ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:v:0", "-c", "copy", "-f", "null", "-"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    m = re.findall(r"frame=\s*(\d+)", r.stderr.decode("utf-8", "replace"))
    return int(m[-1]) if m else None


def plan(proj, project, c, footage, draft, fps):
    e = L.source_entry(proj, project, c["src"], footage=footage, profile=c.get("profile"))
    over = (project.get("profiles") or {}).get(c["src"])
    if over and c.get("profile") and c["profile"] != over:
        print("  · %s:project.json profiles 改判 %s → %s" % (c["id"], c["profile"], over))
    elif e.get("profile_mismatch"):
        L.warn("%s:timeline.json 写的曲线是 %s,footage.json / 实测是 %s,按后者切(要改判写进 project.json 的 profiles)"
               % (c["id"], e["profile_mismatch"], e["profile"]))
    lut = L.resolve_lut(proj, project, e["profile"])
    look_rel = c["look"] if "look" in c else project.get("look")
    look = None
    if look_rel:
        lp = Path(look_rel) if os.path.isabs(look_rel) else proj / look_rel
        if not lp.exists():
            raise RuntimeError("%s 的色表不存在:%s(先跑 look.py build)" % (c["id"], lp))
        look = str(lp.resolve())
    fix = c.get("fix") or {}
    nfps = float(c.get("native_fps") or e.get("fps") or fps)
    dur = float(e.get("duration") or 0)
    a = float(c["from_s"])
    nframes = int(c["frames"]) if c.get("frames") else None
    b = a + nframes / nfps if nframes else float(c["to_s"])
    if a < 0:
        L.warn("%s from_s=%.3f < 0,按 0 切;片段第 0 帧会比 timeline 预期晚 %.3f s(timeline.py 应该先夹住 handles)" % (c["id"], a, -a))
        a = 0.0
    if dur and b > dur + 0.5 / nfps:
        L.warn("%s 区间结尾 %.3f 超过素材时长 %.3f,截到结尾" % (c["id"], b, dur))
        b = dur
        if nframes:
            nframes = max(1, int((b - a) * nfps))
    if b <= a:
        raise RuntimeError("%s 区间无效:%.3f–%.3f" % (c["id"], a, b))
    audio = bool(c.get("audio"))
    if audio and not e.get("has_audio"):
        L.warn("%s 要声音,但素材没有音轨,出无声片段" % c["id"])
        audio = False
    W, H = project["width"], project["height"]
    if draft:
        W, H = 960, round(960 * H / W / 2) * 2
    target = tuple(project.get("levels_target") or L.DEFAULT_LEVELS_TARGET)
    key = {
        "lib": L.LIB_VERSION, "make": MAKE_VERSION, "src": c["src"], "size": os.path.getsize(e["_abs"]),
        "mtime": int(os.path.getmtime(e["_abs"])), "from_s": round(a, 6), "to_s": round(b, 6),
        "from_frame": c.get("from_frame"), "frames": nframes, "profile": e["profile"],
        "range": e.get("range"), "matrix": e.get("matrix"), "rotation": e.get("rotation", 0),
        "lut": L.sha1_file(lut)[:16] if lut else None, "look": L.sha1_file(look)[:16] if look else None,
        "fix": fix, "levels_target": list(target) if fix.get("levels") else None, "audio": audio, "fps": fps,
        "native_fps": nfps, "out": [project["width"], project["height"]],
    }
    return {"id": c["id"], "entry": e, "lut": lut, "look": look, "look_rel": look_rel, "fix": fix, "a": a, "b": b,
            "nframes": nframes, "from_frame": c.get("from_frame"),
            "nfps": nfps, "audio": audio, "W": W, "H": H, "target": target, "hash": L.sha1_obj(key),
            "timeline_hash": c.get("hash")}


def encode(p, out_dir, draft, fps):
    e = p["entry"]
    hw = L.hw_candidate(e) and L.use_hwaccel(e["_abs"], e, p["lut"])
    levels = None
    lv = p["fix"].get("levels")
    if lv == "auto":
        levels = L.measure_levels(e, p["a"], p["b"], p["lut"], p["fix"], hw)
    elif isinstance(lv, dict):
        levels = lv
    # 按帧切:trim=end_frame 在滤镜里数原生帧(-ss 之后的第 0 帧起),正好 frames 帧;
    # 不用输出端 -frames:v ——它会让同一张滤镜图里的声音一起提前结束(实测 25p 采访声音被截到 frames/30 秒)
    pre = ("trim=end_frame=%d," % p["nframes"] if p["nframes"] else "") + "setpts=N/(%s*TB)" % fps
    graph = L.grade_graph(e, p["W"], p["H"], p["lut"], p["fix"], levels, p["target"], p["look"], pre=pre, out="yuv420p")
    dst = out_dir / ("%s.mp4" % p["id"])
    tmp = out_dir / ("%s.part.mp4" % p["id"])

    D = p["b"] - p["a"]
    if p["audio"]:  # 声音按原速、真实时长 D 裁(不受 conform 影响)
        graph += ";[0:a:0]atrim=duration=%.6f,asetpts=PTS-STARTPTS[a]" % D

    def cmd(use_hw):
        # 有 frames 时按帧切:-ss 定到 from_s(timeline.py 已保证第一帧就是 from_frame),输入多读 2 帧余量,trim 卡死帧数
        t_in = D + (2.0 / p["nfps"] if p["nframes"] else 0.0)
        c = L.ff_head() + ["-v", "error"] + L.hw_args(use_hw) + ["-ss", "%.6f" % p["a"], "-t", "%.6f" % t_in,
                                                                 "-i", e["_abs"]] + L.look_inputs(p["look"])
        c += ["-filter_complex", graph, "-map", "[v]"]
        c += (["-map", "[a]", "-c:a", "aac", "-b:a", "192k"] if p["audio"] else ["-an"])
        return c + fps_mode(fps) + enc_args(draft, fps) + L.ff_out_threads() + [str(tmp)]

    t0 = time.time()
    r = subprocess.run(cmd(hw), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0 and hw:
        L.warn("%s 硬解失败,改软解:%s" % (p["id"], r.stderr.decode("utf-8", "replace").strip()[-200:]))
        hw = False
        r = subprocess.run(cmd(False), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        if tmp.exists():
            tmp.unlink()
        raise RuntimeError("%s 转码失败:%s" % (p["id"], r.stderr.decode("utf-8", "replace").strip()[-600:]))
    os.replace(tmp, dst)
    n = count_frames(dst)
    exp = p["nframes"] or round((p["b"] - p["a"]) * p["nfps"])
    tol = 0 if p["nframes"] else 1
    return {
        "file": "clips/%s.mp4" % p["id"], "hash": p["hash"], "quality": "draft" if draft else "final",
        "width": p["W"], "height": p["H"], "fps": fps, "src": e["path"] if "path" in e else None,
        "from_s": round(p["a"], 6), "to_s": round(p["b"], 6), "from_frame": p["from_frame"], "native_fps": p["nfps"],
        "frames": n, "expected_frames": exp, "frames_ok": n is not None and abs(n - exp) <= tol,
        "profile": e["profile"], "lut": Path(p["lut"]).name if p["lut"] else None, "look": p["look_rel"],
        "fix": p["fix"], "levels": levels, "audio": p["audio"], "hwaccel": hw, "timeline_hash": p["timeline_hash"],
        "encoded": datetime.now().isoformat(timespec="seconds"), "seconds": round(time.time() - t0, 1),
    }


def status_of(p, man, out_dir, want):
    m = man.get(p["id"])
    f = out_dir / ("%s.mp4" % p["id"])
    if not m or not f.exists():
        return "missing"
    if m.get("hash") != p["hash"]:
        return "stale"
    if m.get("quality") == want or m.get("quality") == "final":
        return "ok-" + m.get("quality")
    return "draft"


def write_manifest(cj, man, tl_ids, out_dir, title):
    cur = {k: v for k, v in man.items()}
    all_final = all(k in cur and cur[k].get("quality") == "final" and cur[k].get("current", True)
                    and cur[k].get("frames_ok") is not False and (out_dir / ("%s.mp4" % k)).exists() for k in tl_ids)
    data = {"generated": datetime.now().isoformat(timespec="seconds"), "project": title, "all_final": all_final,
            "clips": cur}
    tmp = cj.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, cj)
    return all_final


def main():
    ap = argparse.ArgumentParser(description="按 timeline.json 切调好色的片段")
    ap.add_argument("proj")
    ap.add_argument("--draft", action="store_true", help="960 宽、ultrafast、crf 23,分镜预演用")
    ap.add_argument("--only", help="只做这些镜号,逗号分隔")
    ap.add_argument("--force", action="store_true", help="参数没变也重切")
    ap.add_argument("--check", action="store_true", help="只检查,不转码;不是全部 final 且最新就退出码 1")
    ap.add_argument("--prune", action="store_true", help="删掉 timeline 里已经没有的片段文件")
    ap.add_argument("--no-queue", action="store_true")
    a = ap.parse_args()
    if not a.check:
        L.ensure_queued()

    proj = Path(a.proj).resolve()
    project = L.load_project(proj)
    tlf = proj / "src" / "timeline.json"
    if not tlf.exists():
        L.die("没有 %s:先跑 timeline.py 编译 edit.json" % tlf)
    tl = json.loads(tlf.read_text("utf-8"))
    fps = int(tl.get("fps") or project.get("fps") or 30)
    clips = tl.get("clips") or []
    if a.only:
        want_ids = set(a.only.split(","))
        clips_run = [c for c in clips if c["id"] in want_ids]
        missing = want_ids - {c["id"] for c in clips_run}
        if missing:
            L.die("timeline.json 里没有这些镜号:%s" % ", ".join(sorted(missing)))
    else:
        clips_run = clips
    L.set_cache_dir(proj / "work" / "cache")
    out_dir = proj / "public" / "clips"
    out_dir.mkdir(parents=True, exist_ok=True)
    cj = out_dir / "clips.json"
    man = json.loads(cj.read_text("utf-8")).get("clips", {}) if cj.exists() else {}
    footage = L.load_footage(proj)
    want = "draft" if a.draft else "final"
    tl_ids = [c["id"] for c in clips if c.get("src")]

    plans = []
    errors = []
    for c in clips:
        if not c.get("src"):  # 黑场之类没有素材的镜头不切
            continue
        try:
            plans.append(plan(proj, project, c, footage, a.draft, fps))
        except Exception as ex:  # noqa: BLE001
            errors.append("%s:%s" % (c.get("id"), ex))
    for err in errors:
        print("✗ " + err, file=sys.stderr)
    for p in plans:  # 标记清单里每条是否还是最新参数
        if p["id"] in man:
            man[p["id"]]["current"] = man[p["id"]].get("hash") == p["hash"]

    if a.check:
        bad = 0
        for p in plans:
            st = status_of(p, man, out_dir, "final")
            m = man.get(p["id"], {})
            note = ""
            if m and m.get("frames_ok") is False:
                note = "  ⚠️ 帧数 %s ≠ 预期 %s" % (m.get("frames"), m.get("expected_frames"))
            print("%-10s %-9s %s%s" % (p["id"], st, p["entry"].get("path", ""), note))
            bad += st != "ok-final" or (m.get("frames_ok") is False)
        ok = write_manifest(cj, man, tl_ids, out_dir, project.get("title"))
        print("\n%s:%d 条,%d 条不是最新的 final%s" % ("✓ 可以渲成片" if ok and not bad and not errors else "✗ 还不能渲成片",
                                                    len(plans), bad, ",%d 条有错" % len(errors) if errors else ""))
        sys.exit(0 if ok and not bad and not errors else 1)

    run_ids = {c["id"] for c in clips_run}
    todo = [p for p in plans if p["id"] in run_ids]
    t_all, m_all = time.time(), time.monotonic()
    done = skipped = 0
    handles = float(project.get("handles_s") or 0)
    # 「秒 / 帧」参考值:本次已切的同质量片段;不够两条时借清单里上次同质量的记录
    q = "draft" if a.draft else "final"
    hist = [m["seconds"] / max(1, m.get("frames") or 1) for m in man.values()
            if m.get("quality") == q and m.get("seconds") and m.get("frames") and not m.get("slept_s")]
    rates = []
    graded_warn, slow_list, slept_total = [], [], 0.0
    for i, p in enumerate(todo, 1):
        st = status_of(p, man, out_dir, want)
        if not a.force and st.startswith("ok-"):
            skipped += 1
            print("[%d/%d] %-8s 跳过(%s)" % (i, len(todo), p["id"], st[3:]), flush=True)
            continue
        print("[%d/%d] %-8s %s %.2f–%.2f s · %s%s%s …" % (
            i, len(todo), p["id"], p["entry"].get("path"), p["a"], p["b"], p["entry"]["profile"],
            " · fix %s" % json.dumps(p["fix"], ensure_ascii=False) if p["fix"] else "", " · 带声" if p["audio"] else ""),
            flush=True)
        t_w, t_m = time.time(), time.monotonic()
        try:
            m = encode(p, out_dir, a.draft, fps)
        except Exception as ex:  # noqa: BLE001
            errors.append("%s:%s" % (p["id"], ex))
            print("  ✗ %s" % ex, file=sys.stderr, flush=True)
            continue
        wall, mono = time.time() - t_w, time.monotonic() - t_m
        m["seconds"] = round(wall, 1)  # 含 levels 测量和数帧,和预期值同一口径
        slept = wall - mono if wall - mono > SLEEP_MIN_S else 0.0
        slept_total += slept
        nf = max(1, m.get("frames") or p["nframes"] or 1)
        ref = expected_rate(rates) or expected_rate(hist)
        exp_s = ref * nf if ref else None
        if slept:
            m["slept_s"] = round(slept, 1)
        if exp_s and wall > SLOW_FACTOR * exp_s and wall > SLOW_MIN_S:
            why = "系统休眠了约 %.0f s" % slept if slept else "单调时钟没停,可能是休眠 / 被挂起 / App Nap,或机器被别的任务占满"
            slow_list.append("%s %.0f s(预期约 %.0f s)" % (p["id"], wall, exp_s))
            m["slow"] = {"seconds": round(wall, 1), "expected_s": round(exp_s, 1), "slept_s": round(slept, 1)}
            print("  ⚠️ %s 用了 %.0f s,是预期(约 %.0f s)的 %.0f 倍:可能是休眠(%s)。queue.sh 默认 caffeinate -i;"
                  "合盖照样会睡" % (p["id"], wall, exp_s, wall / exp_s, why), flush=True)
        elif slept:
            print("  ⚠️ %s 期间系统休眠了约 %.0f s(ffmpeg 被挂起)" % (p["id"], slept), flush=True)
        else:
            rates.append(wall / nf)
        try:
            m["grade"], m["warn"] = grade_selfcheck(out_dir / ("%s.mp4" % p["id"]), m.get("frames") or p["nframes"], fps,
                                                    round(handles * p["nfps"]), p["fix"], p["look"])
        except Exception as ex:  # noqa: BLE001  自检失败不影响片段本身,但要说出来
            m["grade"], m["warn"] = None, ["调色自检没跑成:%s" % str(ex)[-200:]]
        if m["warn"]:
            graded_warn.append((p["id"], m["warn"]))
        m["current"] = True
        man[p["id"]] = m
        done += 1
        if not m["frames_ok"]:
            errors.append("%s:帧数 %s ≠ 预期 %s" % (p["id"], m["frames"], m["expected_frames"]))
        flag = "" if m["frames_ok"] else "  ⚠️ 帧数 %s ≠ 预期 %s" % (m["frames"], m["expected_frames"])
        print("  ✓ %s %s · %s 帧(预期 %s)· %s%s · 用时 %.1f s%s%s" % (
            m["file"], m["quality"], m["frames"], m["expected_frames"], "硬解" if m["hwaccel"] else "软解",
            " · levels p5 %.3f p95 %.3f" % (m["levels"]["p5"], m["levels"]["p95"]) if m.get("levels") else "",
            m["seconds"], " · 预期约 %.0f s" % exp_s if exp_s else "", flag), flush=True)
        if m.get("grade"):
            print("    调色后 p5 %.3f p50 %.3f p95 %.3f%s" % (m["grade"]["p5"], m["grade"]["p50"], m["grade"]["p95"],
                                                         "" if m["warn"] else " ✓"), flush=True)
        for w in m["warn"]:
            print("    ⚠️ " + w, flush=True)
        write_manifest(cj, man, tl_ids, out_dir, project.get("title"))

    if a.prune:
        keep = set(tl_ids)
        for f in out_dir.glob("*.mp4"):
            if f.stem not in keep and not f.name.endswith(".part.mp4"):
                f.unlink()
                print("  删掉不用的片段 %s" % f.name)
        for k in list(man):
            if k not in keep:
                del man[k]
    ok = write_manifest(cj, man, tl_ids, out_dir, project.get("title"))
    wall_all, mono_all = time.time() - t_all, time.monotonic() - m_all
    print("\n转了 %d 条,跳过 %d 条,%d 条出错,用时 %.0f s%s;%s" % (
        done, skipped, len(errors), wall_all,
        "(其中系统休眠约 %.0f s)" % (wall_all - mono_all) if wall_all - mono_all > SLEEP_MIN_S else "",
        "全部是最新的 final,可以渲成片" if ok else "还不是全部 final(成片前跑一次不带 --draft 的)"))
    if slow_list:
        print("⚠️ %d 条耗时异常(超过预期 %d 倍):%s。多半是休眠:别合盖,或确认 queue.sh 的 caffeinate 生效"
              % (len(slow_list), SLOW_FACTOR, ";".join(slow_list)))
    if graded_warn:
        print("⚠️ %d 条调色越线(只警告,清单 clips.json 的 warn 字段):" % len(graded_warn))
        for cid, ws in graded_warn:
            print("  %-10s %s" % (cid, ws[0]))
    for err in errors:
        print("✗ " + err, file=sys.stderr)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
