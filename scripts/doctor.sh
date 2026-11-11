#!/usr/bin/env bash
# doctor.sh · footage-to-vlog 环境自检：开工前跑一次，缺什么一眼看清。
#
# 用法：
#   scripts/doctor.sh            # 查工具链和 skill 自带的 LUT
#   scripts/doctor.sh <proj>     # 另外查项目：project.json、素材目录、LUT、色表、字体、Logo
# 产物：无；逐项打印 ✓ / ✗ / ⚠️。必需项（node ≥ 18、ffmpeg 及其 libx264 / lut3d / haldclut、python3 ≥ 3.9 及 numpy / Pillow / jieba、
#       LUT、项目里引用的文件）缺任何一项退出码 1；可选项（ffprobe、opencv、ELEVENLABS_API_KEY、硬解）只提示。
# 环境变量：FFMPEG / FFPROBE / NODE / PYTHON 可指定可执行文件。
# 🔴 只检查 ELEVENLABS_API_KEY 有没有设置，绝不打印它的值；也不读 .env 文件内容。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL="$(dirname "$HERE")"
PROJ="${1:-}"
fail=0
ok()   { printf '  ✓ %s\n' "$*"; }
bad()  { printf '  ✗ %s\n' "$*"; fail=1; }
note() { printf '  ⚠️ %s\n' "$*"; }

echo "== 工具链"
NODE_BIN="${NODE:-$(command -v node 2>/dev/null || true)}"
if [ -n "$NODE_BIN" ] && [ -x "$NODE_BIN" ]; then
  nv="$("$NODE_BIN" -v 2>/dev/null | sed 's/^v//')"; major="${nv%%.*}"
  if [ "${major:-0}" -ge 18 ] 2>/dev/null; then ok "node $nv（$NODE_BIN）"; else bad "node $nv 太旧，要 ≥ 18"; fi
else
  bad "找不到 node（要 ≥ 18；装好放进 PATH，或设 NODE=/path/to/node）"
fi

FF="${FFMPEG:-$(command -v ffmpeg 2>/dev/null || true)}"
if [ -n "$FF" ] && [ -x "$FF" ]; then
  ok "$("$FF" -version 2>/dev/null | head -1 | cut -c1-60)（$FF）"
  if "$FF" -hide_banner -encoders 2>/dev/null | grep -q ' libx264 '; then ok "编码器 libx264"; else bad "ffmpeg 没编译 libx264"; fi
  filters="$("$FF" -hide_banner -filters 2>/dev/null)"
  for f in lut3d haldclut haldclutsrc colorbalance lutrgb curves colorchannelmixer extractplanes eq psnr; do
    if printf '%s\n' "$filters" | grep -Eq "^ *[A-Z.|]+ +$f +"; then :; else bad "ffmpeg 缺滤镜 $f"; missing=1; fi
  done
  [ -z "${missing:-}" ] && ok "滤镜 lut3d / haldclut / haldclutsrc / colorbalance / lutrgb / curves / colorchannelmixer / extractplanes / eq / psnr"
  if [ "$(uname)" = Darwin ]; then
    if "$FF" -hide_banner -hwaccels 2>/dev/null | grep -q videotoolbox; then ok "硬解 videotoolbox 可用（4K HEVC 自动自测后启用）"; else note "没有 videotoolbox 硬解，4K HEVC 会慢一些"; fi
  fi
else
  bad "找不到 ffmpeg（放进 PATH，或设 FFMPEG=/path/to/ffmpeg）"
fi
FP="${FFPROBE:-$(command -v ffprobe 2>/dev/null || true)}"
if [ -n "$FP" ]; then ok "ffprobe（$FP）"; else note "没有 ffprobe：不影响，脚本会改读 ffmpeg -i 的输出"; fi

PY="${PYTHON:-$(command -v python3 2>/dev/null || true)}"
if [ -n "$PY" ]; then
  pv="$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
  if "$PY" -c 'import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)'; then ok "python $pv（$PY）"; else bad "python $pv 太旧，要 ≥ 3.9"; fi
  for m in numpy PIL jieba; do
    if "$PY" -c "import $m" 2>/dev/null; then ok "python 模块 $m"; else bad "缺 python 模块 $m（pip install numpy pillow jieba）"; fi
  done
  if "$PY" -c "import cv2" 2>/dev/null; then ok "python 模块 cv2（扫二维码用）"; else note "缺 cv2：扫二维码要 pip install opencv-python-headless"; fi
else
  bad "找不到 python3"
fi

if [ -n "${ELEVENLABS_API_KEY:-}" ]; then ok "ELEVENLABS_API_KEY 已设置"
else
  note "ELEVENLABS_API_KEY 未设置：配乐 / 音效 / 转写要它（有 .env 的话：set -a && . ./.env && set +a）"
fi

if [ -x /usr/bin/lockf ]; then ok "排队锁：lockf"; elif command -v flock >/dev/null 2>&1; then ok "排队锁：flock"; else note "排队锁：没有 lockf / flock，queue.sh 会用 mkdir 锁"; fi

