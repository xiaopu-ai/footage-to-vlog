# 数据格式:project.json / edit.json / timeline.json

这条流水线只有一个原则:**剪辑决定写成数据,工具和模板只认数据。**

- 人或 agent 只写 `edit.json`(剪辑决定)和 `project.json`(素材、调色、字体)。
- `scripts/timeline.py` 把它编译成 `src/timeline.json`,所有时间都换算成帧,同时做校验(lint)。
- Remotion 模板只读 `timeline.json` 渲染;`make_clips.py`、审核台、卡点核对、卡点表也都读它。
- 不要手改 `timeline.json`;要改就改 `edit.json`,再重新编译。

全片固定 30 fps、1920×1080。时间单位:写在 `edit.json` 里的可以是帧、秒、拍、小节、口播短语;编译后一律是帧(整数)。

## 1. 项目目录(从 `template/` 复制出来)

```
<proj>/
  project.json              素材根目录、LUT、调色色表、字体、品牌色
  edit.json                 剪辑决定(唯一需要手写的剪辑文件)
  beats.json                拍网格(scripts/tempo.py 写;卡点类风格必需)
  words.json                采访逐字时间(scripts/paper_edit.py 写;采访类风格必需)
  src/timeline.json         timeline.py 编译产物,Remotion 只读它
  public/clips/<shot>.mp4   make_clips.py 切出来、已调色的片段
  public/audio/music.wav    剪好的配乐(bar_edit.py)
  public/audio/sfx/*.wav    音效
  public/brand/*            Logo(官方文件,不重绘)
  public/fonts/*            字体
  work/                     footage.json、联系表、shotlog.md、转写、配乐候选、色表、抽帧、审核台
  out/                      交付物
```

## 2. project.json

```json
{
  "title": "品牌燃剪示例",
  "footage_root": "/Users/me/Documents/footage",
  "fps": 30, "width": 1920, "height": 1080,
  "luts": {
    "sony-slog3": "luts/Slog3-S-Gamut3.Cine_To_s709_V200.cube",
    "dji-dlogm":  "luts/DJI_Mavic_4_Pro_D-Log_M_to_Rec.709_V1.cube"
  },
  "profiles": { "rider/DJI_20250101090000_0053_D.MP4": "rec709" },
  "look": "work/looks/A2.png",
  "handles_s": 0.5,
  "levels_target": [0.05, 0.90],
  "fonts": {
    "brush": "fonts/MaShanZheng-Regular.ttf",
    "sans":  "fonts/NotoSansSC-VF.ttf",
    "title": { "src": "fonts/NotoSansSC-VF.ttf", "weight": "900" },
    "num":   "fonts/D-DINCondensed-Bold.ttf"
  },
  "brand": { "accent": "#FF7A1A", "logo_white": "brand/logo-w.svg", "logo_dark": "brand/logo-k.svg" }
}
```

- `luts`:相对路径先找 `<proj>/`,再找 `<skill>/assets/`。
- `profiles`:手动覆盖 `inventory.py` 自动判断的机型曲线。可选值:`sony-slog3` / `dji-dlogm` / `rec709`。
- `look`:HALD 色表(`scripts/look.py` 生成);`null` 表示只做技术还原。
- `handles_s`:切片段时两头各多留几秒,给转场重叠和变速用。
- `levels_target`:`fix.levels: "auto"` 时把每镜的 p5 / p95 拉到的目标,默认 [0.05, 0.90]。A2 这类色表要配合 auto 用。
- 曲线优先级:`profiles` > footage.json > 现场判断。没有对应 LUT 的曲线(sony-slog2 / hlg / pq / unknown-log)`make_clips.py` 直接报错,要先补 LUT。
- `fonts` / `brand`:路径都相对 `public/`。`fonts` 可以整个不写,模板默认就是上面这套 OFL 字体(见 `template/public/fonts/LICENSES.md`);要换字体才写。

## 3. footage.json(`inventory.py` 生成)

顶层是数组,每条素材一项。除下例的字段外,还有 `fps_rational`、`rotation`、`matrix`、`profile_confidence`、`codes`(码值统计)、`sony_meta`、`contact`(在哪张联系表上)。

每条素材一项:

