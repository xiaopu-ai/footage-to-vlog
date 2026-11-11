---
name: footage-to-vlog
description: 把一批实拍原始素材（采访 / 口播、产品、航拍、活动；Sony S-Log3、大疆 D-Log M 等 Log 灰片也行）剪成有风格的成片：人物见证访谈、产品发布大片、红牛式卡点燃剪、品牌口号 / 宣言片。流程：素材盘点 → 定风格 → 配乐和节拍 → 调色 → 风格帧审核 → 分镜和节奏预演 → 切片段 + Remotion 合成 → 自审交付。用户说「这批素材剪个片」「做个 vlog / 品牌片 / 燃剪 / 宣传片 / 口号片」「同一批素材换个风格再剪一版」时使用。Turn raw camera footage into a styled film (testimonial, product launch, hype cut, manifesto) with ffmpeg + Remotion.
---

# footage-to-vlog · 实拍素材剪成片

这套流程在一个真实客户项目的素材上跑通了四种风格，四条都定版交付。它不依赖某个模型，也不依赖 ChatCut、剪映这类剪辑软件：

- **颜色和节拍交给脚本**：判机型套官方 LUT、HALD 色表、切片段、测拍、卡点核对、响度，都是确定性的命令。
- **剪辑决定写成数据**：你只写 `edit.json`，`timeline.py` 编译成帧并按风格做检查。
- **画面质量交给模板**：`template/` 是一个通用的 Remotion 项目，用户审过的组件都在里面。不要从头写特效。
- **人在三个节点拍板**：结构和配乐、风格帧、分镜和节奏。每个节点都要停下来等用户确认，不要一口气做到整片。

## 0 · 开工前

**需要的环境**（跑 `scripts/doctor.sh` 检查）：
- Node ≥ 18、ffmpeg（要有 libx264 和 lut3d、haldclut 滤镜）、Python 3.9+ 及 numpy、Pillow、jieba（`pip install numpy pillow jieba`；扫二维码另需 `opencv-python-headless`）。
- `ELEVENLABS_API_KEY`：配乐、音效、转写都靠它。没有的话，配乐和音效让用户提供，转写可以换成别的带逐字时间码的 ASR。
- 能看图更好：写镜头日志、自审静帧要靠眼睛。如果你的模型看不了图，镜头日志请用户补，每个审核节点都让用户看图拍板；数值检查（亮度、居中、卡点、响度）照常跑。

**先问清楚**（用户没说就问，问完再动手）：
1. 素材在哪；有没有品牌官方物料：Logo 文件、口号的官方写法、产品参数表。
2. 做哪种风格、多长、给谁看、发在哪。不确定就先做第 1 步盘点，再按 `references/style-picker.md` 给出 2–3 个建议。
3. 有没有对标片。有就下载下来逐镜拆解，没有就用本 skill 风格手册里的对标。

**建项目**：

```bash
export SK=<本 skill 目录的绝对路径>          # 下文和 references 里的 scripts/、review/、assets/ 都指这里
export RENDER_QUEUE=$SK/scripts/queue.sh    # 静帧和审核台渲图会自动排队
bash $SK/scripts/fetch_luts.sh             # 第一次用：从 Sony / 大疆官方地址下载技术还原 LUT
bash $SK/scripts/doctor.sh                 # 检查环境
cp -R $SK/template <proj> && cd <proj> && npm install
```

- 写 `project.json`，格式见 `references/edit-schema.md` 第 2 节。LUT 直接指向 `$SK/assets/luts/`。
- 素材、配乐、Logo 放进 `public/` 时**复制或用硬链接，不要用文件软链**：Remotion 的静态服务器拒绝文件软链，会 404、渲染直接失败。整个目录做软链可以。
- 机器上已经有别的渲染队列时，设 `FTV_LOCK=<那个队列的锁文件>`、`FTV_HELD_ENV=<它的「已持锁」变量名>`，共用同一把锁。

🔴 **全机同一时间只跑一个重任务**：视频转码、切片段、批量抽帧、静帧批量、整片渲染，一律套 `scripts/queue.sh <命令>` 排队。并行会把笔记本 CPU 烧到 100°C。

## 1 · 素材盘点

```bash
python3 $SK/scripts/inventory.py <footage_root> --out <proj>/work      # 会自动排队；以 . 或 _ 开头的目录不扫
```

- 产出 `work/footage.json`，记录每条素材的帧率、分辨率、机型曲线（`sony-slog3` / `dji-dlogm` / `rec709`）。认错了就在 `project.json` 的 `profiles` 里改。
- 产出联系表 `work/contact/*.jpg`，已套 LUT，可以直接看。
- 逐条填 `work/shotlog.md`：内容、好用的时刻（秒）、问题（商标、船号 / 车牌、第三方品牌、二维码、电话、地图、误导性画面）、适合哪种风格。