echo "== 技术还原 LUT（scripts/fetch_luts.sh 下载）"
for l in Slog3-S-Gamut3.Cine_To_s709_V200.cube DJI_Mavic_4_Pro_D-Log_M_to_Rec.709_V1.cube; do
  if [ -s "$SKILL/assets/luts/$l" ]; then ok "$l"; else bad "缺 assets/luts/$l（跑 scripts/fetch_luts.sh 从官方地址下载）"; fi
done

if [ -z "$PROJ" ]; then
  if [ -n "$PY" ]; then
    "$PY" - "$HERE" <<'EOF' || note "找不到中文字体：联系表上的中文文件名会显示成 ?（设 FTV_FONT=字体文件）"
import sys; sys.path.insert(0, sys.argv[1]); import ftvlib as L
f, cjk = L.find_font(16)
print("  ✓ 联系表标注字体：%s" % getattr(f, "path", "Pillow 默认")) if cjk else sys.exit(1)
EOF
  fi
  [ $fail = 0 ] && echo "✓ 环境齐了" || echo "✗ 有必需项缺失（见上）"
  exit $fail
fi

echo "== 项目 $PROJ"
if [ ! -f "$PROJ/project.json" ]; then bad "没有 $PROJ/project.json"; echo "✗ 有必需项缺失"; exit 1; fi
"$PY" - "$PROJ" "$SKILL" <<'EOF' || fail=1
import json, os, sys
proj, skill = sys.argv[1], sys.argv[2]
rc = 0
def ok(m): print("  ✓ " + m)
def bad(m):
    global rc; rc = 1; print("  ✗ " + m)
def note(m): print("  ⚠️ " + m)
try:
    p = json.load(open(os.path.join(proj, "project.json"), encoding="utf-8"))
except ValueError as e:
    bad("project.json 不是合法 JSON:%s" % e); sys.exit(1)
ok("project.json 可读")
root = os.path.expanduser(p.get("footage_root") or "")
if root and os.path.isdir(root): ok("素材目录 %s" % root)
else: bad("footage_root 不存在:%s" % (root or "(没写)"))
defaults = {"sony-slog3": "luts/Slog3-S-Gamut3.Cine_To_s709_V200.cube", "dji-dlogm": "luts/DJI_Mavic_4_Pro_D-Log_M_to_Rec.709_V1.cube"}
luts = dict(defaults); luts.update(p.get("luts") or {})
for prof, rel in luts.items():
    cands = ([rel] if os.path.isabs(rel) else []) + [os.path.join(proj, rel), os.path.join(skill, "assets", rel),
             os.path.join(skill, "assets", "luts", os.path.basename(rel))]
    hit = next((c for c in cands if os.path.isfile(c)), None)
    (ok if hit else bad)("LUT %s → %s" % (prof, hit or rel + "(找不到)"))
for src, prof in (p.get("profiles") or {}).items():
    if prof != "rec709" and prof not in luts: bad("profiles 里 %s 指定 %s,但 luts 里没有这条曲线的 LUT" % (src, prof))
look = p.get("look")
if look:
    f = look if os.path.isabs(look) else os.path.join(proj, look)
    (ok if os.path.isfile(f) else bad)("色表 look %s" % look + ("" if os.path.isfile(f) else "(不存在,先跑 look.py build)"))
else:
    note("没设 look:只做技术还原")
for k, rel in (p.get("fonts") or {}).items():
    f = os.path.join(proj, "public", rel)
    (ok if os.path.isfile(f) else bad)("字体 %s → public/%s" % (k, rel) + ("" if os.path.isfile(f) else "(不存在)"))
for k, rel in (p.get("brand") or {}).items():
    if isinstance(rel, str) and os.path.splitext(rel)[1].lower() in (".svg", ".png", ".jpg", ".jpeg", ".webp"):
        f = os.path.join(proj, "public", rel)
        (ok if os.path.isfile(f) else bad)("品牌物料 %s → public/%s" % (k, rel) + ("" if os.path.isfile(f) else "(不存在)"))
for f, what in (("work/footage.json", "素材盘点"), ("src/timeline.json", "编译好的时间线"), ("public/clips/clips.json", "片段清单")):
    if os.path.isfile(os.path.join(proj, f)): ok("%s(%s)" % (f, what))
    else: note("还没有 %s(%s)" % (f, what))
cj = os.path.join(proj, "public/clips/clips.json")
if os.path.isfile(cj):
    d = json.load(open(cj, encoding="utf-8"))
    if d.get("all_final"): ok("片段全部是最新的 final")
    else: note("片段还不全是最新的 final(成片前跑 make_clips.py 不带 --draft)")
sys.exit(rc)
EOF
[ $fail = 0 ] && echo "✓ 环境齐了" || echo "✗ 有必需项缺失（见上）"
exit $fail
