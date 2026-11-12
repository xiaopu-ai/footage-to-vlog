# footage-to-vlog · Remotion 成片模板

这是一个独立的 Remotion 项目。它只读 `src/timeline.json`（由 `scripts/timeline.py` 从 `edit.json` 编译出来），把剪辑数据渲染成片。镜头、运镜、转场、文字包装、字幕和声音都在这里实现，参数是四条已定版示例片（人物访谈、新品发布片、燃剪、口号片）用户审过的原值。**写剪辑的人（或 agent）只改数据，不改这里的代码。**

数据格式见 skill 的 `references/edit-schema.md`（第 5 节 timeline.json）。

## 复制、安装

```bash
cp -R <skill>/template <proj>
cd <proj>
npm install            # Node ≥ 18；remotion / @remotion/cli / bundler / renderer 都钉在 4.0.469
npx tsc --noEmit       # 类型检查
```

项目目录：

| 路径 | 内容 |
|---|---|
| `src/timeline.json` | 编译产物，模板唯一的输入。不要手改，改 `edit.json` 再跑 `timeline.py` |
| `public/clips/` | `make_clips.py` 切好、调好色的片段（`<镜号>.mp4`） |
| `public/audio/` | `music.wav`（按小节重剪好的配乐）、`sfx/*.wav` |
| `public/brand/` | 官方 Logo（原样，不重绘） |
| `public/fonts/` | 默认字体和授权（见 `public/fonts/LICENSES.md`） |
| `tools/stills.mjs` | 批量静帧 |

🔴 `public/` 里**不要放文件软链**：Remotion 的静态服务器拒绝文件软链（404，渲染直接失败）。要省空间就用硬链接（`ln 源 目标`），或者让整个目录是软链（目录软链可以）。

## 预览

```bash
npx remotion studio
```

两个合成：

- **Film**：整片。时长、尺寸、帧率都取自 timeline.json。
- **Frame**：1 帧、30 fps，`props.frame` 指定显示整片第几帧，给风格帧和分镜用。**不要用 `<Still>`**：它是 1 fps，片段的取帧会被当成秒。

两个合成都接受 `props.timeline`，传一份别的 timeline 就渲那一份：

```bash
npx remotion still src/index.ts Frame out/f0120.png --props='{"frame":120}'
```

## 静帧（风格帧、分镜、自审）

```bash
node tools/stills.mjs [另一份timeline.json] <输出目录> <帧…> [--scale=1] [--comp=Film|Frame] [--skip-existing]
# 例：node tools/stills.mjs /tmp/check 0 60 120 240
```

一次打包（关 webpack 缓存）、只开一个浏览器，按整片帧号连续出 `f0120.png`。默认用 `renderStill` 直接渲 Film 的那一帧；`--comp=Frame` 走 1 帧合成。分镜审核台（`<skill>/review/build_review.py`）就是调它出图。

## 整片渲染：用 `scripts/render.py`，不要直接交付 Remotion 的输出

```bash
python3 <skill>/scripts/render.py <proj> --preview 15 --allow-draft   # 节奏预演：开场 15 秒、半分辨率
python3 <skill>/scripts/render.py <proj> --name <片名>-v1             # 整片：再出无声版 / 纯人声版
python3 <skill>/scripts/qa.py <proj> out/<片名>-v1.mp4 --kind film    # 输出检查，任一项 fail 退出码非零
```

`render.py` 的步骤：
1. 排队调 Remotion **静音**渲画面，参数是 `--muted --image-format=png --pixel-format=yuv420p --color-space=bt709 --gl=angle --concurrency=4 --timeout=1800000 --bundle-cache=false`；
2. 核对帧数；
3. 用 `mix.py` 出规范混音，先限幅、再两遍响度标准化；
4. 用 ffmpeg 封装：视频流不重编码，补齐 BT.709 色彩标签。

为什么不用 Remotion 直出（实测）：
- 带声音的直出，AAC 音轨晚约 43 ms；
- 用 JPEG 帧渲染出来是全范围 BT.601。

`remotion.config.ts` 已经改成 PNG 帧、yuv420p、BT.709。代价是渲染慢一些：10 秒片段约 36 秒，原来约 21 秒。

其他要点：
- 🔴 **渲染要排队**：本机同一时间只跑一个重任务。`render.py` 会自己走 `scripts/queue.sh`；静帧可以设 `RENDER_QUEUE=<skill>/scripts/queue.sh`，`tools/stills.mjs` 会自己进队列。
- `--timeout=1800000`：字体的 `delayRender` 句柄一旦没清除，渲到 `--timeout` 就整片退出。模板里字体加载成功、失败、20 秒超时都会放行，这个值只是保险。
- `--bundle-cache=false`：几个项目共用 node_modules 时 webpack 缓存会串。

## 模板实现了什么

### 镜头

- **逐帧取画**：第 k 帧取片段的 `clip_frames[k]` 帧。变速、定格都已算进 `clip_frames`，不靠 `playbackRate`（它低于 0.5 时整片渲染会黑帧）。写法是 `<Freeze frame={0}><OffthreadVideo trimBefore={clip_frames[k]} muted/></Freeze>`（燃剪的写法）：**不要**写成 schema 里的 `<Freeze frame={clip_frames[k]}>`，Remotion 会把冻结帧号夹到「合成时长 − 1」以内，片段帧号超过整片帧数就卡住，1 帧的 Frame 合成里永远是第 0 帧（实测）。
- **`frame`**：静态重构图（放大 + 位移）。位移超出放大余量会被夹住，四边永远不露黑。
- **`move`**：`none` / `push` / `pull` / `drift-left` / `drift-right` / `rise`，在整个可见区间里线性走完；`interview` 见下。
- **`interview`**（访谈示例片的 `camOf`）：同一段采访不复位，整段缓推 +0.04；剪气口处全景 1.03 / 近景 1.18 交替（`tight`）；`make_room: "right"` 时整体放大并左移，让出右侧给信息卡。`run_from` / `run_to` / `tight` 由 timeline.py 给，没给就模板自己算。
- **`sky`**：天空压暗渐变（燃剪）；**`vignette`**：暗角；**黑场镜头**：`clip` 为 null 时画纯黑，照样参与转场。
- **`light`**（模板扩展，schema 里没有）：镜内光扫，`{"from": 4, "to": 40, "angle": 80, "width": 0.45, "stay": false}`，帧号相对 cut。