## 2 · 定风格和结构 → 🛑 用户确认

- 读 `references/style-picker.md` 和对应的风格手册：`references/styles/<style>.md`。
- 写 `work/PLAN.md`：
  - 风格和对标；
  - 时长；
  - 段落结构，用小节或秒表示，每段干什么、用哪类镜头；
  - 文字包装；
  - 调色方向；
  - 配乐方向。
- 采访类还要先转写，再做纸面剪辑，见第 3 步。
- **停下来给用户看 PLAN.md**，确认后再往下做。

## 3 · 配乐和节拍（采访类加转写）→ 🛑 用户选配乐

**配乐**，详见 `references/music-sfx.md`：
1. 从 `assets/music-plans/<style>.plan.json` 改出作曲计划。
2. 生成 2–3 首：`python3 $SK/scripts/music_gen.py plan.json work/music/a.mp3`。并发上限 2，一首一首来。
3. 用户选一首。
4. 测拍：`python3 $SK/scripts/tempo.py work/music/a.mp3 --out beats.json`；结构用 `bands.py` 按小节看。小节头拿不准时加 `--downbeat <秒>`。常见的测拍工具会把 124 测成 83 / 164，要用本 skill 的 `tempo.py`。
5. 按段落结构写 bar_edit EDL，按小节重剪：`python3 $SK/scripts/bar_edit.py src.mp3 public/audio/music.wav edl.json`。
6. 重剪后再测一次，更新 `beats.json`。

**采访**，详见 `references/interview-edit.md`：
1. 转写：`python3 $SK/scripts/transcribe.py <采访文件…> --out work/transcripts --keyterms "品牌名,型号"`。必须从素材第 0 秒开始转。
2. 从 `.sentences.md` 抄秒数，写 `work/paper_edit.json`。
3. 生成镜头和逐字时间：`python3 $SK/scripts/paper_edit.py <proj>` → `work/paper_edit.shots.json`（粘进 edit.json）+ `words.json`。
4. 删掉违规说法后，检查前后句是否说完整。

## 4 · 调色和风格帧 → 🛑 用户选调色、字体、版式

详见 `references/color.md`。

1. 挑 4–6 个代表镜头抽帧：`python3 $SK/scripts/grab.py <素材相对路径> <秒>… --proj <proj>`，抽出来已经套了技术 LUT。
2. 做 2–4 套色表对比：先 `python3 $SK/scripts/look.py build --builtin --out <proj>/work/looks/`（A / B / A2 三套起点），再用 `look.py build <名> "<滤镜链>"` 加自己的，最后 `look.py preview <png…> --frames <jpg…>` 出对比图。
3. 写一个只有风格帧镜头的 `edit.json`，编译后用模板的 `Frame` 合成渲出带字的风格帧：

```bash
python3 $SK/scripts/timeline.py <proj>
python3 $SK/scripts/make_clips.py <proj> --draft                         # 会自动排队
python3 $SK/review/build_review.py <proj> --kind sf --serve --port 8765  # 出审核台，把 http://localhost:8765/ 给用户
```

4. 用户在审核台逐张给意见，意见存在 `work/review/sf/feedback.json`，然后把选定的色表写进 `project.json` 的 `look`。

## 5 · 分镜和节奏预演 → 🛑 用户逐镜通过

1. 写完整的 `edit.json`（格式：`references/edit-schema.md`）：
   - 镜头按拍或小节排，采访按口播排；
   - 每镜写 `note`；
   - 转场、文字、音效、音量都写进去。
2. 编译：`python3 $SK/scripts/timeline.py <proj>`。lint 报错必须清零，警告逐条看。
3. 逐镜核对素材：`python3 $SK/scripts/grab.py --proj <proj> --shots all --at start,mid,end` 抽每镜的首、中、尾三帧，看有没有商标、穿帮、文不对题。按镜头日志排的分镜，经验上三分之一要换。
4. 出分镜审核台：`make_clips.py <proj> --draft`，再 `build_review.py <proj> --kind board --serve`。
5. **节奏预演**：开场 10–15 秒用 draft 片段渲成半分辨率小样给用户看：`$SK/scripts/queue.sh npx remotion render src/index.ts Film out/preview.mp4 --frames=0-449 --scale=0.5 --gl=angle --concurrency=4 --bundle-cache=false`。节奏只有动起来才看得出，静帧分镜全过、整片被推翻的事发生过。

## 6 · 切片段 + 整片

```bash
python3 $SK/scripts/make_clips.py <proj>          # final 质量，会自动排队；--check 只核对不切
npx tsc --noEmit
$SK/scripts/queue.sh npx remotion render src/index.ts Film out/raw.mp4 --gl=angle --concurrency=4 --timeout=1800000 --bundle-cache=false
```