```json
{
  "path": "aerial/DJI_20250101102513_0020_D.MP4",
  "duration": 61.2, "fps": 59.94, "width": 3840, "height": 2160,
  "codec": "hevc", "pix_fmt": "yuv420p10le", "bit_depth": 10, "range": "tv",
  "transfer": "", "make": "DJI", "model": "Mavic 4 Pro",
  "profile": "dji-dlogm", "profile_reason": "DJI 10-bit,中位码值 0.51",
  "has_audio": false, "created": "2026-03-06T10:25:13"
}
```

## 4. edit.json(手写)

```json
{
  "style": "hype",
  "duration": { "bars": 23 },
  "shots": [ ... ],
  "texts": [ ... ],
  "captions": { "enabled": false },
  "audio": { "music": { ... }, "sfx": [ ... ] }
}
```

`style` 的取值:`testimonial` / `launch` / `hype` / `manifesto` / `custom`。lint 规则按它来套。`duration` 可以写帧数、`{"bars": n}`、`{"s": x}`,或 `"auto"`(取主轨最后一镜的结尾)。

### 4.1 时间点 T 和时长 D

**时间点 T**,下面任一写法都行。每种写法都可以再加 `"offset": n`(帧,可以是负数):

| 写法 | 意思 |
|---|---|
| `120` | 第 120 帧 |
| `{"s": 12.5}` | 第 12.5 秒 |
| `{"beat": 16}` | 第 16 拍(从 0 数),帧号取 beats.json 的拍点再减 `lead` |
| `{"bar": 4}` / `{"bar": 4, "beat": 2}` | 第 4 小节(从 0 数)的第 1 拍 / 第 3 拍 |
| `{"eighth": 33}` | 第 33 个 8 分音符 |
| `{"say": "钓鱼", "nth": 0, "edge": "start"}` | 口播里第 `nth` 次出现「钓鱼」的开头(`edge: "end"` 取结尾),查 words.json |
| `{"shot": "S07", "edge": "start"}` | 某个镜头的切点(`"end"` 取它的结尾) |

**时长 D**:`45`(帧)、`{"s": 1.5}`、`{"beats": 2}`、`{"bars": 1}`、`{"eighths": 3}`,或者 `{"until": T}`。

### 4.2 shots(镜头)

```json
{
  "id": "S07",
  "src": "aerial/DJI_20250101102513_0020_D.MP4",
  "in": 10.4,
  "at": { "bar": 4 },
  "dur": { "beats": 2 },
  "layer": 0,
  "speed": 1,
  "frame": { "scale": 1.0, "x": 0, "y": 0 },
  "move": "push",
  "move_amt": 0.06,
  "in_tr": { "type": "cut", "len": 0 },
  "audio": false,
  "fix": { "warm": 0.0, "exposure": 0.0 },
  "sky": 0.0,
  "vignette": 0.25,
  "hold": null,
  "note": "S 形尾迹正俯拍,drop 第一刀"
}
```

| 字段 | 说明 |
|---|---|
| `id` | 唯一,也用作片段文件名 `public/clips/<id>.mp4` |
| `src` / `in` | 素材路径(相对 `footage_root`)、镜头起点对应的源素材秒数(真实时间) |
| `at` | 默认紧接同一层上一镜的结尾 |
| `layer` | `0` 主轨;`1` 盖在主轨上的插入镜头(比如采访说话时盖 B-roll,采访的声音照常) |
| `speed` | 相对真实时间的倍数:`1` 原速,`0.5` 慢一倍。也可以写关键点 `[[0,1],[0.4,0.25],[1,1]]`,第一列是镜头内进度 0–1,线性插值。**下限是 `min(1, 30 / 原生帧率)`**:60p 最慢 0.5,120p 最慢 0.25;25p / 30p 素材(比如 FX3 采访是 25p)不能慢放。再低会重复帧发顿,lint 报错 |
| `src: null` | 黑场镜头:不切片段,直接画纯黑,用来垫空隙或落版底 |
| `make_room` | 只用于 interview:`"right"` 时人物整体左移,把右侧让给信息卡 |
| `frame` | 静态重构图:`scale` 放大(比如 1.3 用来裁掉商标),`x` / `y` 是 1920 坐标系里的像素偏移 |
| `move` | 运镜预设:`none`、`push`、`pull`、`drift-left`、`drift-right`、`rise`、`interview`(采访双机位感,见 4.2.1)。`move_amt` 是幅度,默认 0.06 |
| `in_tr` | 从同层上一镜进本镜的转场,见 4.2.2 |
| `audio` | `false`,或 `true` / `{"gain":1,"fade_in":0,"fade_out":6}`:播放这一镜自己的声音(采访人声、现场声)。只允许 `speed` 恒为 1 |
| `fix` | 切片段时烤进去的逐镜校正:`warm` 为正往冷拉(减红加蓝);`exposure` 以档为单位;或写 `"levels": "auto"`,按 p5 / p95 拉到全片统一目标 |
| `sky` | 天空压暗渐变强度 0–1(冷峻调色靠它,不靠压暗部);正俯拍别加 |
| `vignette` | 暗角 0–1,默认取风格默认值 |
| `hold` | `{"from": D}`:从镜头内第几帧开始定格,用于定格落版 |
| `light` | 镜内光扫(发布片的三层光):`{"from": 4, "to": 40, "angle": 80, "width": 0.45, "stay": false}`,帧号相对本镜切点 |