### 转场（窗口以切点为中心：`[cut − pre, cut + post)`）

| type | 做法（来源） |
|---|---|
| `cut` | 硬切 |
| `dip` | 切点前淡到黑、切点后从黑淡入；主轨的 dip 作用于整幅画面（插入层一起暗） |
| `fade` | 两镜交叉叠化 |
| `whip` | 上一镜横向甩出、本镜从反方向甩进；拖影是 6 层位移叠加（燃剪甩镜，不用 blur 放大画面） |
| `punch` | 上一镜加速推到 ×1.2 + 4 层变焦拖影；本镜从 1.15 回落到 1、同时提亮 16%（发布片冲击推镜） |
| `sweep` | 暖色柔光带扫过、后面露出本镜，换镜藏在光带最亮处。三层光：补光带半宽 45%（更亮偏暖）、只取最亮约 30% 的高光（模糊 7、screen 0.95）、7% 暖色空气光，ease-in-out（发布片定稿） |
| `wipe` | 本镜用蒙版从左往右擦入，入镜画面轻推（访谈示例片问题卡；没有橙色细线） |

重叠类（fade / sweep / wipe）两镜要同时可见，片段帧不够时用首 / 末帧定格补齐。没有下一镜、自带 `out_tr` 的镜头（插入层收尾、最后一镜）会按自己的 out_tr 淡出。

### 文字（`texts[].type`）

| type | 主要字段（默认值） | 来源 |
|---|---|---|
| `slam` | `text`、`size`=420、`font`=brush、`shake`=14、`push`=0.06、`nudge`=[dx,dy]、`stroke` | 访谈示例片倒计时：2 帧内 2.6→1 倍，画面衰减震动 10 帧、每下推近一档；首尾相接的一组四周压暗 |
| `brushword` | `text`、`x`=960、`y`=470、`size`=200、`vertical`、`align` | 访谈示例片关键词：裁切揭示 + 弹簧 |
| `chapter` | `text`、`side`=left、`size`=150、`cx`（左 330 / 右 1590）、`cy`=540、`nd`=1 | 口号片竖排章名 + 天空压暗，硬切进出 |
| `qcard` | `no`、`lines[]`、`hi`、`tag`、`of`、`logo` | 访谈示例片 v7 问题卡；`tag` 会在卡后出章节角标，停到下一张问题卡 |
| `lower` | `name`、`title`、`kicker`、`sub`、`side`、`shade`=0.55 | 人名条（访谈示例片人物卡的字体层级），角落压暗衬底 |
| `spec` | `big`、`unit`、`label`、`sub`、`no` / `of`、`index` | 发布片 C 版参数卡；一张卡不到 70 帧时出场压到 16 帧内 |
| `reveal` | `text` 或 `image`、`x`、`y`、`size`、`hi`、`w`、`invert`、`delay`=4、`sweep`=24/28 | 发布片字标 / Logo 光扫揭示 |
| `lockup` | `lines[2]`、`logo`、`size`=150、`offset`=120、`logo_at`=48 | 口号片落版：字从雾里浮现，Logo 在重击上出现；下面的画面从 `hold_from` 起成雾 |
| `endcard` | `logos[]`（路径或 `{src, w, h, invert}`）、`sub`、`bg` | 燃剪片尾：深色面板擦入 + 官方 Logo |
| `image` | `src`、`x`、`y`（中心）、`w`、`h`、`invert`、`fade`=8 | 静态图淡入淡出 |

`font` 写字体角色：`brush` / `kai`（马善政）、`sans`（思源黑体）、`title`（思源黑体 Black，锁 900 字重）、`num`（D-DIN Condensed）；timeline.fonts 可以给角色换文件或加新角色。

### 字幕和声音

- 字幕：`captions[]`，样式取 `caption_style`（`size` 默认 46、`bottom` 默认 90）；问题卡、砸字、落版、片尾 Logo 出现时不出字幕。
- 配乐：`gain_keys` 线性插值，`duck_ranges` 里按 dB 压低 `duck_db`（前 `attack` 帧压下去、后 `release` 帧回来）。
- 音效：`sfx[]`，可选 `tone`（变调）、`dur`。
- 原声：`voice[]`，从片段的 `clip_start_frame`（30 fps 帧号）开始放，原速，带 `fade_in` / `fade_out`。

## 改模板时的规矩

- 动效全部帧驱动；画面部分只看整片帧号 g，不用 `<Sequence>`（所以 Frame 合成能渲任意一帧）。
- 入场动画的 transform 不要覆盖元素自己的 scale / rotate：拼在一个 transform 里，或拆两层。
- 放大很多倍的元素不要带 filter / drop-shadow / mix-blend-mode / blur（Chrome 离屏渲染面超限会被缩小再贴回）；拖影用多层叠加。
- SVG 滤镜一律 `color-interpolation-filters="sRGB"`。
- `<Sequence>` 放进普通 div 时容器要 `position: relative`。
- 改完 `npx tsc --noEmit`，再用 `tools/stills.mjs` 渲关键帧亲眼看过。
