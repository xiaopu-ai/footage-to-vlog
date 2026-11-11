#!/usr/bin/env bash
# fetch_luts.sh · 从厂商官方地址下载技术还原 LUT（Log → Rec.709），放进 skill 的 assets/luts/。
# 厂商 LUT 的再分发授权不明确，所以开源仓库里不带这些文件，安装后跑一次本脚本即可。
#
# 用法：
#   scripts/fetch_luts.sh            # 下载到 <skill>/assets/luts/，已存在且校验通过的跳过
#   scripts/fetch_luts.sh --force    # 重新下载
# 产物：assets/luts/Slog3-S-Gamut3.Cine_To_s709_V200.cube、assets/luts/DJI_Mavic_4_Pro_D-Log_M_to_Rec.709_V1.cube
# 依赖：curl、unzip、shasum 或 sha256sum。
#
# 来源（官方）：
#   Sony   sony-cinematography.com → Resources → LUTs → s709 V200（Sony 官网的文件 CDN）
#   大疆   dji.com Mavic 4 Pro 下载页 → D-Log M to Rec.709 LUT（大疆官方 CDN 直链）
#          大疆官网会按 IP 把海外访问判到美区，美区页面没有这个 LUT；直链不受影响。
#          直链失效时：浏览器访问 https://www.dji.com/cn/mavic-4-pro/downloads（必要时把 cookie 改成 region=CN），
#          手动下载后改名放进 assets/luts/。
# 其他机型：别的大疆机型（Air / Mini / Osmo）或 Sony 的 S-Gamut3（非 Cine）要用各自官方的 LUT，
#          下载后放进项目，在 project.json 的 luts 里指定。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HERE/../assets/luts"
mkdir -p "$DEST"
FORCE=0; [ "${1:-}" = "--force" ] && FORCE=1

sha() { if command -v shasum >/dev/null; then shasum -a 256 "$1" | cut -d' ' -f1; else sha256sum "$1" | cut -d' ' -f1; fi; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

fetch() { # name  url  sha256  [zip-member]
  local name="$1" url="$2" want="$3" member="${4:-}" out="$DEST/$1"
  if [ "$FORCE" = 0 ] && [ -s "$out" ] && [ "$(sha "$out")" = "$want" ]; then echo "✓ 已有 $name"; return; fi
  echo "↓ $name"
  curl -fsSL --retry 3 --connect-timeout 20 -o "$TMP/dl" "$url"
  if [ -n "$member" ]; then
    unzip -o -q "$TMP/dl" -d "$TMP/unz"
    local f; f="$(find "$TMP/unz" -name "$member" -type f | head -1)"
    [ -n "$f" ] || { echo "✗ 压缩包里没有 $member" >&2; exit 1; }
    mv "$f" "$TMP/dl"
  fi
  local got; got="$(sha "$TMP/dl")"
  if [ "$got" != "$want" ]; then
    echo "⚠️ $name 的校验和和已知版本不同（厂商可能更新了文件）：$got" >&2
    echo "   仍然安装；如果调色明显不对，按文件头的来源手动下载核对。" >&2
  fi
  mv "$TMP/dl" "$out"; echo "✓ $name"
}

fetch "Slog3-S-Gamut3.Cine_To_s709_V200.cube" \
  "https://77snszqv.media.zestyio.com/Slog3-S-Gamut3.Cine_To_s709_V200.zip" \
  "79f33e0dba22f5e2e9710ef58c6ca3d331304a6dc2b66b5ed5047145acc37a4d" \
  "Slog3-S-Gamut3.Cine_To_s709_V200.cube"

fetch "DJI_Mavic_4_Pro_D-Log_M_to_Rec.709_V1.cube" \
  "https://terra-1-g.djicdn.com/851d20f7b9f64838a34cd02351370894/WA341%20LUT/DJI%20Mavic%204%20Pro%20D-Log%20M%20to%20Rec.709%20V1.cube" \
  "f00d2f8621d8fe3edcfc77a4d4969a8361aaf0a5804196df7d37dcaa2e925fed"

echo "LUT 就绪：$DEST"