#### 4.2.1 `move: "interview"`

同一段连续采访(主轨上首尾相接的 interview 镜头)当成一次「机位运行」:

- 运行内镜头不复位,整段缓推 +0.04;
- 每个剪气口(段内的切点)在全景 1.03 和近景 1.18 之间交替,看起来像两台机位;
- 短于 15 帧的碎片沿用上一档;
- `make_room: "right"` 时整体左移,把右侧让给信息卡。

#### 4.2.2 转场 `in_tr`

切点帧就是本镜的 `at`。默认 `{"type":"cut"}`。

转场以切点为中心:切点前 `pre = len // 2` 帧,切点后 `post = len − pre` 帧;`punch` 例外,是 `pre = round(len × 5/8)`、`post = len`(len 8 → 切点前 5 帧推进、切点后 8 帧回落,照发布片原版)。插入层的镜头和全片最后一镜没有「下一镜」,可以自己写 `out_tr`。

| type | 默认 len | 做法 | 适用 |
|---|---|---|---|
| `cut` | 0 | 硬切 | 默认。**采访说话中只能用它** |
| `dip` | 8 | 上一镜后 len/2 淡到黑,本镜前 len/2 从黑淡入 | 暗场段落、章节换气 |
| `fade` | 12 | 交叉叠化,两镜重叠 len 帧 | 慢段、落版前 |
| `whip` | 8 | 上一镜最后 len/2 帧横向拖影加位移,本镜前 len/2 帧从反方向拖影进 | 进 drop、换场 |
| `punch` | 8 | 上一镜最后 5 帧放大到 ×1.2 并拖影;本镜前 8 帧从 1.15 回落到 1,同时提亮 16% | 重拍、进高潮 |
| `sweep` | 10 | 一道柔光带扫过画面,换镜藏在光带最亮处 | 产品揭示、发布片 |
| `wipe` | 12 | 本镜用蒙版从左往右擦入,盖住上一镜 | 问题卡、章节卡 |

拖影一律用多层位移叠加,不用 CSS `blur` 去放大画面。

### 4.3 texts(文字和包装)

通用字段:`id`、`type`、`at`(T)、`dur`(D)、`z`(层级)。各类型的字段和默认值:

| type | 字段 | 做法(来源) |
|---|---|---|
| `slam` | `text`、`size`=420、`font`=brush、`shake`=14、`push`=0.06、`nudge`=[dx,dy] | 毛笔字 2 帧内从 2.6 倍砸到 1 倍,画面衰减震动 10 帧,镜头一档推近。字形中心要量像素修正(`nudge`)(访谈示例片倒计时) |
| `brushword` | `text`、`x`、`y`、`size`=200、`vertical`、`align` | 关键词毛笔字:裁切揭示加弹簧,带投影(访谈示例片) |
| `chapter` | `text`、`side`=left/right、`size`=150、`cx`、`cy` | 竖排章名,`vertical-rl`,字距 −0.12em,色 #fdfdfb 加淡投影。站三分线外侧,按主体让位,硬切进出(口号片) |
| `qcard` | `no`、`lines[]`、`hi`、`tag` | 问题卡:序号加 2–3 行题目,`hi` 高亮,蒙版从左擦入 12 帧,至少停 110 帧(≈3.7 s)让人读完(访谈示例片) |
| `lower` | `name`、`title` | 人名条 |
| `spec` | `big`、`unit`、`label`、`sub` | 参数卡:大数字加单位加说明,左上角压暗衬底;一张卡停 4 拍,第 3 拍换背景、字不动(发布片) |
| `reveal` | `text` 或 `image` | 用光带扫出字标(发布片) |
| `lockup` | `lines[2]`、`logo` | 落版:字从雾里浮现,scale 0.78→1、blur 14→0,两行左右错开 120 px,Logo 高 120,配合 `hold` 定格成雾(口号片) |
| `endcard` | `logos[]`、`sub` | 深色面板擦入加官方 Logo(燃剪) |
| `image` | `src`、`x`、`y`、`w` | 静态图(Logo 等),淡入淡出 |

