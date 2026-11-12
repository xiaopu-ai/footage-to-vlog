#!/usr/bin/env bash
# queue.sh · 可移植的重任务锁：全机同一时间只跑一个重任务（转码、切片段、批量抽帧、整片渲染），后来的排队等。
# 起因：两个 ffmpeg 同时跑，笔记本 CPU 到 100°C；两个渲染同时跑会双双超时。
#
# 用法：
#   scripts/queue.sh <命令> [参数…]
#   例：scripts/queue.sh python3 scripts/make_clips.py <proj>
#       scripts/queue.sh npx remotion render src/index.ts Film out/raw.mp4 --gl=angle --concurrency=4
# 产物：无（只是排队执行命令，退出码 = 命令的退出码）。
#
# 锁的实现（按顺序找）：macOS /usr/bin/lockf → Linux flock → 都没有就退化成 mkdir 锁（记 pid，进程没了自动清掉）。
# 防休眠：拿到锁之后，macOS 用 `caffeinate -i` 包住命令，Linux 有可用的 systemd-inhibit 就用它（--what=idle:sleep）。
#   起因：用户离开时系统休眠，ffmpeg 被挂起，单条片段从约 10 秒拖到约 15 分钟（DeepSeek 实测 3 条各空转 900+ 秒）。
#   caffeinate -i 只防「闲置休眠」，合上笔记本盖子照样会睡。
# 环境变量：
#   FTV_LOCK       锁文件，默认 /tmp/footage-to-vlog.lock。想和机器上已有的队列共用一把锁，就设成那个队列的锁文件。
#   FTV_HELD_ENV   已有队列用来标记「已经拿到锁」的环境变量名（比如它的 ffmpeg 包装认 RENDER_QUEUE_HELD）。
#                  设了以后：①这个变量非空时视为已持锁，不再抢锁（防止自己等自己）；②拿到锁后也把它设成 1 传给子进程。
#   FTV_NO_QUEUE=1       不排队直接跑（照样防休眠）。
#   FTV_NO_CAFFEINATE=1  不防休眠。
#   FTV_LOCK_MODE  强制用 lockf / flock / mkdir（测试用；默认自动选）。
# 拿到锁的子进程里 FTV_QUEUE_HELD=1：里面再调 queue.sh（或 Python 脚本自动排队）不会重复抢锁，也不会再套一层防休眠。
# 例（和本机另一套队列共用一把锁）：
#   FTV_LOCK=/tmp/remotion-render-queue.lock FTV_HELD_ENV=RENDER_QUEUE_HELD scripts/queue.sh python3 scripts/inventory.py …
# 写法约定：变量一律 ${var}（bash 3.2 + UTF-8 下 `$var（` 会报 unbound variable）；不用数组（bash 3.2 下 set -u 空数组也会报错）。
set -u
if [ "${#}" -eq 0 ]; then
  sed -n '2,27p' "${0}"; exit 2
fi
LOCK="${FTV_LOCK:-/tmp/footage-to-vlog.lock}"
HELD_ENV="${FTV_HELD_ENV:-}"
if [ -n "${HELD_ENV}" ] && ! printf '%s' "${HELD_ENV}" | grep -Eq '^[A-Za-z_][A-Za-z0-9_]*$'; then
  echo "[queue] FTV_HELD_ENV 不是合法的变量名：${HELD_ENV}" >&2; exit 2
fi

held=0
[ "${FTV_QUEUE_HELD:-}" = "1" ] && held=1
if [ -n "${HELD_ENV}" ] && [ -n "$(eval "printf '%s' \"\${${HELD_ENV}:-}\"")" ]; then held=1; fi
if [ "${held}" = 1 ]; then
  exec "${@}"   # 外层已经持锁、也已经防休眠
fi

# 防休眠：把命令改写成「caffeinate -i 命令…」/「systemd-inhibit … 命令…」。只在最外层套一次。
AWAKE=""
if [ "${FTV_NO_CAFFEINATE:-}" != "1" ]; then
  if [ "$(uname)" = Darwin ] && command -v caffeinate >/dev/null 2>&1; then
    AWAKE="caffeinate -i"
    set -- caffeinate -i "${@}"
  elif command -v systemd-inhibit >/dev/null 2>&1 \
       && systemd-inhibit --what=idle:sleep --who=footage-to-vlog --why=probe true >/dev/null 2>&1; then
    # 先试一次：容器里没有 logind 时 systemd-inhibit 会直接失败，那样就不套，免得命令根本不跑
    AWAKE="systemd-inhibit"
    set -- systemd-inhibit --what=idle:sleep --who=footage-to-vlog "--why=footage-to-vlog 重任务" "${@}"
  fi
fi
AWAKE_NOTE=""
[ -n "${AWAKE}" ] && AWAKE_NOTE="（${AWAKE} 防休眠）"

if [ "${FTV_NO_QUEUE:-}" = "1" ]; then
  exec "${@}"
fi

export FTV_QUEUE_HELD=1
[ -n "${HELD_ENV}" ] && export "${HELD_ENV}=1"
say() { echo "[queue] $(date +%H:%M:%S) ${*}" >&2; }
# 拿到锁之后才打印「开始」
START='echo "[queue] $(date +%H:%M:%S) 开始${AWAKE_NOTE}：${*}" >&2; exec "${@}"'
export AWAKE_NOTE

MODE="${FTV_LOCK_MODE:-}"
if [ -z "${MODE}" ]; then
  if [ -x /usr/bin/lockf ]; then MODE=lockf; elif command -v flock >/dev/null 2>&1; then MODE=flock; else MODE=mkdir; fi
fi

if [ "${MODE}" = lockf ]; then
  if ! /usr/bin/lockf -k -t 0 "${LOCK}" true 2>/dev/null; then say "另一个重任务在跑，排队等待…（锁 ${LOCK}）"; fi
  /usr/bin/lockf -k "${LOCK}" bash -c "${START}" _ "${@}"; rc="${?}"
  say "结束（退出码 ${rc}）"; exit "${rc}"
fi

if [ "${MODE}" = flock ]; then
  if ! flock -n "${LOCK}" true 2>/dev/null; then say "另一个重任务在跑，排队等待…（锁 ${LOCK}）"; fi
  flock "${LOCK}" bash -c "${START}" _ "${@}"; rc="${?}"
  say "结束（退出码 ${rc}）"; exit "${rc}"
fi

# 兜底：mkdir 是原子操作。锁目录里记持锁进程的 pid，那个进程不在了就当作残留锁清掉。
DIR="${LOCK}.d"
waited=0
while ! mkdir "${DIR}" 2>/dev/null; do
  pid="$(cat "${DIR}/pid" 2>/dev/null || true)"
  if [ -n "${pid}" ] && ! kill -0 "${pid}" 2>/dev/null; then
    say "清掉残留锁（pid ${pid} 已不在）"; rm -rf "${DIR}"; continue
  fi
  [ "${waited}" = 0 ] && say "另一个重任务在跑，排队等待…（锁 ${DIR}）"
  waited=1; sleep 2
done
echo "${$}" > "${DIR}/pid"
trap 'rm -rf "${DIR}"' EXIT
trap 'exit 130' INT TERM
say "开始${AWAKE_NOTE}：${*}"
"${@}"; rc="${?}"
say "结束（退出码 ${rc}）"
exit "${rc}"
