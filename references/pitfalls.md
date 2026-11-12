# 工程坑（都踩过）

## 机器和性能

- 🔴 **全机同一时间只跑一个重任务**：转码、切片段、批量抽帧、静帧批量、整片渲染都套 `scripts/queue.sh`。两个 ffmpeg 同时跑，M2 Pro 笔记本 CPU 到过 100°C。
- **ffmpeg 限线程**：没写 `-threads` 时加 `-filter_threads 4`，输出前加 `-threads 4`。实测占核 8.9 → 3.8，耗时翻倍，画质不变（PSNR 一样）。「只用能效核」（`taskpolicy -b`）慢 11 倍，不用。
- **硬解**：4K HEVC（大疆航拍）在 macOS 上 `-i` 前加 `-hwaccel videotoolbox`，CPU −35%，输出逐位一致。Sony 1080p H.264 不加：省不了，还偏暗约 1%。新格式先拿 2 秒素材，软解硬解各转一次比 PSNR，结果是 inf 才用硬解。
- **两个 ffmpeg 别用管道接**：后一个会等前一个的锁，卡死。确实要接，就把整条管道放进一次 `queue.sh` 里。
- **Remotion 并发**：`--concurrency=4` 足够。并发 4 / 3 / 2 占核差不多，画面差 55 dB 以上。

- **休眠会让长任务空转**：用户离开、电脑休眠时 ffmpeg 会被挂起，单条片段从约 10 秒拖到约 15 分钟。`queue.sh` 拿到锁后会在 macOS 上用 `caffeinate -i`、在 Linux 上用 `systemd-inhibit` 包住命令；`make_clips.py` 发现单条耗时超过预期 10 倍会提示。
- **缓存目录要可写、盘点要对账**：有的 agent 跑在沙箱里，写不了 `~/.cache`。`inventory.py` 的缓存优先级是 `FTV_CACHE` → 项目内 `work/cache` → `~/.cache` → 只存内存，写不进只警告，不会让分析失败。盘点必须对账：发现数 = 分析成功 + 失败，排除的单列；有失败退出码 2，不能当成完成。用 `--include` 圈定素材目录，免得把成片、旧项目扫进去。曾经有 43 条航拍因为缓存写不进去被静默漏掉。

## Remotion

- **不要用 Remotion 带声音的直出当成片**：两次实测都发现直出的 AAC 音轨比画面晚约 40 ms；JPEG 帧渲染出来是全范围 BT.601，播放器会偏色；整片还多 2 帧。用 `render.py`：静音渲画面（PNG、yuv420p、BT.709），`mix.py` 出规范混音，再用 ffmpeg 封装并补齐色彩标签。

- `public/` 里不要放**文件软链**：Remotion 的静态服务器会 404，渲染直接失败。复制或用硬链接（`ln 源 目标`）；整个目录做软链可以。
- 模板逐帧取画用 `<Freeze frame={0}><OffthreadVideo trimBefore={帧号}/></Freeze>`；写成 `<Freeze frame={帧号}>` 会被夹到「合成时长 − 1」，片段帧号一大就卡住。
- studio 预览里逐帧取画会卡，这是正常的；以 `tools/stills.mjs` 渲的静帧和整片渲染为准。

- 整片渲染要加 `--timeout=1800000`：字体的 delayRender 一旦没清除，渲到 `--timeout` 就退出，和渲到第几帧无关。模板里字体加载成功、失败都会 `continueRender`。
- `--bundle-cache=false`：几个项目或 worktree 共用 node_modules 时，webpack 缓存会串（报 `wasm-hash.js reading 'length'`）。
- 含 WebGL 或滤镜的渲染加 `--gl=angle`。
- `<OffthreadVideo>` 的 `playbackRate < 0.5` 时，整片渲染会整段黑帧。模板用 `<Freeze frame>` 逐帧取画，不靠 playbackRate。
- 风格帧不能用 `<Still>`：它是 1 fps，startFrom 会被当成秒。用模板的 `Frame` 合成（1 帧、30 fps）。
- 只渲 wav 也会逐帧取视频，可能报 compositor 超时。声音有问题就整片重渲，或者用 ffmpeg 把人声、配乐、音效三轨直接相加：`amix=normalize=0`。
- 入场动画的 `transform` 会整条覆盖元素自己的 `scale` / `rotate`。基础变换要拼在入场变换前面，或者拆成两层 div。
- 放大很多倍的元素不要带 `filter`、`drop-shadow`、`mix-blend-mode`、`blur`：Chrome 会建离屏渲染面，超过纹理上限就被缩小再贴回，字形放大错位。拖影用多层位移叠加。
- SVG 滤镜要设 `color-interpolation-filters="sRGB"`，否则整体发灰。
- CSS 3D（preserve-3d）的片子要用 `--concurrency=1` 渲：并发时同一帧的字会差 1–7 px，成片来回抖。渲完跑 `jitter_scan.py`。
- `<Sequence>` 放进普通 div 时，容器要 `position: relative`。

## ffmpeg

- 本机可能没有 ffprobe，脚本会退回解析 `ffmpeg -i` 的输出。Remotion 自带的 ffprobe 在 `node_modules/@remotion/compositor-*/`。
- 片段编码统一用 `-fps_mode cfr -r 30 -g 30`，录屏和 VFR 素材先转 CFR，否则 seek 不准。
- mp3 解码自带约 22 ms 编码延迟，AAC 约 21 ms。成片里量出 20–40 ms 的偏差是正常的，但测拍相位要考虑这一点。

## 权限和下载

- macOS 上 agent 的 shell 进程可能读不了「桌面」：权限按 helper 进程算，给 App 开权限没用。素材放在「文稿」或其他目录，「桌面」只留软链。
- YouTube：系统自带的 yt-dlp 太旧。下官方独立二进制（`yt-dlp_macos`），加 `--js-runtimes node`，格式选 `bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]`。
- B 站报 HTTP 567：用 `--print urls` 取直链，域名换成 `upos-sz-mirrorhw.bilivideo.com`，带 Referer 用 curl 下。
- 大疆 LUT：带 cookie `region=CN` 才能拿到国区直链。
- ElevenLabs：作曲并发上限 2，遇到 429 等一下重试；音效逐条请求。
- 不读、不打印 `.env` 和 key，只在命令里 `set -a && . ./.env && set +a` 加载。

## 上下文和成本

- 每个阶段结束就写 `HANDOFF.md`。上下文太长（每次调用重读 50 万以上 token）时，换新会话接着做。访谈示例片前两个阶段花掉了总费用的 71%。
- 大段工具输出（素材元数据、转写全文）写进文件，只读需要的那几行。

## 审核介质

- 有的 agent 界面放不了本地 mp3 / mp4，连对话里的图片都不显示。所有审核节点统一用本地网页（`review/build_review.py` + `review/serve_local.py`），从第 1 个节点就这样做，不要先贴文件试试看。
- 本地服务要支持 HTTP Range，否则 Safari 的 `<audio>` / `<video>` 播不了。`python3 -m http.server` 不支持，用 skill 自带的 `serve_local.py`。