### 4.4 captions(字幕,采访类)

```json
"captions": { "enabled": true, "max": 17, "size": 46, "bottom": 90 }
```

由 `captions.py` 从 words.json 生成,编译时并入 timeline。断句规则:先按标点和停顿断;超过 `max` 的只在分词边界上拆,两边各不少于 4 字;单独的语气词不出字幕。

### 4.5 audio

```json
"audio": {
  "music": {
    "src": "audio/music.wav", "at": 0, "trim_s": 0, "gain": 0.9,
    "duck": { "under": "voice", "db": -13, "attack": 6, "release": 8 },
    "keys": [ [ { "bar": 20 }, 0.9 ], [ { "bar": 21 }, 0.4 ] ]
  },
  "sfx": [
    { "id": "slam1", "src": "audio/sfx/slam_v1.wav", "at": 16, "gain": 0.6, "note": "倒计时 3" }
  ],
  "voice_gain": 1.0
}
```

`duck.under: "voice"` 表示所有 `audio` 打开、`layer 0` 的采访镜头说话时(按 words.json 的逐字区间,前后各留几帧),音乐压低 `db`。

## 5. timeline.json(编译产物,只读)

```json
{
  "fps": 30, "width": 1920, "height": 1080, "duration": 1390, "style": "hype",
  "fonts": { "brush": "fonts/MaShanZheng-Regular.ttf" },
  "brand": { "accent": "#FF7A1A" },
  "shots": [{
    "id": "S07", "layer": 0, "cut": 330, "from": 326, "to": 360,
    "clip": "clips/S07.mp4", "clip_frames": [101, 103, 105],
    "src": "航拍/...MP4", "native_fps": 59.94, "src_in_s": 10.4, "src_out_s": 11.53,
    "clip_in_s": 9.9, "clip_out_s": 12.03,
    "frame": { "scale": 1, "x": 0, "y": 0 },
    "move": { "type": "push", "amt": 0.06, "run_from": 330, "run_to": 360, "tight": false },
    "in_tr": { "type": "whip", "len": 8 }, "out_tr": { "type": "cut", "len": 0 },
    "sky": 0, "vignette": 0.25, "hold_from": null,
    "audio": null, "note": "..."
  }],
  "texts": [ { "id": "t1", "type": "slam", "from": 16, "to": 31, "text": "3", "size": 420 } ],
  "captions": [ { "text": "我是因为受伤入坑的", "from": 640, "to": 702 } ],
  "audio": {
    "music": { "src": "audio/music.wav", "from": 0, "trim_frames": 0, "gain_keys": [[0, 0.9]], "duck_ranges": [[640, 702]], "duck_db": -13, "attack": 6, "release": 8 },
    "sfx": [ { "id": "slam1", "src": "audio/sfx/slam_v1.wav", "from": 16, "gain": 0.6 } ],
    "voice": [ { "shot": "S03", "clip": "clips/S03.mp4", "from": 600, "to": 900, "clip_start_frame": 15, "gain": 1, "fade_in": 0, "fade_out": 6 } ]
  },
  "clips": [{
    "id": "S07", "src": "航拍/...MP4", "from_s": 9.9, "to_s": 12.03,
    "profile": "dji-dlogm", "native_fps": 59.94, "fix": {}, "look": "work/looks/A2.png",
    "audio": false, "hash": "…"
  }],
  "lint": { "errors": [], "warnings": [] }
}
```

### 字段约定