- 渲染前过一遍 `references/qa.md` 的「渲前清单」。
- 用到的模板组件和参数见 `template/README.md`。

## 7 · 自审和交付

逐条跑 `references/qa.md` 的「渲后检查」，全过才能交付：
- 联系表：`scripts/sheet.py --video out/raw.mp4 --fps 2`；
- 黑场、卡点：`$SK/scripts/queue.sh python3 $SK/scripts/synccheck.py out/raw.mp4 <proj>`；
- 抖动：`scripts/jitter_scan.py`；
- 响度：先限幅，再 `scripts/loudnorm.py --I -14 --TP -1.5`；
- 二维码：`scripts/qr_scan.py`；
- 卡点表：`scripts/cuesheet.py`。

交付物：
- 成片；
- 纯人声版（采访类）或无声版（纯音乐类）；
- 卡点表。

**没有亲眼看过真实渲染结果，不说「完成」。**

## 必守规则（违反就返工；全部来自用户审片）

1. **审核台前置**：结构 → 配乐 → 风格帧 → 分镜 + 节奏预演，每一步都等用户确认。跳过审核台的那条片子，整片改了 4 轮。
2. **采访说话中一律硬切**。只在问题卡或章节卡出现时用小转场（蒙版擦入），前景字卡停着的时候背后不做大幅转场。
3. **画面不说谎**：说越野就不配水上摩托；素材里没有的东西，用人加信息卡讲。**字卡、包装不挡人脸**，采访画面左移让位。
4. **字要清楚**：毛笔字用清楚好认的（模板默认马善政；想用演示夏行楷要先确认授权、自己下载），不用程序飞白和抽象行草。组合元素按 1:1 静帧量像素居中，误差 ≤ 2 px。
5. **字幕只在词边界断**，两边各不少于 4 字；单独的语气词不出字幕。
6. **「冷峻」不等于「暗」**：黑位不低于约 0.05，冷感靠天空压暗和暗部偏青，人脸不压黑。全片同段镜头的 p5 / p95 差不超过 3 个百分点，中间调冷暖统一。
7. **配乐第一小节就要有钩子**：不要慢前奏、平铺直叙、战争片式史诗；卡点类片子开头 3 秒内要有重击。生成结果不按计划走是常态，先测再剪。
8. **卡点**：
   - 切点吸附 8 分音符；
   - 小节头的刀放在当帧或前 1 帧；
   - 成片实测画面比音频领先 0–2 帧；
   - 85% 以上的切点误差在 ±1.5 帧内。
9. **慢放不低于素材原生帧率**：60p 最慢 0.5×，120p 最慢 0.25×，不插帧。
10. **音效少而准**：访谈类 10–15 个，只放关键节点，不用短促尖锐的 tick / pop；燃剪可以分层放到约 20 个。
11. **合规**：
    - 画面里不出现二维码、电话、地图 / 地球仪；
    - 第三方商标、车牌 / 船号要换镜或裁掉；
    - 口播和字卡不用「最、第一、唯一、无可匹敌」这类绝对化用语；
    - 参数逐条对官方参数表。

    详见 `references/compliance.md`。
12. **官方物料原样用**：Logo、口号写法都照官方，不重绘、不改字。

## 文件地图

| 路径 | 内容 |
|---|---|
| `references/edit-schema.md` | project.json / edit.json / timeline.json 格式（必读） |
| `references/style-picker.md` | 素材能撑起哪些风格、怎么挑 |
| `references/styles/testimonial.md` | 人物见证访谈（示例 3:52） |
| `references/styles/launch.md` | 产品发布大片（示例 33.7 s） |
| `references/styles/hype.md` | 红牛式卡点燃剪（46 s 主版 + 15 s） |
| `references/styles/manifesto.md` | 品牌口号 / 宣言片（60 s，四章） |
| `references/color.md` | 机型识别、LUT、HALD 色表、逐镜校色 |
| `references/music-sfx.md` | 作曲计划、测拍、按小节重剪、音效、混音 |
| `references/interview-edit.md` | 转写、纸面剪辑、字幕、合规删句 |
| `references/review.md` | 审核台、风格帧、分镜、节奏预演 |
| `references/qa.md` | 渲前清单、渲后检查、交付 |
| `references/pitfalls.md` | 工程坑（Remotion、ffmpeg、权限、性能） |
| `references/compliance.md` | 平台合规和品牌规范 |
| `scripts/` | 全部命令行工具，每个文件开头写了用法 |
| `template/` | Remotion 项目模板（组件说明见其 README） |
| `review/` | 本地审核台 |
| `assets/` | LUT、作曲计划模板、bar_edit 示例 |
