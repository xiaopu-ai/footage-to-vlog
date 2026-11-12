#!/usr/bin/env python3
"""render.py · 一条命令出可交付的成片（或节奏预演）。

为什么不直接 `npx remotion render`：两次实测都发现 Remotion 带声音的直出①音轨比画面晚约 40 ms（AAC 编码前置延迟没补偿），
②JPEG 帧渲出来是全范围 yuvj420p + BT.601 矩阵，③容器时长多 2 帧。这里把画面和声音分开做，最后用 ffmpeg 封装。

用法：
  python3 scripts/render.py <proj> [--preview 秒 | --frames a-b] [--scale S] [--name 名] [--allow-draft]
                            [--concurrency 4] [--reuse-video 文件] [--keep-intermediate] [--no-queue]
  例：python3 scripts/render.py <proj> --preview 15 --allow-draft   # 节奏预演：开场 15 秒（0-449 帧）、半分辨率 → out/preview.mp4
      python3 scripts/render.py <proj> --name 品牌燃剪-v1            # 整片 → out/品牌燃剪-v1.mp4 + 无声版（+ 纯人声版）
      python3 scripts/render.py <proj> --frames 0-299 --allow-draft  # 任意范围（闭区间，0-299 = 300 帧）
      python3 scripts/render.py <proj> --name 品牌燃剪-v1 --reuse-video out/品牌燃剪-v1.mp4   # 只改了声音：不重渲画面
  范围：--frames 和 Remotion 一样是闭区间；也接受半开写法 a:b。--preview S = 第 0 帧起 round(S × fps) 帧。
  默认名：--preview → preview；--frames → frames-a-b；整片 → film。默认 --scale：预演 0.5，其余 1。

做了什么（按顺序）：
  1. 片段检查：范围内用到的片段必须存在；没给 --allow-draft 时必须全是最新的 final（读 public/clips/clips.json）。
  2. 画面：套 scripts/queue.sh 跑 `npx remotion render src/index.ts Film … --muted --image-format=png --pixel-format=yuv420p
     --color-space=bt709 --gl=angle --concurrency=4 --timeout=1800000 --bundle-cache=false --frames=a-b --scale=S`。
  3. 帧数：必须等于范围帧数；多出来的帧裁掉（重编码一次，清单里记下），少了直接报错。像素格式不是 yuv420p / tv / BT.709 时转换一次并记下。
  4. 声音：mix.py 混出同范围的规范混音（32 位浮点，不削波）→ `alimiter=limit=0.74:latency=1`（latency=1 补偿前视延迟，
     不加整条声音晚 5 ms）→ 两遍 loudnorm 到 −14 LUFS / −1.5 dBTP → 24 bit 母带。
     限幅后「真峰值 − 响度」超过 12.3 dB 时 loudnorm 只能走动态模式（会改动态），所以先量：超了就按超出量把 limit 从 0.74 往下压，
     直到能纯线性增益（清单里记每次的 limit 和峰值响度比）。
  5. 封装：视频流直接复制，h264_metadata 写入 BT.709 的 primaries / transfer / matrix 和 video_full_range_flag=0；
     音频 AAC 320k / 48 kHz；+faststart。封完复测响度：真峰值超标就把限幅压低一点重来（最多 3 次）。
  6. 整片（不是预演 / 部分范围）另出无声版（-an -c:v copy）；timeline 里有原声（voice）的再出纯人声版（同画面，人声单独标准化）。
  7. 复核：成片音轨对规范混音做互相关（audio_sync.py），时移 > 5 ms 退出码 1。
  8. 清单 work/render/<名>.json：命令、参数、帧数、各文件 sha256、混音和成片响度、对齐结果。

产物：
  out/<名>.mp4、out/<名>-无声版.mp4（整片）、out/<名>-纯人声版.mp4（整片且有 voice）
  work/render/<名>.json（清单）、<名>.mix.wav（规范混音，32 位浮点，qa 的对齐基准）、<名>.master.wav（限幅 + 标准化后的母带）、
  <名>.remotion.log。Remotion 的原始静音视频封装后删掉（成片里是同一条视频流），--keep-intermediate 保留。

检查边界：只保证「文件规格对、声音和画面对齐、响度达标」。画面内容、卡点、黑场、二维码这些用 qa.py 查；好不好看要人看。
🔴 整个脚本是一个重任务：开头自动钻进 scripts/queue.sh 排队（FTV_LOCK / FTV_HELD_ENV 见 queue.sh）。
依赖：Node + 项目里装好的 Remotion、ffmpeg（要有 libx264、aac、alimiter、loudnorm、h264_metadata）、numpy。兼容 Python 3.9。
"""
import argparse
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import audio_sync as AS  # noqa: E402
import ftvlib as L  # noqa: E402
import mix as M  # noqa: E402