- `from` / `to`:这一镜真正可见的帧区间 `[from, to)`,已包含转场重叠。`cut` 是名义切点。
- `clip_frames[k]`:可见区间第 k 帧该取片段里的第几帧。片段按「保留全部原生帧、按 30 fps 播放」切出,所以片段第 i 帧对应源时间 `clip_in_s + i / native_fps`。变速、定格都已经算进这个数组,模板逐帧取画的写法是 `<Freeze frame={0}><OffthreadVideo trimBefore={clip_frames[k]} muted/></Freeze>`。⚠️ 不能写成 `<Freeze frame={clip_frames[k]}>`:Remotion 会把冻结帧号夹到「合成时长 − 1」以内,片段帧号一大就卡住。
- `out_tr`:下一镜的 `in_tr` 抄过来,方便上一镜处理自己那一半转场。
- `audio.voice[*].clip_start_frame`:片段的声音从第几帧(30 fps)开始放。片段的声音始终是原速,不受 conform 影响。
- `clips`:`make_clips.py` 的输入,一镜一条。参数(`hash`)不变就跳过,不重切。

## 6. beats.json 和 words.json

**beats.json**(`tempo.py` 写):

```json
{ "bpm": 124.06, "beat_s": 0.48365, "offset_s": 0.012, "beats_per_bar": 4, "lead_frames": 1, "fps": 30,
  "trim_s": 0, "src": "public/audio/music.wav", "fit": { "residual_ms": 38 }, "preview": [[0, 0], [1, 14.5]] }
```

- `offset_s`:音乐从成片第 0 帧开始放时,第 0 小节头落在成片的第几秒。
- 如果 edit.json 里 `audio.music.at` 或 `trim_s` 和测拍时不同,`timeline.py` 会自动平移网格。
- 小节头靠低频重音投票判断,拿不准时会提示,这时用 `tempo.py --downbeat <秒>` 手动指定。

**words.json**(`paper_edit.py` 写):

```json
[ { "t": "钓", "f": 640.2, "e": 646.0, "src": "interview/A001….MP4", "shot": "IV003", "ss": 191.34, "se": 191.53 } ]
```

- `f` / `e` 是成片帧(可以带小数),`ss` / `se` 是源素材秒。
- `timeline.py` 按每个镜头的实际位置重算逐字时间。纸面剪辑之后挪动或插入镜头,不用重跑 `paper_edit.py`。

## 7. 编译器约定(`timeline.py` 的实际行为)

- **lead 只作用于画面**:镜头、文字、段落按拍定位时减 `lead_frames`(画面比声音早 1 帧);音效和音量关键点不减。第 0 拍减完是 −1,夹到 0。
- **按拍写的时长,结尾落在网格上**:连续顺接不会累积误差。起点离 8 分音符 ≤ 2 帧时先吸附过去。`offset` 只挪本镜切点,不带动下一镜。
- **默认值**:
  - `duration: {"bars": n}` 等于第 n 小节线。主轨最后一镜早结束、差距不超过 lead 帧时,自动补齐。
  - 镜头缺 `dur` 时,一直取到同层下一镜的 `at`。
  - 文字默认时长:`slam` 15 帧、`qcard` 115 帧;`lockup` / `endcard` 默认到片尾。
  - `audio: true` 时 gain 为 1,淡入淡出各 2 帧。
  - 暗角默认值按风格:testimonial 0.15、launch 0.2、hype 0.25、manifesto 0.3、custom 0.2。
- **interview 机位运行**:同一素材、首尾相接的 interview 镜头算一段;不到 15 帧的碎片沿用上一档。
- **edit.json 还能写的字段**:
  - `sections`:`[{"name":"drop","at":T,"dur":D}]`,会进卡点表和审核台;
  - `captions.gap` / `captions.dict`;
  - `duck.pad_pre` / `duck.pad_post` / `duck.merge`:默认 3 帧 / 6 帧 / attack + release。
- **timeline.json 比第 5 节多出的字段**:
  - shots:`end`、`speed`、`hold_from`(绝对帧)、in_tr / out_tr 的 `pre` / `post`。普通运镜的 `run_from` / `run_to` 等于可见区间。
  - 顶层:`grid`(已换算到成片时间)、`caption_style`、`sections`。
  - clips:`from_frame`、`frames`。`make_clips.py` 按帧切:片段第 i 帧 = 源素材第 `from_frame + i` 个原生帧。
  - voice:`clip_start_s`、`layer`。
- **有错误时**:timeline.json 照样写出(方便看),但退出码为 1。渲染前必须清零。

## 8. 两次实测后新增（2026-11-12）

### beats.json

- `accents`：重音表。每项 `{t: 音乐文件秒, s: 0–1 强度百分位, kind: "kick|snare|hit", eighth: 最近的 8 分音符序号, dev_ms: 离它多少毫秒}`。
- `accent_stats`：重音表的统计。
- `onset_density`：`{mean: 每秒起音数, max, active_s}`。
- 剪辑后的配乐用 `tempo.py --accents-only`：沿用已有网格，只重测重音。

