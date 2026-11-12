#!/usr/bin/env python3
"""footage-to-vlog 共用库(不是命令行工具)。

inventory.py / grab.py / look.py / measure.py / make_clips.py 都 import 它,保证抽帧、量色、切片段走**同一条色彩链**:
  解码 → 缩放(显式 in_range / in_color_matrix)→ rgb48le → 技术 LUT(Log→Rec.709)→ 逐镜 fix(exposure / warm / levels)→ HALD 色表 → 输出

内容:找 ffmpeg / ffprobe;读素材信息(有 ffprobe 用 ffprobe,没有就解析 `ffmpeg -i` 的输出);读 Sony 内嵌的 NonRealTimeMeta XML;
判机型曲线(元数据 + 抽帧量码值);拼滤镜链;排队(scripts/queue.sh);macOS 4K HEVC 硬解自测。

只依赖标准库;numpy 在用到的函数里再 import。兼容 Python 3.9。不 import 任何项目代码。

环境变量:
  FFMPEG / FFPROBE   指定可执行文件(默认在 PATH 里找;没有 ffprobe 也能工作)
  FTV_THREADS        给每个 ffmpeg 加 `-filter_threads N` 和输出 `-threads N`(降温,拿时间换温度;默认不加)
  FTV_HWACCEL        auto(默认:macOS 上 4K HEVC 先自测 2 秒软硬解 PSNR,inf 才用 videotoolbox)/ off / on
  FTV_CACHE          缓存目录(存硬解自测结果)。不设时依次用:项目内(inventory 的 <out>/cache,其余脚本 <proj>/work/cache)
                     → 用户级 ~/.cache/footage-to-vlog → 都写不进就只存内存(本次运行有效)。
                     🔴 缓存写不进只提示、绝不让分析失败:实测沙箱拒写 ~/.cache 时,旧版让 43 条航拍全部分析失败、盘点却照样打印完成。
  FTV_LOCK / FTV_HELD_ENV / FTV_NO_QUEUE   排队相关,见 scripts/queue.sh 文件头
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL / "scripts"
LIB_VERSION = "3"  # 改了色彩链的拼法就加一,make_clips 会据此重切

VIDEO_EXT = {".mp4", ".mov", ".mxf", ".m4v", ".mts", ".m2ts", ".avi", ".mkv", ".webm", ".insv"}  # 不含 .LRV(大疆 / GoPro 的低码率代理)
DEFAULT_LUTS = {
    "sony-slog3": "luts/Slog3-S-Gamut3.Cine_To_s709_V200.cube",
    "dji-dlogm": "luts/DJI_Mavic_4_Pro_D-Log_M_to_Rec.709_V1.cube",
}
NO_LUT_PROFILES = {"rec709"}
DEFAULT_LEVELS_TARGET = (0.05, 0.90)  # fix.levels="auto":本镜 p5 / p95 拉到这里(project.json 的 levels_target 可改)
# 调色后(技术 LUT + fix + look 全链)亮度 p5 / p95 越过这两条线 = 暗部压死 / 高光削顶。
# 开着 levels 时越线,基本就是「levels 先拉满、强对比色表的 S 曲线再压一次」(DeepSeek 实测 p5 0.000–0.011、p95 0.958–0.985)。
CONFLICT_P5, CONFLICT_P95 = 0.02, 0.96
SAFE_LEVELS_TARGET = (0.06, 0.86)  # 强对比色表配 levels auto 时,给 S 曲线留余量的目标


# ───────────────────────── 基础 ─────────────────────────

def die(msg, code=2):
    print("✗ " + msg, file=sys.stderr)
    sys.exit(code)


def warn(msg):
    print("⚠️ " + msg, file=sys.stderr)


_FF = None


def ffmpeg():
    global _FF
    if _FF is None:
        _FF = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
        if not _FF:
            die("找不到 ffmpeg:装好后放进 PATH,或设环境变量 FFMPEG=/path/to/ffmpeg")
    return _FF


def ffprobe():
    p = os.environ.get("FFPROBE") or shutil.which("ffprobe")
    if p:
        return p
    sib = Path(ffmpeg()).with_name("ffprobe" + Path(ffmpeg()).suffix)
    return str(sib) if sib.exists() else None


def threads():
    n = os.environ.get("FTV_THREADS", "").strip()
    return n if n.isdigit() and int(n) > 0 else None


def ff_head():
    """每条 ffmpeg 命令的开头。"""
    cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-y"]
    if threads():
        cmd += ["-filter_threads", threads()]
    return cmd


def ff_out_threads():
    """放在输出文件名前面。"""
    return ["-threads", threads()] if threads() else []


def run(cmd, capture=False, check=True):
    r = subprocess.run(cmd, stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE)
    if check and r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError("ffmpeg 失败(退出码 %d):%s\n命令:%s" % (r.returncode, "\n".join(err[-8:]), " ".join(map(str, cmd))))
    return r


def ffesc(path):
    """滤镜参数里的文件路径:单引号包住,内部单引号转义;Windows 盘符的冒号要转义。"""
    p = Path(path).resolve().as_posix()
    p = p.replace("'", "'\\''")
    if re.match(r"^[A-Za-z]:/", p):
        p = p[0] + "\\:" + p[2:]
    return "'" + p + "'"


_SHA = {}


def sha1_file(path):
    path = str(path)
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime)
    if key not in _SHA:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        _SHA[key] = h.hexdigest()
    return _SHA[key]


def sha1_obj(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


_CACHE = {"project": None, "resolved": False, "dir": None, "why": ""}


def set_cache_dir(path, override=True):
    """指定项目内缓存目录(inventory:<out>/cache;其余:<proj>/work/cache)。override=False 时已经设过就不改。"""
    if _CACHE["project"] is not None and not override:
        return
    _CACHE.update(project=Path(path), resolved=False, dir=None, why="")


def writable_dir(d):
    """真的建目录、写一个临时文件再删掉。沙箱会拦写入但权限位照常,只看 os.access 不准。返回 (ok, 原因)。"""
    try:
        d = Path(d)
        d.mkdir(parents=True, exist_ok=True)
        t = d / (".ftv-write-test.%d" % os.getpid())
        t.write_bytes(b"")
        t.unlink()
        return True, ""
    except OSError as e:
        return False, "%s: %s" % (type(e).__name__, e.strerror or e)


def cache_dir():
    """可写的缓存目录,或 None(都写不进:调用方只存内存)。顺序:FTV_CACHE → 项目内 → 用户级。不抛异常。"""
    if _CACHE["resolved"]:
        return _CACHE["dir"]
    cands = []
    if os.environ.get("FTV_CACHE"):
        cands.append(("FTV_CACHE", Path(os.path.expanduser(os.environ["FTV_CACHE"]))))
    if _CACHE["project"] is not None:
        cands.append(("项目内", _CACHE["project"]))
    try:
        home = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "footage-to-vlog"
        cands.append(("用户级", home))
    except RuntimeError:  # 没有 HOME
        pass
    failed = []
    for label, d in cands:
        ok, why = writable_dir(d)
        if ok:
            if failed:
                warn("缓存目录 %s 写不进,改用%s缓存 %s" % ("、".join(failed), label, d))
            _CACHE.update(resolved=True, dir=d)
            return d
        failed.append("%s(%s:%s)" % (label, d, why))
    warn("缓存目录都写不进:%s。硬解自测结果只存内存(本次有效),分析照常进行,不会漏素材"
         % ("、".join(failed) or "没有候选"))
    _CACHE.update(resolved=True, dir=None, why="; ".join(failed))
    return None


def cache_desc():
    d = cache_dir()
    return str(d) if d else "只存内存(%s)" % (_CACHE["why"] or "没有可写目录")


# ───────────────────────── 排队 ─────────────────────────

def queue_held():
    if os.environ.get("FTV_QUEUE_HELD") == "1":
        return True
    alt = os.environ.get("FTV_HELD_ENV", "").strip()
    return bool(alt and os.environ.get(alt))


def ensure_queued():
    """重任务脚本开头调用:没在 queue.sh 里就自己钻进去重新执行(全机同一时间只跑一个重任务)。
    --no-queue 或 FTV_NO_QUEUE=1 跳过;没有 bash 时直接跑并提示。"""
    if "--no-queue" in sys.argv or os.environ.get("FTV_NO_QUEUE") == "1" or queue_held():
        return
    q = SCRIPTS / "queue.sh"
    bash = shutil.which("bash")
    if not bash or not q.exists():
        warn("没有 bash / queue.sh,不排队直接跑;请确认同一时间没有别的转码在跑")
        return
    sys.stdout.flush()
    os.execv(bash, [bash, str(q), sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:])


# ───────────────────────── 读素材信息 ─────────────────────────

NTSC = {23.98: "24000/1001", 23.976: "24000/1001", 29.97: "30000/1001", 47.95: "48000/1001", 59.94: "60000/1001",
        119.88: "120000/1001", 239.76: "240000/1001"}


def fps_rational(fps):
    if fps is None:
        return None
    for k, v in NTSC.items():
        if abs(fps - k) < 0.006:
            return v
    if abs(fps - round(fps)) < 1e-3:
        return "%d/1" % round(fps)
    f = Fraction(fps).limit_denominator(1001)
    return "%d/%d" % (f.numerator, f.denominator)


def _bit_depth(pix_fmt):
    m = re.search(r"p(\d{2})(le|be)$", pix_fmt or "") or re.search(r"(\d{2})(le|be)$", pix_fmt or "")
    if m and m.group(1) in ("09", "10", "12", "14", "16"):
        return int(m.group(1))
    return 8


def _split_top(s):
    """按顶层的 ", " 切,括号 / 方括号里的逗号不切。"""
    out, depth, cur = [], 0, ""
    i = 0
    while i < len(s):
        c = s[i]
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        if depth == 0 and s.startswith(", ", i):
            out.append(cur)
            cur = ""
            i += 2
            continue
        cur += c
        i += 1
    if cur:
        out.append(cur)
    return [x.strip() for x in out]


CSP_NAMES = {"bt709": "bt709", "bt470bg": "bt601", "smpte170m": "bt601", "bt601": "bt601", "bt2020nc": "bt2020", "bt2020c": "bt2020",
             "bt2020": "bt2020", "fcc": "fcc", "smpte240m": "smpte240m"}


def _probe_ffprobe(path, fp):
    r = subprocess.run([fp, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError("ffprobe 读不了:%s" % r.stderr.decode("utf-8", "replace")[-300:])
    j = json.loads(r.stdout.decode("utf-8", "replace"))
    fmt = j.get("format", {})
    v = None
    has_audio = False
    for s in j.get("streams", []):
        if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic") and v is None:
            v = s
        if s.get("codec_type") == "audio":
            has_audio = True
    if v is None:
        raise RuntimeError("没有视频流")
    fr = v.get("r_frame_rate") or v.get("avg_frame_rate") or "0/1"
    try:
        fps = float(Fraction(fr)) if Fraction(fr) else None
    except (ZeroDivisionError, ValueError):
        fps = None
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(round(float(sd["rotation"])))
    if not rot and v.get("tags", {}).get("rotate"):
        rot = -int(v["tags"]["rotate"])
    dur = float(fmt.get("duration") or v.get("duration") or 0)
    return {
        "duration": round(dur, 3), "fps": round(fps, 3) if fps else None,
        "fps_rational": fr if fr and fr != "0/0" else fps_rational(fps),
        "width": int(v.get("width", 0)), "height": int(v.get("height", 0)),
        "codec": v.get("codec_name", ""), "pix_fmt": v.get("pix_fmt", ""),
        "range": {"tv": "tv", "pc": "pc"}.get(v.get("color_range", ""), ""),
        "colorspace": v.get("color_space", "") or "", "primaries": v.get("color_primaries", "") or "",
        "transfer": v.get("color_transfer", "") or "", "rotation": rot, "has_audio": has_audio,
        "tags": {k.lower(): str(val) for k, val in (fmt.get("tags") or {}).items()},
        "vtags": {k.lower(): str(val) for k, val in (v.get("tags") or {}).items()},
        "probe": "ffprobe",
    }


def _probe_ffmpeg(path):
    r = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    txt = r.stderr.decode("utf-8", "replace")
    if "Input #0" not in txt:
        raise RuntimeError("ffmpeg 读不了:" + txt.strip().splitlines()[-1] if txt.strip() else "无输出")
    lines = txt.splitlines()
    info = {"tags": {}, "vtags": {}, "has_audio": False, "rotation": 0, "probe": "ffmpeg -i"}
    # 文件级元数据:Input #0 之后、第一个 Duration 之前缩进 4 格的 key : value
    i0 = next(i for i, ln in enumerate(lines) if ln.startswith("Input #0"))
    sec = None
    video_seen = False
    in_video = False
    for ln in lines[i0 + 1:]:
        ind = len(ln) - len(ln.lstrip(" "))
        s = ln.strip()
        m = re.match(r"Stream #0:\d+(?:\[0x[0-9a-fA-F]+\])?(?:\([^)]*\))?: (\w+): (.*)$", s)
        if m:
            kind, body = m.group(1), m.group(2)
            in_video = False
            sec = "stream"
            if kind == "Audio":
                info["has_audio"] = True
            if kind == "Video" and not video_seen and "(attached pic)" not in body:
                video_seen = in_video = True
                parts = _split_top(body)
                info["codec"] = parts[0].split()[0]
                pm = re.match(r"([0-9a-z_]+)(?:\((.*)\))?", parts[1] if len(parts) > 1 else "")
                info["pix_fmt"] = pm.group(1) if pm else ""
                rng, csp, pri, trc = "", "", "", ""
                if pm and pm.group(2):
                    for tok in [t.strip() for t in pm.group(2).split(",")]:
                        if tok in ("tv", "pc"):
                            rng = tok
                        elif "/" in tok:
                            a = tok.split("/")
                            csp, pri, trc = (a + ["", "", ""])[:3]
                        elif tok not in ("progressive", "top first", "bottom first", "top coded first (swapped)",
                                         "bottom coded first (swapped)") and re.match(r"^[a-z0-9-]+$", tok):
                            csp = pri = trc = tok
                info.update(range=rng, colorspace=csp if csp != "unknown" else "", primaries=pri if pri != "unknown" else "",
                            transfer=trc if trc != "unknown" else "")
                for p in parts[2:]:
                    wm = re.match(r"(\d+)x(\d+)", p)
                    if wm and "width" not in info:
                        info["width"], info["height"] = int(wm.group(1)), int(wm.group(2))
                    fm = re.match(r"([\d.]+)(k?) fps$", p)
                    if fm:
                        info["fps"] = float(fm.group(1)) * (1000 if fm.group(2) else 1)
                    tm = re.match(r"([\d.]+)(k?) tbr$", p)
                    if tm and "fps" not in info:
                        info["fps"] = float(tm.group(1)) * (1000 if tm.group(2) else 1)
            continue
        if s.startswith("Duration:"):
            sec = "duration"
            dm = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", s)
            if dm:
                info["duration"] = round(int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3)), 3)
            continue
        if s == "Metadata:" and sec is None and ind == 2:
            sec = "fmeta"
            continue
        if sec == "fmeta" and ind >= 4:
            km = re.match(r"([^:]+?)\s*: (.*)$", s)
            if km:
                info["tags"][km.group(1).strip().lower()] = km.group(2).strip()
            continue
        if in_video:
            rm = re.search(r"rotation of (-?[\d.]+) degrees", s)
            if rm:
                info["rotation"] = int(round(float(rm.group(1))))
            km = re.match(r"([^:]+?)\s*: (.*)$", s)
            if km and ind >= 6:
                info["vtags"][km.group(1).strip().lower()] = km.group(2).strip()
    if not video_seen:
        raise RuntimeError("没有视频流")
    info.setdefault("duration", 0.0)
    info.setdefault("width", 0)
    info.setdefault("height", 0)
    fps = info.get("fps")
    info["fps"] = round(fps, 3) if fps else None
    info["fps_rational"] = fps_rational(fps)
    return info


def probe(path):
    """返回 {duration, fps, fps_rational, width, height, codec, pix_fmt, bit_depth, range, colorspace, primaries,
    transfer, rotation, has_audio, tags, vtags, probe}。有 ffprobe 用 ffprobe,否则解析 `ffmpeg -i`。"""
    fp = ffprobe()
    info = None
    if fp:
        try:
            info = _probe_ffprobe(path, fp)
        except Exception as e:  # noqa: BLE001  ffprobe 坏了就退回 ffmpeg -i
            warn("ffprobe 失败,改用 ffmpeg -i:%s" % e)
    if info is None:
        info = _probe_ffmpeg(path)
    info["bit_depth"] = _bit_depth(info.get("pix_fmt"))
    return info


def sony_nrt(path):
    """Sony XAVC 文件内嵌的 NonRealTimeMeta XML(一般在文件尾部):机型、Log 曲线、色域、编码矩阵、拍摄时间。没有就返回 {}。"""
    size = os.path.getsize(path)
    buf = b""
    with open(path, "rb") as f:
        for n in (256 << 10, 4 << 20):
            f.seek(max(0, size - n))
            tail = f.read()
            if b"<NonRealTimeMeta" in tail:
                buf = tail
                break
        if not buf:
            f.seek(0)
            head = f.read(4 << 20)
            if b"<NonRealTimeMeta" in head:
                buf = head
    if not buf:
        return {}
    a = buf.find(b"<NonRealTimeMeta")
    b = buf.find(b"</NonRealTimeMeta>", a)
    x = buf[a:b if b > 0 else a + 20000].decode("utf-8", "replace")

    def item(name):
        m = re.search(r'name="%s"\s+value="([^"]*)"' % name, x)
        return m.group(1) if m else ""

    def attr(tag, at):
        m = re.search(r"<%s\b[^>]*\b%s=\"([^\"]*)\"" % (tag, at), x)
        return m.group(1) if m else ""

    return {
        "gamma": item("CaptureGammaEquation"), "primaries": item("CaptureColorPrimaries"),
        "coding": item("CodingEquations"), "make": attr("Device", "manufacturer"), "model": attr("Device", "modelName"),
        "created": attr("CreationDate", "value"), "capture_fps": attr("VideoFrame", "captureFps"),
        "format_fps": attr("VideoFrame", "formatFps"),
    }


def make_model(info, nrt):
    if nrt.get("model"):
        return nrt.get("make") or "Sony", nrt["model"]
    t = info.get("tags", {})
    for mk, md in (("com.apple.quicktime.make", "com.apple.quicktime.model"), ("make", "model"),
                   ("com.android.manufacturer", "com.android.model")):
        if t.get(mk) or t.get(md):
            return t.get(mk, ""), t.get(md, "")
    enc = t.get("encoder", "") or info.get("vtags", {}).get("encoder", "")
    if enc.upper().startswith("DJI"):
        return "DJI", enc[3:].strip()
    if "gopro" in (info.get("vtags", {}).get("handler_name", "") + enc).lower():
        return "GoPro", ""
    if t.get("major_brand", "").upper().startswith("XAVC"):
        return "Sony", ""
    return "", ""


def created_iso(info, nrt, name=""):
    """拍摄时间。Sony 内嵌元数据自带时区,直接用;文件名里有 14 位本地时间(大疆 DJI_20250101102513_…)时,
    用它和 UTC 的 creation_time 反推时区;都没有就给 UTC(带 Z)。不按运行这台电脑的时区换算。"""
    if nrt.get("created"):
        return nrt["created"]
    ct = info.get("tags", {}).get("creation_time") or info.get("vtags", {}).get("creation_time")
    if not ct:
        return ""
    try:
        utc = datetime.strptime(ct.replace("Z", "").split(".")[0], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return ct
    m = re.search(r"(?<!\d)(20\d{12})(?!\d)", name or "")
    if m:
        try:
            loc = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
            off = (loc - utc.replace(tzinfo=None)).total_seconds()
            q = round(off / 900) * 900
            if abs(off - q) <= 120 and abs(q) <= 14 * 3600:
                return utc.astimezone(timezone(timedelta(seconds=q))).isoformat(timespec="seconds")
        except ValueError:
            pass
    return utc.isoformat(timespec="seconds").replace("+00:00", "Z")


def matrix_of(info, nrt):
    csp = CSP_NAMES.get(info.get("colorspace", ""))
    if csp:
        return csp
    if (nrt.get("coding") or "").lower().startswith("rec709"):
        return "bt709"
    if (nrt.get("coding") or "").lower().startswith("rec2020"):
        return "bt2020"
    return "bt709" if max(info.get("width", 0), info.get("height", 0)) >= 1280 else "bt601"


def range_of(info):
    if info.get("range") in ("tv", "pc"):
        return info["range"]
    return "pc" if (info.get("pix_fmt") or "").startswith("yuvj") else "tv"


# ───────────────────────── 抽帧量码值 / 判机型曲线 ─────────────────────────

def hw_signature(info):
    return "|".join(str(x) for x in (info.get("codec"), info.get("pix_fmt"), info.get("width"), info.get("height"),
                                     info.get("make", ""), info.get("model", "")))


def hw_candidate(info):
    mode = os.environ.get("FTV_HWACCEL", "auto").lower()
    if mode == "off":
        return False
    if mode == "on":
        return True
    return sys.platform == "darwin" and info.get("codec") == "hevc" and max(info.get("width", 0), info.get("height", 0)) >= 3840


def _ff_version():
    r = subprocess.run([ffmpeg(), "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return (r.stdout.decode("utf-8", "replace").splitlines() or [""])[0]


_HW_MEM = {}


def use_hwaccel(path, entry, lut_path=None):
    """macOS 4K HEVC:同一种格式第一次遇到时测 2 秒软解 / 硬解的 PSNR(过同一条技术还原链),inf 才用硬解;
    结果存进缓存目录的 hwaccel.json(写不进就只存内存)。缓存读写出任何错都不影响分析。"""
    if not hw_candidate(entry):
        return False
    if os.environ.get("FTV_HWACCEL", "auto").lower() == "on":
        return True
    key = hw_signature(entry) + "|" + _ff_version()
    if key in _HW_MEM:
        return _HW_MEM[key]["ok"]
    cd = cache_dir()
    cf = cd / "hwaccel.json" if cd else None
    cache = {}
    if cf is not None:
        try:
            cache = json.loads(cf.read_text("utf-8"))
        except (OSError, ValueError):
            cache = {}
    if key in cache and isinstance(cache[key], dict) and "ok" in cache[key]:
        _HW_MEM[key] = cache[key]
        return cache[key]["ok"]
    t = max(0.0, min(10.0, (entry.get("duration") or 4) / 2 - 1))
    ch = tech_chain(entry, 1920, 1080, lut_path) + ",format=rgb24"
    cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-v", "info",
           "-hwaccel", "videotoolbox", "-ss", "%.3f" % t, "-t", "2", "-i", str(path),
           "-ss", "%.3f" % t, "-t", "2", "-i", str(path),
           "-filter_complex", "[0:v]%s[a];[1:v]%s[b];[a][b]psnr" % (ch, ch), "-f", "null", "-"]
    t0 = time.time()
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    txt = r.stderr.decode("utf-8", "replace")
    m = re.findall(r"PSNR .*?average:(\S+)", txt)
    psnr = m[-1] if m else None
    ok = r.returncode == 0 and psnr == "inf"
    cache[key] = {"ok": ok, "psnr": psnr, "tested": datetime.now().isoformat(timespec="seconds"), "file": str(path),
                  "seconds": round(time.time() - t0, 1)}
    _HW_MEM[key] = cache[key]
    if cf is not None:
        try:
            tmp = cf.with_suffix(".json.%d.tmp" % os.getpid())
            tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=1), "utf-8")
            os.replace(tmp, cf)
        except OSError as e:
            warn("硬解自测结果写不进缓存 %s(%s),只存内存" % (cf, e))
    print("  [硬解自测] %s → PSNR %s → %s(%.1f s)" % (Path(path).name, psnr, "用 videotoolbox" if ok else "用软解",
                                                     time.time() - t0), flush=True)
    return ok


def hw_args(on):
    return ["-hwaccel", "videotoolbox"] if on else []


def luma_codes(path, t, hw=False, w=480, h=270):
    """抽一帧,直接取亮度平面的原始码值(不做范围 / 矩阵转换),按全码值归一到 0–1。返回 numpy 数组。"""
    import numpy as np
    cmd = ff_head() + ["-v", "error"] + hw_args(hw) + ["-ss", "%.3f" % t, "-i", str(path), "-frames:v", "1", "-an",
                                                        "-vf", "extractplanes=y,scale=%d:%d:flags=area,format=gray16le" % (w, h),
                                                        "-f", "rawvideo"] + ff_out_threads() + ["-"]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0 or len(r.stdout) < w * h * 2:
        if hw:
            return luma_codes(path, t, False, w, h)
        raise RuntimeError("抽帧失败 %s @%.2fs:%s" % (path, t, r.stderr.decode("utf-8", "replace")[-200:]))
    return np.frombuffer(r.stdout[: w * h * 2], np.uint16).astype(np.float64) / 65535.0


def code_stats(arrs):
    """多帧码值 → {p1 取最小、p99 取最大、其余取中位}。"""
    import numpy as np
    ps = [np.percentile(a, [1, 5, 50, 95, 99]) for a in arrs]
    P = np.array(ps)
    return {"p1": round(float(P[:, 0].min()), 3), "p5": round(float(np.median(P[:, 1])), 3),
            "p50": round(float(np.median(P[:, 2])), 3), "p95": round(float(np.median(P[:, 3])), 3),
            "p99": round(float(P[:, 4].max()), 3), "frames": len(arrs)}


def _loglike(s, strict=True):
    span = s["p99"] - s["p1"]
    if strict:
        return s["p1"] >= 0.07 and s["p99"] <= 0.82 and span <= 0.60
    return s["p1"] >= 0.05 and s["p99"] <= 0.92 and span <= 0.66


def classify(info, nrt, stats):
    """→ (profile, reason, confidence)。profile 取值:sony-slog3 / dji-dlogm / rec709;
    认得出但没内置 LUT 的给 sony-slog2 / hlg / pq / unknown-log,make_clips 会要求在 project.json 的 luts 里补 LUT 或用 profiles 覆盖。"""
    make, model = info.get("make", ""), info.get("model", "")
    who = (make + " " + model).strip() or "未知机型"
    st = "中位码值 %.2f,p1–p99 %.2f–%.2f" % (stats["p50"], stats["p1"], stats["p99"]) if stats else "未抽帧"
    trc = (info.get("transfer") or "").lower()
    if trc == "arib-std-b67":
        return "hlg", "%s,传输特性 HLG(arib-std-b67);没有内置 LUT,需要自备 HLG→709 转换" % who, "high"
    if trc == "smpte2084":
        return "pq", "%s,传输特性 PQ(smpte2084);没有内置 LUT" % who, "high"
    g = (nrt.get("gamma") or "").lower()
    if g:
        src = "内嵌元数据 CaptureGammaEquation=%s / %s" % (nrt.get("gamma"), nrt.get("primaries") or "?")
        if "s-log3" in g:
            extra = ""
            pr = (nrt.get("primaries") or "").lower()
            if pr and pr != "s-gamut3-cine":
                extra = ";⚠️ 内置 LUT 是 S-Gamut3.Cine,这条是 %s,颜色会偏,换对应 LUT" % nrt.get("primaries")
            return "sony-slog3", "%s %s;%s%s" % (who, src, st, extra), "high"
        if "s-log2" in g:
            return "sony-slog2", "%s %s;没有内置 S-Log2 LUT" % (who, src), "high"
        if "hlg" in g:
            return "hlg", "%s %s;没有内置 HLG LUT" % (who, src), "high"
        if any(k in g for k in ("rec709", "709", "s-cinetone", "still", "cine")):
            return "rec709", "%s %s(非 Log);%s" % (who, src, st), "high"
    if not stats:
        return "rec709", "%s,没抽到帧,按 rec709;请人工确认" % who, "low"
    if make.upper() == "DJI":
        if info.get("bit_depth", 8) >= 10 and _loglike(stats, strict=False):
            return ("dji-dlogm", "DJI %s %d-bit,%s(Log 特征:黑位抬高、高光压缩);元数据分不出 D-Log / D-Log M,"
                    "按 D-Log M(Mavic 4 Pro 实测);内置 LUT 是 Mavic 4 Pro 的,别的机型请在 project.json 换 LUT"
                    % (model or "?", info.get("bit_depth", 8), st), "medium")
        return "rec709", "DJI %s %d-bit,%s(反差正常,不像 Log)" % (model or "?", info.get("bit_depth", 8), st), "medium"
    if make.lower() == "sony":
        if _loglike(stats, strict=True):
            return "sony-slog3", "Sony %s 无内嵌曲线元数据,%s(Log 特征)→ 按 S-Log3;请人工确认" % (model or "?", st), "medium"
        return "rec709", "Sony %s 无内嵌曲线元数据,%s(反差正常)" % (model or "?", st), "medium"
    if info.get("bit_depth", 8) >= 10 and _loglike(stats, strict=True):
        return "unknown-log", "%s %d-bit,%s(像 Log 但机型不认识)→ 请在 project.json 的 profiles / luts 指定" % (
            who, info.get("bit_depth", 8), st), "low"
    span = stats["p99"] - stats["p1"]
    return "rec709", "%s,%s" % (who, st), "medium" if span > 0.66 else "low"


def analyze(path, rel=None, frames=None):
    """读一条素材:元数据 + 抽帧量码值 + 判曲线。返回 footage.json 的一项(不含 contact)。"""
    path = Path(path)
    info = probe(path)
    nrt = sony_nrt(path) if path.suffix.lower() in (".mp4", ".mxf", ".mov") else {}
    info["make"], info["model"] = make_model(info, nrt)
    if nrt.get("capture_fps") and not info.get("fps"):
        info["fps"] = float(re.sub(r"[^\d.]", "", nrt["capture_fps"]) or 0) or None
    d = info.get("duration") or 0
    if frames is None:
        frames = 1 if nrt.get("gamma") else 3
    ts = [d * 0.5] if frames == 1 else [d * k for k in (0.25, 0.5, 0.75)][:frames]
    hw = hw_candidate(info) and use_hwaccel(path, dict(info, range=range_of(info), matrix=matrix_of(info, nrt), profile="rec709"))
    arrs = []
    errs = []
    for t in ts:
        try:
            arrs.append(luma_codes(path, max(0.0, min(t, d - 0.1)), hw))
        except RuntimeError as e:
            errs.append(str(e))
            warn(str(e))
    stats = code_stats(arrs) if arrs else None
    trc = (info.get("transfer") or "").lower()
    if stats is None and not nrt.get("gamma") and trc not in ("arib-std-b67", "smpte2084"):
        # 判曲线要靠码值:一帧都没抽到就不能瞎猜成 rec709(Log 素材会被当成正常片子),按失败处理,让盘点对账抓到
        raise RuntimeError("抽帧量码值全部失败(%d 帧),无法判断曲线:%s" % (len(ts), (errs[-1] if errs else "无输出")[-300:]))
    profile, reason, conf = classify(info, nrt, stats)
    st = os.stat(path)
    e = {
        "path": rel if rel is not None else str(path),
        "duration": info.get("duration"), "fps": info.get("fps"), "fps_rational": info.get("fps_rational"),
        "width": info.get("width"), "height": info.get("height"), "rotation": info.get("rotation", 0),
        "codec": info.get("codec"), "pix_fmt": info.get("pix_fmt"), "bit_depth": info.get("bit_depth"),
        "range": range_of(info), "matrix": matrix_of(info, nrt), "transfer": info.get("transfer") or "",
        "make": info.get("make", ""), "model": info.get("model", ""),
        "profile": profile, "profile_reason": reason, "profile_confidence": conf,
        "codes": stats, "has_audio": bool(info.get("has_audio")), "created": created_iso(info, nrt, path.name),
        "size": st.st_size, "mtime": int(st.st_mtime), "probe": info.get("probe"),
    }
    if nrt.get("gamma"):
        e["sony_meta"] = {k: nrt[k] for k in ("gamma", "primaries", "coding", "capture_fps") if nrt.get(k)}
    return e


# ───────────────────────── 项目 / 素材索引 ─────────────────────────

def load_project(proj):
    proj = Path(proj)
    pj = proj / "project.json"
    p = json.loads(pj.read_text("utf-8")) if pj.exists() else {}
    p.setdefault("fps", 30)
    p.setdefault("width", 1920)
    p.setdefault("height", 1080)
    p.setdefault("luts", {})
    p.setdefault("profiles", {})
    p.setdefault("look", None)
    p.setdefault("levels_target", list(DEFAULT_LEVELS_TARGET))
    return p


def load_footage(proj):
    """<proj>/work/footage.json → {相对路径: 项}。没有就返回 {}。"""
    f = Path(proj) / "work" / "footage.json"
    if not f.exists():
        return {}
    data = json.loads(f.read_text("utf-8"))
    items = data["items"] if isinstance(data, dict) else data
    return {it["path"]: it for it in items}


def resolve_lut(proj, project, profile):
    """profile → LUT 绝对路径;rec709 返回 None;找不到就报错。相对路径先找 <proj>/,再找 <skill>/assets/。"""
    if profile in NO_LUT_PROFILES:
        return None
    rel = (project.get("luts") or {}).get(profile) or DEFAULT_LUTS.get(profile)
    if not rel:
        raise RuntimeError("机型曲线 %s 没有 LUT:在 project.json 的 luts 里加 \"%s\": \"luts/xxx.cube\",或用 profiles 改判" % (profile, profile))
    cands = [Path(rel)] if os.path.isabs(rel) else []
    if proj:
        cands.append(Path(proj) / rel)
    cands += [SKILL / "assets" / rel, SKILL / "assets" / "luts" / Path(rel).name]
    for c in cands:
        if c.exists():
            return str(c.resolve())
    raise RuntimeError("找不到 LUT 文件 %s(找过:%s)" % (rel, ", ".join(str(c) for c in cands)))


def resolve_src(project, src, root=None):
    if os.path.isabs(src):
        return Path(src)
    base = root or project.get("footage_root")
    if not base:
        raise RuntimeError("相对路径 %s 需要 footage_root(project.json)或 --root" % src)
    return Path(os.path.expanduser(base)) / src


COLOR_KEYS = ("range", "matrix", "bit_depth", "width", "height", "fps", "duration", "codec", "pix_fmt")
_ENTRY_CACHE = {}


def source_entry(proj, project, src, root=None, footage=None, profile=None, force_profile=None):
    """拿一条素材的色彩参数(range / matrix / profile / 尺寸 / 帧率):先查 footage.json,没有就现场分析。
    profile 优先级:project.json 的 profiles(手动改判)> footage.json(inventory 实测)> 现场判断 > 参数 profile(timeline 里抄的)。
    footage.json 的项缺 range / matrix 等字段(手写的)时,现场读元数据补齐(不抽帧)。"""
    path = resolve_src(project, src, root)
    if not path.exists():
        raise RuntimeError("素材不存在:%s" % path)
    if proj:
        set_cache_dir(Path(proj) / "work" / "cache", override=False)  # 缓存默认放项目内
    footage = footage if footage is not None else (load_footage(proj) if proj else {})
    e = footage.get(src)
    if e is None:
        for k, v in footage.items():
            if Path(k).as_posix() == Path(src).as_posix():
                e = v
                break
    key = str(path)
    if e is None:
        if key not in _ENTRY_CACHE:
            _ENTRY_CACHE[key] = analyze(path, rel=src)
        e = _ENTRY_CACHE[key]
    elif any(e.get(k) in (None, "") for k in COLOR_KEYS):
        if key + "#meta" not in _ENTRY_CACHE:
            info = probe(path)
            nrt = sony_nrt(path) if path.suffix.lower() in (".mp4", ".mxf", ".mov") else {}
            info["make"], info["model"] = make_model(info, nrt)
            _ENTRY_CACHE[key + "#meta"] = dict(info, range=range_of(info), matrix=matrix_of(info, nrt))
        meta = _ENTRY_CACHE[key + "#meta"]
        e = dict(e)
        for k in COLOR_KEYS + ("rotation", "make", "model", "has_audio"):
            if e.get(k) in (None, ""):
                e[k] = meta.get(k)
    e = dict(e)
    e.setdefault("path", src)
    over = (project.get("profiles") or {}).get(src)
    if over:
        e["profile"], e["profile_reason"] = over, "project.json profiles 手动指定"
    elif not e.get("profile") and profile:
        e["profile"] = profile
    if profile and not over and e.get("profile") != profile:
        e["profile_mismatch"] = profile  # timeline.json 里抄的和实测 / footage.json 不一致,调用方会提示
    if force_profile:  # 命令行显式指定(grab.py --profile)最优先
        e["profile"], e["profile_reason"] = force_profile, "命令行指定"
    e["_abs"] = str(path)
    return e


# ───────────────────────── 滤镜链 ─────────────────────────

def _fit(entry, W, H):
    """非 16:9 素材:比例差 ≤20% 填满裁边;更窄 / 更宽(竖拍)按高度放进画面,两边补黑。"""
    w, h = entry.get("width") or W, entry.get("height") or H
    if abs(entry.get("rotation", 0)) % 180 == 90:
        w, h = h, w
    r = (w / h) / (W / H)
    if abs(r - 1) < 0.01:
        return "", ""
    if abs(r - 1) <= 0.2:
        return ":force_original_aspect_ratio=increase", ",crop=%d:%d" % (W, H)
    return ":force_original_aspect_ratio=decrease", ",pad=%d:%d:(ow-iw)/2:(oh-ih)/2" % (W, H)


def tech_chain(entry, W, H, lut_path):
    """解码后 → W×H rgb48le(全范围)→ 技术 LUT。显式指定 in_range 和 in_color_matrix,不靠 ffmpeg 猜。"""
    opt, post = _fit(entry, W, H)
    rng = entry.get("range") or "tv"
    # 实测(ffmpeg 7.1 swscale):8-bit tv 范围 → RGB 默认走快速查表,整体偏暗约 2 级(Y=126 → 126,应为 128.1);
    # 加 accurate_rnd+full_chroma_int 降到 ≤1 级。8-bit pc(Sony yuvj420p)默认就是准的,加了反而差 1 级;10-bit 两种都 ≤1 级。
    flags = "bicubic+accurate_rnd+full_chroma_int" if (rng == "tv" and (entry.get("bit_depth") or 8) <= 8) else "bicubic"
    s = "scale=%d:%d:in_range=%s:out_range=pc:in_color_matrix=%s:flags=%s%s,format=rgb48le%s" % (
        W, H, rng, entry.get("matrix") or "bt709", flags, opt, post)
    if lut_path:
        s += ",lut3d=file=%s:interp=tetrahedral" % ffesc(lut_path)
    return s


def colorbalance(k):
    """逐镜冷暖:k>0 往冷(减红加蓝),阴影 / 高光按 0.6 倍跟随。"""
    return ("colorbalance=rs=%.4f:bs=%.4f:rm=%.4f:bm=%.4f:rh=%.4f:bh=%.4f"
            % (-k * 0.6, k * 0.6, -k, k, -k * 0.6, k * 0.6))


def _lutrgb(expr):
    return "lutrgb=r='%s':g='%s':b='%s'" % (expr, expr, expr)


def exposure_filter(ev, knee=0.85):
    """曝光(档):按 2.2 幂律,线性光乘 2^ev = 编码值乘 2^(ev/2.2);超过 knee 的高光软压,不直接削顶。"""
    g = 2 ** (ev / 2.2)
    return _lutrgb("st(0,val/maxval*%.5f);if(gt(ld(0),%.3f),%.3f+%.3f*(1-exp(-(ld(0)-%.3f)/%.3f)),ld(0))*maxval"
                   % (g, knee, knee, 1 - knee, knee, 1 - knee))


def levels_filter(p5, p95, lo, hi):
    """把本镜亮度 p5 / p95 线性拉到 lo / hi;hi 以上软压进 (hi, 1),lo 以下线性到 0。"""
    s = (hi - lo) / max(1e-3, p95 - p5)
    return _lutrgb("st(0,%.4f+(val/maxval-%.4f)*%.5f);if(gt(ld(0),%.4f),%.4f+%.4f*(1-exp(-(ld(0)-%.4f)/%.4f)),max(ld(0),0))*maxval"
                   % (lo, p5, s, hi, hi, 1 - hi, hi, 1 - hi))


def fix_filters(fix, levels=None, target=DEFAULT_LEVELS_TARGET):
    """fix = {"warm": k, "exposure": 档, "levels": "auto" | {"p5":..,"p95":..}};levels 是 auto 量出来的 {"p5","p95"}。
    顺序:曝光 → 冷暖 → levels。"""
    fix = fix or {}
    out = []
    ev = float(fix.get("exposure") or 0)
    if abs(ev) > 1e-4:
        out.append(exposure_filter(ev))
    k = float(fix.get("warm") or 0)
    if abs(k) > 1e-4:
        out.append(colorbalance(k))
    lv = fix.get("levels")
    if isinstance(lv, dict) and "p5" in lv and "p95" in lv:
        levels = lv
    if lv and levels:
        out.append(levels_filter(levels["p5"], levels["p95"], target[0], target[1]))
    return out


def grade_graph(entry, W, H, lut_path, fix=None, levels=None, target=DEFAULT_LEVELS_TARGET, look=None,
                pre="", out="rgb24"):
    """拼完整的 filter_complex:输入 [0:v](视频)和 [1:v](HALD 色表,有 look 时)→ 输出 [v]。
    pre:放在最前面的滤镜(比如 conform 用的 setpts);out:rgb24 / rgb48le / yuv420p(成片编码用,转回 tv range BT.709)。"""
    chain = ([pre] if pre else []) + [tech_chain(entry, W, H, lut_path)] + fix_filters(fix, levels, target)
    tail = {"rgb24": "format=rgb24", "rgb48le": "format=rgb48le",
            # setparams 把 BT.709 写进帧属性:新版 ffmpeg 编码器按帧属性打标签,只写 -color_primaries 时 Sony 素材出来是 unknown
            "yuv420p": "scale=out_range=tv:out_color_matrix=bt709,format=yuv420p,"
                       "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"}[out]
    if look:
        return "[0:v]%s[c];[c][1:v]haldclut,%s[v]" % (",".join(chain), tail)
    return "[0:v]%s,%s[v]" % (",".join(chain), tail)


def look_inputs(look):
    return ["-i", str(look)] if look else []


def rgb_frame(entry, t, W, H, lut_path, fix=None, levels=None, target=DEFAULT_LEVELS_TARGET, look=None, hw=False, depth=8):
    """按完整色彩链抽一帧,返回 H×W×3 的 numpy 数组(0–1)。"""
    import numpy as np
    pf = "rgb24" if depth == 8 else "rgb48le"
    g = grade_graph(entry, W, H, lut_path, fix, levels, target, look, out=pf)
    cmd = ff_head() + ["-v", "error"] + hw_args(hw) + ["-ss", "%.4f" % t, "-i", entry["_abs"]] + look_inputs(look) + [
        "-filter_complex", g, "-map", "[v]", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", pf] + ff_out_threads() + ["-"]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    n = W * H * 3 * (1 if depth == 8 else 2)
    if r.returncode != 0 or len(r.stdout) < n:
        if hw:
            return rgb_frame(entry, t, W, H, lut_path, fix, levels, target, look, False, depth)
        raise RuntimeError("抽帧失败 %s @%.2fs:%s" % (entry["_abs"], t, r.stderr.decode("utf-8", "replace")[-300:]))
    a = np.frombuffer(r.stdout[:n], np.uint8 if depth == 8 else np.uint16).reshape(H, W, 3)
    return a.astype(np.float64) / (255.0 if depth == 8 else 65535.0)


def luma709(rgb):
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def measure_levels(entry, a, b, lut_path, fix, hw=False):
    """fix.levels="auto" 用:在 [a, b] 的 15% / 50% / 85% 抽 3 帧(技术还原 + 曝光 + 冷暖之后、levels 之前),量亮度 p5 / p95。"""
    import numpy as np
    pre_fix = {k: v for k, v in (fix or {}).items() if k != "levels"}
    vals = []
    for k in (0.15, 0.5, 0.85):
        rgb = rgb_frame(entry, a + (b - a) * k, 480, 270, lut_path, pre_fix, hw=hw, depth=16)
        vals.append(np.percentile(luma709(rgb), [5, 95]))
    m = np.mean(vals, axis=0)
    return {"p5": round(float(m[0]), 4), "p95": round(float(m[1]), 4)}


def frame_stats(rgb):
    """亮度 p5 / p50 / p95(0–1)和中间调(亮度 60–190 / 255)像素的平均 R−B(0–255 刻度)。"""
    import numpy as np
    y = luma709(rgb)
    p5, p50, p95 = np.percentile(y, [5, 50, 95])
    m = (y >= 60 / 255) & (y <= 190 / 255)
    rb = float(((rgb[..., 0] - rgb[..., 2])[m]).mean() * 255) if m.any() else float("nan")
    return {"p5": round(float(p5), 4), "p50": round(float(p50), 4), "p95": round(float(p95), 4),
            "rb_mid": round(rb, 2), "mid_frac": round(float(m.mean()), 3)}


def levels_on(fix):
    lv = (fix or {}).get("levels")
    return lv == "auto" or isinstance(lv, dict)


def grade_check(p5, p95, fix=None, look=None):
    """调色后的 p5 / p95 越线检查。没越线返回 None;越线返回 {kind, msg, advice}:
    kind = "levels-conflict"(开着 levels:色表和 levels 叠压)/ "crush"(没开 levels:色表或曝光本身压死 / 削顶)。"""
    lo, hi = p5 < CONFLICT_P5, p95 > CONFLICT_P95
    if not (lo or hi):
        return None
    what = "、".join(x for x in ("暗部压死 p5 %.3f < %.2f" % (p5, CONFLICT_P5) if lo else "",
                                 "高光削顶 p95 %.3f > %.2f" % (p95, CONFLICT_P95) if hi else "") if x)
    if levels_on(fix):
        return {"kind": "levels-conflict", "msg": "色表和 levels 冲突:%s" % what,
                "advice": "去掉 fix.levels,改用 fix.exposure / fix.warm 逐镜对齐;或把 project.json 的 levels_target 收到 [%.2f, %.2f]"
                          % SAFE_LEVELS_TARGET}
    return {"kind": "crush", "msg": what,
            "advice": "色表%s太陡:降 fix.exposure,或换对比更缓的色表" % ("" if look else "(没套色表,是曝光 / 技术还原)")}


def file_frame_stats(path, times, W=480, H=270):
    """量已编码片段(make_clips 的输出:tv range BT.709 yuv420p)在若干秒处的亮度 / 冷暖。返回 [frame_stats…]。"""
    import numpy as np
    out = []
    for t in times:
        cmd = ff_head() + ["-v", "error", "-ss", "%.4f" % max(0.0, t), "-i", str(path), "-frames:v", "1", "-an",
                           "-vf", "scale=%d:%d:in_range=tv:out_range=pc:in_color_matrix=bt709:flags=bicubic+accurate_rnd+full_chroma_int,"
                                  "format=rgb48le" % (W, H),
                           "-f", "rawvideo", "-pix_fmt", "rgb48le"] + ff_out_threads() + ["-"]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        n = W * H * 6
        if r.returncode != 0 or len(r.stdout) < n:
            raise RuntimeError("抽帧失败 %s @%.2fs:%s" % (path, t, r.stderr.decode("utf-8", "replace")[-200:]))
        a = np.frombuffer(r.stdout[:n], np.uint16).reshape(H, W, 3).astype(np.float64) / 65535.0
        out.append(frame_stats(a))
    return out


# ───────────────────────── 字体(联系表 / 拼图标注)─────────────────────────

FONT_CANDS = [
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/STHeiti Light.ttc", "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
]


def find_font(size=16, proj=None):
    """找一个能显示中文的字体:FTV_FONT → 项目 fonts.sans → 系统常见中文字体 → fc-match → Pillow 默认(只有英文)。"""
    from PIL import ImageFont
    cands = [os.environ.get("FTV_FONT")]
    if proj:
        try:
            sans = (load_project(proj).get("fonts") or {}).get("sans")
            if sans:
                cands.append(str(Path(proj) / "public" / sans))
        except (OSError, ValueError):
            pass
    cands += FONT_CANDS
    if shutil.which("fc-match"):
        r = subprocess.run(["fc-match", "-f", "%{file}", ":lang=zh"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode == 0 and r.stdout:
            cands.append(r.stdout.decode("utf-8", "replace"))
    for c in cands:
        if c and os.path.exists(c):
            try:
                return ImageFont.truetype(c, size), True
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size), False
    except TypeError:
        return ImageFont.load_default(), False


def safe_text(s, cjk_ok):
    return s if cjk_ok else s.encode("ascii", "replace").decode("ascii")


def tc(t):
    """秒 → m:ss.s"""
    m, s = divmod(max(0.0, t), 60)
    return "%d:%04.1f" % (m, s)