I_TARGET, TP_TARGET, LRA_TARGET = -14.0, -1.5, 11.0
LIMIT = 0.74            # 限幅起点（和原流程一致）
AAC_MARGIN = 0.2        # 给 AAC 编码抬峰留的余量（实测 +0.01 ~ +0.08 dB）
PLR_MAX = (TP_TARGET - I_TARGET) - AAC_MARGIN   # 12.3 dB：限幅后真峰值 − 响度不超过它，loudnorm 才能纯线性
MIN_LIMIT = 0.35
SYNC_MAX_MS = 5.0
VUI_BSF = "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0"
COMMANDS = []


def say(msg):
    print(msg, flush=True)


def die(msg, code=2):
    print("✗ " + msg, file=sys.stderr, flush=True)
    sys.exit(code)


def ff():
    return M.ffmpeg_bin()


def run(cmd, what, env=None, log=None):
    """跑一条命令并记进清单；失败抛异常。返回 stderr 文本。"""
    COMMANDS.append(shlex.join([str(c) for c in cmd]))
    r = subprocess.run([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    err = r.stderr.decode("utf-8", "replace")
    if log:
        Path(log).write_text(err, "utf-8")
    if r.returncode != 0:
        raise RuntimeError("%s失败（退出码 %d）：\n%s" % (what, r.returncode, "\n".join(err.strip().splitlines()[-12:])))
    return err


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ───────────────────────── 探测（qa.py 也用） ─────────────────────────

def count_frames(path):
    """数视频帧（流复制计包，快）；没有视频流返回 0。"""
    r = subprocess.run([ff(), "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:v:0?", "-c", "copy", "-f", "null", "-"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    m = re.findall(r"frame=\s*(\d+)", r.stderr.decode("utf-8", "replace"))
    return int(m[-1]) if m else 0


def _parse_color_tokens(tokens, out):
    known = {"bt709", "bt470bg", "smpte170m", "bt2020nc", "bt2020c", "bt2020", "smpte240m", "fcc", "gbr", "ycgco", "unknown",
             "bt470m", "film", "smpte428", "smpte431", "smpte432", "linear", "iec61966-2-1", "arib-std-b67", "smpte2084"}
    for t in tokens:
        t = t.strip()
        if t in ("tv", "pc"):
            out["color_range"] = t
        elif "/" in t:
            sp, pr, tr = (t.split("/") + ["", ""])[:3]
            out["color_space"], out["color_primaries"], out["color_transfer"] = sp, pr, tr
        elif t in known:
            out["color_space"] = out["color_primaries"] = out["color_transfer"] = t


def probe_video(path):
    """视频和音频的规格。优先用 ffprobe（环境变量 FFPROBE 或 PATH），没有就解析 `ffmpeg -i` 的输出。
    返回 {pix_fmt, color_range, color_space, color_primaries, color_transfer, width, height, fps, codec,
          has_audio, audio_codec, sample_rate, channels, duration_s, via}；拿不到的字段为 None。"""
    out = {k: None for k in ("codec", "pix_fmt", "color_range", "color_space", "color_primaries", "color_transfer",
                             "width", "height", "fps", "audio_codec", "sample_rate", "channels", "duration_s")}
    out["has_audio"] = False
    fp = os.environ.get("FFPROBE") or shutil.which("ffprobe")
    if fp:
        r = subprocess.run([fp, "-v", "error", "-show_entries",
                            "stream=codec_type,codec_name,pix_fmt,color_range,color_space,color_primaries,color_transfer,"
                            "width,height,r_frame_rate,sample_rate,channels:format=duration", "-of", "json", str(path)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode == 0:
            j = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
            for s in j.get("streams", []):
                if s.get("codec_type") == "video" and out["codec"] is None:
                    out["codec"] = s.get("codec_name")
                    out["pix_fmt"] = s.get("pix_fmt")
                    for k in ("color_range", "color_space", "color_primaries", "color_transfer"):
                        out[k] = s.get(k) or "unknown"
                    out["width"], out["height"] = s.get("width"), s.get("height")
                    if s.get("r_frame_rate") and "/" in s["r_frame_rate"]:
                        n, d = s["r_frame_rate"].split("/")
                        out["fps"] = round(float(n) / float(d), 3) if float(d) else None
                elif s.get("codec_type") == "audio" and not out["has_audio"]:
                    out["has_audio"] = True
                    out["audio_codec"] = s.get("codec_name")
                    out["sample_rate"] = int(s["sample_rate"]) if s.get("sample_rate") else None
                    out["channels"] = s.get("channels")
            d = (j.get("format") or {}).get("duration")
            out["duration_s"] = float(d) if d else None
            out["via"] = "ffprobe"
            return out
    r = subprocess.run([ff(), "-hide_banner", "-nostdin", "-i", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    txt = r.stderr.decode("utf-8", "replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", txt)
    if m:
        out["duration_s"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"Stream #[^\n]*?Video:\s*([\w-]+)[^\n]*?,\s*([a-z0-9_]+)(?:\(([^)]*)\))?,\s*(\d+)x(\d+)", txt)
    if m:
        out["codec"], out["pix_fmt"] = m.group(1), m.group(2)
        for k in ("color_range", "color_space", "color_primaries", "color_transfer"):
            out[k] = "unknown"
        _parse_color_tokens((m.group(3) or "").split(","), out)
        out["width"], out["height"] = int(m.group(4)), int(m.group(5))
        mf = re.search(r"Video:[^\n]*?,\s*([\d.]+)\s*fps", txt)
        out["fps"] = float(mf.group(1)) if mf else None
    m = re.search(r"Stream #[^\n]*?Audio:\s*([\w-]+)[^\n]*?,\s*(\d+)\s*Hz,\s*([^,\n]+)", txt)
    if m:
        out["has_audio"] = True
        out["audio_codec"], out["sample_rate"] = m.group(1), int(m.group(2))
        lay = m.group(3).strip()
        out["channels"] = {"mono": 1, "stereo": 2}.get(lay, None)
    out["via"] = "ffmpeg -i"
    return out


def color_ok(p):
    """yuv420p + tv + BT.709 三项（matrix / primaries / transfer）齐全"""
    bad = []
    if p.get("pix_fmt") != "yuv420p":
        bad.append("像素格式 %s（要 yuv420p）" % p.get("pix_fmt"))
    if p.get("color_range") != "tv":
        bad.append("范围 %s（要 tv）" % p.get("color_range"))
    for k, nm in (("color_space", "matrix"), ("color_primaries", "primaries"), ("color_transfer", "transfer")):
        if p.get(k) != "bt709":
            bad.append("%s %s（要 bt709）" % (nm, p.get(k)))
    return bad


def loud_measure(path, pre=None, I=I_TARGET, TP=TP_TARGET, LRA=LRA_TARGET):
    """loudnorm 第一遍的测量 JSON（input_i / input_tp / input_lra / input_thresh / target_offset）。pre = 前置滤镜。"""
    af = "loudnorm=I=%g:TP=%g:LRA=%g:print_format=json" % (I, TP, LRA)
    if pre:
        af = pre + "," + af
    cmd = [ff(), "-hide_banner", "-nostdin", "-nostats", "-i", str(path), "-vn", "-map", "0:a:0", "-af", af, "-f", "null", "-"]
    COMMANDS.append(shlex.join(cmd))
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    txt = r.stderr.decode("utf-8", "replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", txt, re.S)
    if r.returncode != 0 or not m:
        raise RuntimeError("响度测量失败：%s\n%s" % (path, "\n".join(txt.strip().splitlines()[-8:])))
    j = json.loads(m.group(0))
    return {k: (float(v) if re.match(r"^-?[\d.]+$", str(v)) else v) for k, v in j.items()}


# ───────────────────────── 片段检查 ─────────────────────────

def check_clips(proj, tl, a, b):
    """返回 (缺失列表, 非 final 列表, 说明)。只看可见区间和范围有交集的镜头。"""
    shots = [s for s in tl.get("shots") or [] if s.get("clip") and s.get("from", 0) < b and s.get("to", 0) > a]
    cj = Path(proj) / "public" / "clips" / "clips.json"
    man = {}
    if cj.exists():
        try:
            man = (json.loads(cj.read_text("utf-8")) or {}).get("clips") or {}
        except ValueError:
            man = {}
    by_file = {v.get("file"): v for v in man.values() if isinstance(v, dict)}
    tl_hash = {c.get("id"): c.get("hash") for c in tl.get("clips") or [] if isinstance(c, dict)}
    missing, not_final = [], []
    for s in shots:
        clip = str(s["clip"]).lstrip("/")
        if not (Path(proj) / "public" / clip).exists():
            missing.append("%s（%s）" % (s.get("id"), clip))
            continue
        e = by_file.get(clip) or man.get(s.get("id"))
        if not e:
            not_final.append("%s：clips.json 里没有记录" % s.get("id"))
        elif e.get("quality") != "final":
            not_final.append("%s：%s 质量" % (s.get("id"), e.get("quality")))
        elif tl_hash.get(s.get("id")) and e.get("hash") != tl_hash[s.get("id")]:
            not_final.append("%s：参数变了，片段过期" % s.get("id"))
        elif e.get("current") is False or e.get("frames_ok") is False:
            not_final.append("%s：片段过期或帧数不对" % s.get("id"))
    note = "clips.json 不存在" if not cj.exists() else ("clips.json all_final=%s" % bool((json.loads(cj.read_text("utf-8")) or {}).get("all_final")))
    return missing, not_final, note, len(shots)


# ───────────────────────── 声音：限幅 + 两遍响度 ─────────────────────────

def lim_filter(limit):
    # latency=1：补偿前视延迟（默认不补，整条声音会晚 attack 毫秒）；level 保持默认（限幅后自动拉回满幅，和原流程一致）
    return "alimiter=limit=%.4f:latency=1" % limit


def plan_limit(src, limit, auto=True):
    """从 limit 起量「限幅后的峰值响度比」PLR = 真峰值 − 响度。PLR ≤ PLR_MAX 时 loudnorm 才能纯线性增益（不改动态）；
    超了就按超出量把 limit 压低再量（PLR 和 limit 的 dB 基本 1:1），最多 8 次、最低 MIN_LIMIT。返回 (limit, 第一遍测量, 历史)。"""
    hist = []
    for _ in range(8):
        p1 = loud_measure(src, pre=lim_filter(limit))
        if not isinstance(p1.get("input_i"), float) or p1["input_i"] < -69:
            raise RuntimeError("声音几乎是静音（%s LUFS），不做标准化" % p1.get("input_i"))
        plr = p1["input_tp"] - p1["input_i"]
        hist.append({"limit": round(limit, 4), "input_i": p1["input_i"], "input_tp": p1["input_tp"], "plr": round(plr, 2)})
        if not auto or plr <= PLR_MAX or limit <= MIN_LIMIT:
            return limit, p1, hist
        limit = max(MIN_LIMIT, limit * 10 ** (-(plr - PLR_MAX + 0.05) / 20.0))
    return limit, p1, hist  # 8 次还没压到：用最后一次量过的（loudnorm 会退回动态模式，清单里有记录）


def make_master(src, dst, n_samples, limit, auto=True):
    """src（32 位浮点）→ 限幅 → 两遍 loudnorm → dst（24 bit，长度正好 n_samples）。返回记录 dict。"""
    limit, p1, hist = plan_limit(src, limit, auto)
    lim = lim_filter(limit)
    lra = LRA_TARGET if p1["input_lra"] <= LRA_TARGET else min(50.0, math.ceil(p1["input_lra"] + 1))
    ln = ("loudnorm=I=%g:TP=%g:LRA=%g:measured_I=%s:measured_TP=%s:measured_LRA=%s:measured_thresh=%s:offset=%s:"
          "linear=true:print_format=json" % (I_TARGET, TP_TARGET, lra, p1["input_i"], p1["input_tp"], p1["input_lra"],
                                             p1["input_thresh"], p1["target_offset"]))
    af = "%s,%s,aresample=%d,apad=whole_len=%d,atrim=end_sample=%d" % (lim, ln, M.SR, n_samples, n_samples)
    err = run([ff(), "-hide_banner", "-nostdin", "-nostats", "-y", "-i", src, "-vn", "-af", af, "-ar", str(M.SR), "-ac", "2",
               "-c:a", "pcm_s24le", dst], "响度标准化")
    m = re.findall(r"\{[^{}]*\"normalization_type\"[^{}]*\}", err, re.S)
    ntype = json.loads(m[-1]).get("normalization_type") if m else None
    p3 = loud_measure(dst)
    return {"limit": round(limit, 4), "limiter": lim, "limit_search": hist, "lra_target": lra, "pass1": p1,
            "normalization_type": ntype, "master": p3}


def mux(video, master, dst, reencode=None):
    """视频流复制（或按 reencode 重编码）+ BT.709 VUI；音频 AAC 320k。master 为 None 时只封画面。"""
    cmd = [ff(), "-hide_banner", "-nostdin", "-nostats", "-y", "-i", video]
    if master:
        cmd += ["-i", master]
    cmd += ["-map", "0:v:0"] + (["-map", "1:a:0"] if master else [])
    if reencode:
        cmd += reencode
    else:
        cmd += ["-c:v", "copy", "-bsf:v", VUI_BSF]
    if master:
        cmd += ["-c:a", "aac", "-b:a", "320k", "-ar", str(M.SR), "-ac", "2"]
    cmd += ["-movflags", "+faststart", dst]
    run(cmd, "封装")


def file_rec(path):
    return {"path": str(path), "sha256": sha256(path), "bytes": os.path.getsize(path)}


# ───────────────────────── 主流程 ─────────────────────────

def main():
    ap = argparse.ArgumentParser(description="渲染 + 规范混音 + 封装，一条命令出可交付文件")
    ap.add_argument("proj")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--preview", type=float, metavar="秒", help="节奏预演：从第 0 帧起这么多秒，默认半分辨率")
    g.add_argument("--frames", metavar="a-b", help="帧范围，闭区间（同 Remotion）；也接受半开 a:b")
    ap.add_argument("--scale", type=float, help="缩放，默认预演 0.5、其余 1")
    ap.add_argument("--name", help="输出名（out/<名>.mp4），默认 preview / frames-a-b / film")
    ap.add_argument("--allow-draft", action="store_true", help="允许 draft / 过期 / 未登记的片段（只给预演用）")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--reuse-video", metavar="文件", help="不重渲画面，用这个文件的视频流（比如只改了混音）")
    ap.add_argument("--keep-intermediate", action="store_true", help="保留 Remotion 原始静音视频")
    ap.add_argument("--no-queue", action="store_true", help="不排队（确认同一时间没有别的重任务）")
    a = ap.parse_args()

    proj = Path(a.proj).resolve()
    tl = M.load_timeline(proj)
    fps = float(tl.get("fps") or 30)
    total = int(round(M.num(tl.get("duration"), 1)))
    if a.preview is not None:
        if a.preview <= 0:
            die("--preview 要大于 0 秒")
        rng = (0, min(total, max(1, int(round(a.preview * fps)))))
        kind = "preview"
    elif a.frames:
        try:
            rng = M.parse_range(a.frames, total)
        except ValueError as e:
            die(str(e))
        if rng[0] >= total:
            die("范围起点 %d 超出成片（%d 帧）" % (rng[0], total))
        if rng[1] > total:
            say("⚠️ 范围末端超出成片，夹到第 %d 帧" % (total - 1))
            rng = (rng[0], total)
        kind = "film" if rng == (0, total) else "partial"
    else:
        rng, kind = (0, total), "film"
    fa, fb = rng
    nframes = fb - fa
    scale = a.scale if a.scale else (0.5 if kind == "preview" else 1.0)
    name = a.name or {"preview": "preview", "partial": "frames-%s" % M.range_label(fa, fb), "film": "film"}[kind]
    if "/" in name or name.startswith("."):
        die("--name 只写名字，不带路径")

    L.ensure_queued()  # 整个脚本是一个重任务
    env = dict(os.environ)
    if a.no_queue:
        env["FTV_NO_QUEUE"] = "1"

    out_dir, rdir = proj / "out", proj / "work" / "render"
    out_dir.mkdir(parents=True, exist_ok=True)
    rdir.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    rec = {"name": name, "created": datetime.now().isoformat(timespec="seconds"), "proj": str(proj), "kind": kind,
           "argv": sys.argv[1:], "timeline_sha256": sha256(proj / "src" / "timeline.json"), "style": tl.get("style"),
           "fps": fps, "range": {"from": fa, "to": fb, "frames_arg": M.range_label(fa, fb), "frames": nframes,
                                 "note": "to 不含（半开区间）；frames_arg 是 Remotion --frames 的闭区间写法"},
           "scale": scale, "commands": COMMANDS}
    say("▶ %s：成片帧 %s（%d 帧，%.2f 秒），scale %.2f → out/%s.mp4" % (kind, M.range_label(fa, fb), nframes, nframes / fps, scale, name))

    # 1 片段
    missing, not_final, note, nshots = check_clips(proj, tl, fa, fb)
    if missing:
        die("范围内缺片段，不能渲：%s\n  先跑 make_clips.py <proj>" % "；".join(missing))
    if not_final and not a.allow_draft:
        die("范围内有 %d 个片段不是最新的 final：\n  %s\n  先跑 make_clips.py <proj>（不带 --draft）；节奏预演可以加 --allow-draft"
            % (len(not_final), "\n  ".join(not_final)))
    rec["clips"] = {"shots_in_range": nshots, "not_final": not_final, "allow_draft": a.allow_draft, "note": note}
    if not_final:
        say("⚠️ 用了 %d 个非 final 片段（--allow-draft）：%s" % (len(not_final), "；".join(not_final[:6]) + ("…" if len(not_final) > 6 else "")))

    # 2 画面
    raw = rdir / ("%s.video.mp4" % name)
    if a.reuse_video:
        src_v = Path(a.reuse_video).resolve()
        if not src_v.exists():
            die("--reuse-video 文件不存在：%s" % src_v)
        if src_v == (out_dir / ("%s.mp4" % name)).resolve():  # 输出会覆盖它：先复制一份
            shutil.copy2(src_v, raw)
            src_v = raw
        rec["remotion"] = {"reused": str(a.reuse_video)}
        say("· 复用画面：%s" % a.reuse_video)
        video = src_v
    else:
        npx = shutil.which("npx")
        if not npx:
            die("找不到 npx（Node ≥ 18）")
        cmd = ["bash", str(HERE / "queue.sh"), npx, "remotion", "render", "src/index.ts", "Film", str(raw),
               "--muted", "--image-format=png", "--pixel-format=yuv420p", "--color-space=bt709", "--gl=angle",
               "--concurrency=%d" % a.concurrency, "--timeout=1800000", "--bundle-cache=false",
               "--frames=%s" % M.range_label(fa, fb), "--scale=%g" % scale]
        COMMANDS.append("cd %s && %s" % (shlex.quote(str(proj)), shlex.join(cmd)))
        log = rdir / ("%s.remotion.log" % name)
        say("· Remotion 渲画面（静音、PNG 帧、yuv420p、BT.709），日志 %s" % log)
        t0 = time.time()
        with open(log, "w", encoding="utf-8") as lf:
            p = subprocess.Popen(cmd, cwd=str(proj), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            last, prev = time.time(), ""
            for line in iter(p.stdout.readline, b""):
                s = line.decode("utf-8", "replace")
                lf.write(s)
                if re.search(r"(?i)error|✗|warn", s) or time.time() - last > 15:  # 进度每 15 秒打一行，报错全打
                    print("  " + s.rstrip()[:200], flush=True)
                    last = time.time()
                prev = s
            rc = p.wait()
            if prev.strip():
                print("  " + prev.rstrip()[:200], flush=True)
        if rc != 0 or not raw.exists():
            tail = log.read_text("utf-8", "replace").strip().splitlines()[-15:]
            die("Remotion 渲染失败（退出码 %d）：\n  %s" % (rc, "\n  ".join(tail)))
        rec["remotion"] = {"seconds": round(time.time() - t0, 1), "log": str(log)}
        try:
            rec["remotion"]["version"] = json.loads((proj / "node_modules" / "remotion" / "package.json").read_text("utf-8")).get("version")
        except (OSError, ValueError):
            pass
        video = raw

    # 3 帧数和像素格式
    n = count_frames(video)
    pv = probe_video(video)
    rec["video_raw"] = {"frames": n, "expected": nframes, "probe": pv}
    reencode = None
    if n < nframes:
        die("视频只有 %d 帧，少于范围的 %d 帧（渲染没完成？）" % (n, nframes))
    vf = []
    if pv.get("pix_fmt") in ("yuvj420p",) or pv.get("color_range") == "pc" or pv.get("color_space") not in ("bt709",):
        cm = "bt709" if pv.get("color_space") == "bt709" else "bt601"
        rng_in = "pc" if pv.get("pix_fmt") == "yuvj420p" or pv.get("color_range") == "pc" else "tv"
        vf = ["-vf", "scale=in_range=%s:out_range=tv:in_color_matrix=%s:out_color_matrix=bt709:flags=lanczos+accurate_rnd+full_chroma_int+full_chroma_inp,format=yuv420p" % (rng_in, cm)]
        rec["video_raw"]["converted"] = "%s %s %s → yuv420p tv bt709" % (pv.get("pix_fmt"), rng_in, cm)
        say("⚠️ 原始视频是 %s（%s，%s），转换成 yuv420p tv BT.709（重编码一次）" % (pv.get("pix_fmt"), rng_in, cm))
    if n > nframes or vf:
        if n > nframes:
            say("⚠️ 视频有 %d 帧，比范围多 %d 帧：裁掉（重编码一次）" % (n, n - nframes))
            rec["video_raw"]["trimmed"] = n - nframes
        reencode = vf + ["-frames:v", str(nframes), "-c:v", "libx264", "-preset", "slow", "-crf", "12", "-pix_fmt", "yuv420p",
                         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
                         "-bsf:v", VUI_BSF]

    # 4 声音
    say("· 规范混音（mix.py）……")
    try:
        x, minfo = M.mix_timeline(proj, tl, fa, fb, "all")
    except (ValueError, FileNotFoundError) as e:
        die(str(e))
    mixwav = rdir / ("%s.mix.wav" % name)
    M.write_wav(mixwav, x, M.SR, "32f")
    rec["mix"] = {"path": str(mixwav), "sha256": sha256(mixwav), "samples": len(x), "peak_dbfs": minfo["peak_dbfs"],
                  "tracks": len(minfo["tracks"]), "silent": minfo["silent"]}
    final = out_dir / ("%s.mp4" % name)
    tmp_final = out_dir / (".%s.tmp.mp4" % name)
    master = rdir / ("%s.master.wav" % name)
    failed = []
    if minfo["silent"]:
        say("⚠️ 这一段 timeline 里没有声音：只封画面，不做响度")
        mux(video, None, tmp_final, reencode)
        os.replace(tmp_final, final)
        rec["audio"] = None
    else:
        limit = LIMIT
        tries = []
        for attempt in range(3):
            say("· 限幅（从 %.3f 起）+ 两遍 loudnorm（目标 %g LUFS / %g dBTP）……" % (limit, I_TARGET, TP_TARGET))
            try:
                lr = make_master(mixwav, master, len(x), limit)
            except RuntimeError as e:
                die(str(e))
            limit = lr["limit"]
            say("  限幅 %.3f（峰值响度比 %.2f dB），loudnorm %s 模式" % (limit, lr["limit_search"][-1]["plr"], lr["normalization_type"]))
            say("· 封装：视频流复制 + BT.709 标签，音频 AAC 320k……" if not reencode else "· 封装：视频重编码 + BT.709 标签，音频 AAC 320k……")
            mux(video, master, tmp_final, reencode)
            fm = loud_measure(tmp_final)
            lr["final"] = fm
            tries.append(lr)
            excess = fm["input_tp"] - TP_TARGET
            say("  成片：%.2f LUFS，真峰值 %.2f dBTP" % (fm["input_i"], fm["input_tp"]))
            if excess <= 0 or attempt == 2:
                break
            limit = limit * 10 ** (-(excess + AAC_MARGIN) / 20.0)
            say("  真峰值超了 %.2f dB（AAC 编码抬峰）：限幅压到 %.3f 重来" % (excess, limit))
        os.replace(tmp_final, final)
        fm = tries[-1]["final"]
        rec["audio"] = {"master": file_rec(master), "tries": tries, "limit": limit,
                        "final_i": fm["input_i"], "final_tp": fm["input_tp"], "final_lra": fm["input_lra"],
                        "normalization_type": tries[-1]["normalization_type"]}
        if abs(fm["input_i"] - I_TARGET) > 0.5:
            failed.append("响度 %.2f LUFS 不在 %g ±0.5" % (fm["input_i"], I_TARGET))
        if fm["input_tp"] > TP_TARGET:
            failed.append("真峰值 %.2f dBTP > %g" % (fm["input_tp"], TP_TARGET))

    # 5 复核：帧数、色彩、音画对齐
    n_out = count_frames(final)
    po = probe_video(final)
    rec["video"] = {"frames": n_out, "expected": nframes, "probe": po, "color_problems": color_ok(po)}
    if n_out != nframes:
        failed.append("成片 %d 帧，应为 %d" % (n_out, nframes))
    if rec["video"]["color_problems"]:
        failed.append("色彩标签：" + "；".join(rec["video"]["color_problems"]))
    if rec["audio"]:
        sync = AS.measure(final, mixwav, max_ms=SYNC_MAX_MS)
        rec["audio_sync"] = sync
        say("  音画对齐：%+.2f ms，相关 %.4f（对规范混音）" % (sync.get("delay_ms", float("nan")), sync.get("corr", float("nan"))))
        if sync["status"] != "pass":
            failed.append("音画对齐：" + "；".join(sync.get("problems") or [sync.get("reason", "")]))

    outputs = {"main": file_rec(final)}
    # 6 无声版 / 纯人声版
    if kind == "film":
        silent = out_dir / ("%s-无声版.mp4" % name)
        run([ff(), "-hide_banner", "-nostdin", "-y", "-i", final, "-map", "0:v:0", "-c:v", "copy", "-an", "-movflags", "+faststart", silent], "出无声版")
        outputs["silent"] = file_rec(silent)
        if any(t["kind"] == "voice" for t in minfo["tracks"]):
            say("· 纯人声版……")
            vx, vinfo = M.mix_timeline(proj, tl, fa, fb, "voice")
            vmix = rdir / ("%s.voice-mix.wav" % name)
            M.write_wav(vmix, vx, M.SR, "32f")
            vmaster = rdir / ("%s.voice-master.wav" % name)
            voice_path = out_dir / ("%s-纯人声版.mp4" % name)
            try:
                vr = make_master(vmix, vmaster, len(vx), LIMIT)
                run([ff(), "-hide_banner", "-nostdin", "-y", "-i", final, "-i", vmaster, "-map", "0:v:0", "-map", "1:a:0",
                     "-c:v", "copy", "-c:a", "aac", "-b:a", "320k", "-ar", str(M.SR), "-ac", "2", "-movflags", "+faststart",
                     voice_path], "出纯人声版")
                vr["final"] = loud_measure(voice_path)
                outputs["voice"] = file_rec(voice_path)
                outputs["voice"]["loudness"] = vr
            except RuntimeError as e:
                say("⚠️ 纯人声版没出：%s" % e)
                outputs["voice"] = {"error": str(e)}
    rec["outputs"] = outputs
    if not a.keep_intermediate and not a.reuse_video and raw.exists():
        raw.unlink()
    elif a.reuse_video and raw.exists() and raw != Path(a.reuse_video).resolve() and not a.keep_intermediate:
        raw.unlink()
    rec["seconds"] = round(time.time() - t_start, 1)
    rec["status"] = "fail" if failed else "pass"
    rec["problems"] = failed
    man = rdir / ("%s.json" % name)
    man.write_text(json.dumps(rec, ensure_ascii=False, indent=1), "utf-8")

    say("\n%s out/%s.mp4：%d 帧（%.3f 秒），%s×%s，%s(%s, %s/%s/%s)" % (
        "✓" if not failed else "✗", name, n_out, n_out / fps, po.get("width"), po.get("height"), po.get("pix_fmt"),
        po.get("color_range"), po.get("color_space"), po.get("color_primaries"), po.get("color_transfer")))
    if rec["audio"]:
        say("  声音：%.2f LUFS / %.2f dBTP（%s），音画对齐 %+.2f ms" % (rec["audio"]["final_i"], rec["audio"]["final_tp"],
                                                                   rec["audio"]["normalization_type"], rec["audio_sync"].get("delay_ms", float("nan"))))
    for k, v in outputs.items():
        if k != "main" and "path" in v:
            say("  %s：%s" % ({"silent": "无声版", "voice": "纯人声版"}[k], os.path.relpath(v["path"], proj)))
    say("  清单：%s（%.0f 秒）" % (os.path.relpath(man, proj), rec["seconds"]))
    for f in failed:
        say("  ✗ " + f)
    say("下一步：python3 %s %s %s --kind %s" % (HERE / "qa.py", proj, final, "film" if kind == "film" else "preview"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