### edit.json 新写法

| 写法 | 意思 |
|---|---|
| `{"accent": n}` | 重音表第 n 项（从 0 数，就是 `tempo.py` 打印的 # 号），按 `audio.music.at` / `trim_s` 平移到成片。画面会减 `lead_frames`，音效不减。用它定某镜的 `at` 时，上一镜不要写 `dur` |
| 镜头 `"snap": "accent"` | 按 `at` / `dur` 排好后，把切点吸附到 ±4 帧内最近的强重音（`s ≥ 0.7`，同距离取更强的）。同层上一镜的结尾离旧切点 ≤ 4 帧时，跟着一起挪。找不到就警告 |
| `speed` 关键点的位置写 T | `[[0, 1], [{"beat": 34}, 0.5], [1, 1]]`。0–1 的数字照旧当镜头内进度，写成时间点就按成片时间换算成进度 |
| `sections[].kind` | `intro / build / stop / drop / breakdown / end / other`。`name` 里含 drop 也认作 drop。lint 按 kind 查各段规则 |
| 镜头 `"allow_reuse": true` | 允许这一镜和同一素材的其他镜头源区间重叠或多次使用。lint 会列出来但不报错，`note` 里写理由 |
| Logo | `endcard.logos` 的每项、`image.src` 都可以写字符串或 `{"src", "w"}`；缺 `src` 或类型不对会报错 |

### timeline.json 新字段

| 字段 | 说明 |
|---|---|
| `accents` | 成片帧的重音表 `{f, t, s, kind}`。**`f` 没减 lead**，是音频时刻；画面要对准它，就放在 `f − grid.lead_frames` |
| `sections` | `{name, kind, from, to}` |
| `cuts` | 主轨每个切点 `{f, shot, type, layer, accent, acc_d}`。`acc_d` 是离最近强重音多少帧 |
| `expected_dark` | `[[from, to, 原因]]`：dip 转场、`src: null` 黑场镜头、深底落版卡。qa 只把这之外的黑场当问题 |
| `stats` | 每段镜数、平均镜长、变速镜头数、drop 段重音命中率、碎镜比例、不同素材数 |
| shots 新增 | `src_used_s`：实际用到的源区间；`snapped`：`{from, to, accent}` |

### lint 新规则（错误 = 退出码 1）

| 规则 | 适用 | 错误 | 警告 |
|---|---|---|---|
| 同一素材源区间重叠 > 0.2 s | 全部风格 | ✓（`allow_reuse` 豁免） | |
| 同一素材用 ≥ 3 次；不同素材数 / 镜头数 < 0.7 | hype / launch / manifesto | | ✓ |
| drop 段硬切落在强重音 ±2 帧内的比例（没有 drop 段就算全片） | hype | < 60% | < 75% |
| 同上 | launch | | < 60% |
| drop 段变速坡（`speed` 是关键点数组、最小值 < 0.9） | hype | 0 处 | < 2 处 |
| 连续恒速镜头 > 4 个 | hype | | ✓ |
| drop 段短于 1.5 s 的镜头：个数 > 40% **且**时长 > 30% | hype | | ✓ |
| 配乐起音 > 15 次 / 秒，且 drop 平均镜长 < 1.6 s | hype | | ✓ |

### 其他工作文件

| 文件 | 由谁写 | 内容（字段见脚本开头） |
|---|---|---|
| `work/inventory-failed.json` | `inventory.py` | 盘点对账：status、counts（discovered / analyzed / failed / excluded）、失败原因、扫描范围 |
| `work/coverage.json` | `coverage.py` | 素材总数、已用、按目录的未用清单、复用、源区间重叠、镜头日志缺漏 |
| `work/capabilities.json` | `capabilities.py` | music / sfx / transcribe 各自的来源、传输方式、凭证状态、是否需要用户配置 |
| `work/state.json`、`work/events.jsonl` | `state.py` | 节点审批（用户原话 + 输入文件 hash）和阶段事件 |
| `work/render/<名>.json` | `render.py` | 渲染命令、帧数、各文件 sha256、混音响度 |
| `work/qa/<名>.QA.json` | `qa.py` | 每项检查的结果、阈值、覆盖范围 |
