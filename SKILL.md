---
name: footage-to-vlog
description: 把一批实拍原始素材（采访 / 口播、产品、航拍、活动；Sony S-Log3、大疆 D-Log M 等 Log 灰片也行）剪成有风格的成片：人物见证访谈、产品发布大片、红牛式卡点燃剪、品牌口号 / 宣言片。流程：素材盘点 → 定风格 → 配乐和节拍 → 调色 → 风格帧审核 → 分镜和节奏预演 → 切片段 + Remotion 合成 → 自审交付。用户说「这批素材剪个片」「做个 vlog / 品牌片 / 燃剪 / 宣传片 / 口号片」「同一批素材换个风格再剪一版」时使用。Turn raw camera footage into a styled film (testimonial, product launch, hype cut, manifesto) with ffmpeg + Remotion.
---

# footage-to-vlog · 实拍素材剪成片

这套流程在一个真实客户项目的素材上跑通了四种风格，四条都定版交付；后来又在 Codex 和 DeepSeek 两个 agent 上各实跑过一次。它不依赖某个模型，也不依赖剪映、ChatCut 这类剪辑软件。

- **颜色和节拍交给脚本**：判机型套官方 LUT、HALD 色表、切片段、测拍和重音、卡点核对、响度、封装，都是确定性的命令。
- **剪辑决定写成数据**：你只写 `edit.json`。`timeline.py` 把它编译成帧，并按风格检查：重音命中、变速坡、素材复用、慢放极限等。
- **画面质量交给模板**：`template/` 是一个通用的 Remotion 项目，用户审过的组件都在里面。不要从头写特效。
- **人在六个节点拍板**：方案 → 配乐 → 风格帧 → 分镜 → 节奏预演 → 成片。节点的唯一定义见 `references/review.md`。每个节点都停下来等用户确认，不要一口气做到整片。
- **规则分级**见 `references/rules.md`：A 用户硬要求、B 技术约束必须遵守；C 风格建议可以偏离，但要写理由；D 示例参数仅供参考。

## 0 · 开工前（这一步不做完，不进第 1 步）

**环境**：

```bash
export SK=<本 skill 目录的绝对路径>          # 下文和 references 里的 scripts/、review/、assets/ 都指这里
export RENDER_QUEUE=$SK/scripts/queue.sh    # 静帧和审核台渲图会自动排队
bash $SK/scripts/fetch_luts.sh             # 第一次用：从 Sony / 大疆官方地址下载技术还原 LUT
bash $SK/scripts/doctor.sh                 # 检查环境，缺什么装什么，直到全部打勾
cp -R $SK/template <proj> && cd <proj> && npm install
cp $SK/assets/templates/PLAN.md <proj>/work/PLAN.md
```

- 需要 Node ≥ 18、ffmpeg（libx264、lut3d、haldclut）、Python 3.9+ 及 numpy、Pillow、jieba；扫二维码另需 opencv-python-headless。
- 写 `project.json`，格式见 `references/edit-schema.md` 第 2 节。LUT 直接指向 `$SK/assets/luts/`。
- 素材、配乐、Logo 放进 `public/` 时**复制或用硬链接，不要用文件软链**：Remotion 会 404。目录软链可以。
- 机器上已有别的渲染队列时，设 `FTV_LOCK` / `FTV_HELD_ENV` 共用一把锁（见 `scripts/queue.sh` 文件头）。

🔴 **全机同一时间只跑一个重任务**：转码、切片段、批量抽帧、静帧批量、整片渲染一律经 `queue.sh` 排队。脚本大多会自己排队，排队时还会阻止电脑休眠。并行会把笔记本 CPU 烧到 100°C。

**能力探测**：配乐、音效、转写的来源，详见 `references/music-sfx.md` 第 0 节。按顺序找：
1. 用户给的文件；
2. 你宿主里已经连着的生成工具；
3. `ELEVENLABS_API_KEY`；
4. 都没有，才问用户。

```bash
python3 $SK/scripts/capabilities.py <proj> [--music-file …] [--host-tool "工具名:能做什么"]
```

结果在 `work/capabilities.json`。**不要只因为缺 ElevenLabs key 就停下来要 key。**

**审核介质**：从第 1 个节点起，所有给用户看、听的东西都挂在本地网页上（`review/build_review.py` + `review/serve_local.py`，支持 Safari 播放），把 `http://127.0.0.1:<端口>/` 单独一行发给用户。不要在对话里直接贴 mp3 / mp4 / 图片：很多 agent 的界面放不了。详见 `references/review.md`。

**先问清楚**（用户没说就问）：
1. 素材在哪；官方物料：Logo 文件、口号写法、产品参数表。
2. 做哪种风格、多长、画幅、给谁看、发在哪。「发抖音」不自动等于竖屏，没说就问。不确定风格就先做第 1 步盘点，再按 `references/style-picker.md` 给出 2–3 个建议。
3. 品牌层级：传播主体是谁、画面里的产品标识是什么、落版用哪个 Logo。写进 PLAN 的品牌层级表。
4. 有没有对标片。

能看图更好。看不了图的模型，镜头日志请用户补，每个审核节点都让用户看图拍板；数值检查照常跑。

## 1 · 素材盘点（要对账）

```bash
python3 $SK/scripts/inventory.py <footage_root> --out <proj>/work [--include 子目录…]
```

- 产出：
  - `work/footage.json`：帧率、分辨率、机型曲线 `sony-slog3` / `dji-dlogm` / `rec709`；认错了在 `project.json` 的 `profiles` 里改；
  - `work/contact/*.jpg`：联系表，已套 LUT。
- 🔴 **对账**：输出里的「发现 = 分析成功 + 失败」要对上，排除的单列。
  - 有失败时退出码是 2（不完整），失败清单在 `work/inventory-failed.json`，修好后 `--retry-failed`。
  - **不完整的盘点不能往下走。**
- **镜头日志 `work/shotlog.md` 要覆盖每一条素材**：
  - 每条至少看 25% / 50% / 75% 三个时刻（`grab.py`）；
  - 写清内容、好用的时刻（秒）、问题（商标、号码、第三方品牌、二维码、误导画面）、适合哪种风格。
  - 联系表只用来初筛，不能直接拿来定分镜。`coverage.py` 会查日志有没有漏条。

## 2 · 方案 → 🛑 节点 1 `plan`

- 读 `references/style-picker.md` 和对应的风格手册 `references/styles/<style>.md`。
- 照 `assets/templates/PLAN.md` 写 `work/PLAN.md`：
  - 需求、品牌层级、对标；
  - 段落结构，每段标 `kind`，会进 `edit.json` 的 `sections`；
  - 素材使用计划：预计用多少条不同素材；
  - 配乐来源和方向、调色和文字方向。
- **候选池从全部镜头日志里挑**，不要在这一步就收窄到十几条。
- 停下来给用户看。确认后：`python3 $SK/scripts/state.py <proj> approve plan --quote "用户原话"`。

## 3 · 配乐和节拍（采访类加转写）→ 🛑 节点 2 `music`

**配乐**，详见 `references/music-sfx.md`：
1. 按能力探测的结果取得 2–3 首候选。有费用的先报价，用户同意再生成。
2. 用 media 页给用户试听，用户选一首。
3. 测拍和重音：`python3 $SK/scripts/tempo.py <曲子> --out beats.json`；`bands.py` 按小节看结构和起音密度。
4. 按段落结构写 bar_edit EDL，按小节重剪：`python3 $SK/scripts/bar_edit.py src.mp3 public/audio/music.wav edl.json`。
5. 重剪后**再测一次**，更新 `beats.json`，里面有重音表 `accents`。重剪版也给用户听一遍，再 `state.py approve music`。

**采访**，详见 `references/interview-edit.md`：
1. 转写：从素材第 0 秒开始转。
2. 从 `.sentences.md` 抄秒数，写 `work/paper_edit.json`，再用 `paper_edit.py` 生成镜头和 `words.json`。
3. 删掉违规说法后，检查前后句是否说完整。

## 4 · 调色和风格帧 → 🛑 节点 3 `look`

详见 `references/color.md`。
1. 挑 4–6 个代表镜头抽帧（`grab.py`），做 2–4 套色表对比（`look.py`）。
2. 写只含风格帧镜头的 `edit.json` → `timeline.py` → `make_clips.py --draft` → `build_review.py --kind sf`。
3. 落版 Logo 在风格帧里就按 PLAN 的品牌层级放好。
4. 强对比色表不要和 `levels: "auto"` 叠着用；`measure.py` 报「冲突」就改。
5. 用户选定后，把色表写进 `project.json` 的 `look`，然后 `state.py approve look`。

## 5 · 分镜和节奏预演 → 🛑 节点 4 `board`、节点 5 `rhythm`

1. 写完整的 `edit.json`（格式见 `references/edit-schema.md`）：
   - 镜头按重音、拍或小节排，采访按口播排；
   - 写 `sections`；每镜写 `note`；
   - 转场、文字、音效、音量都写进去。
2. 编译 `python3 $SK/scripts/timeline.py <proj>`：**错误必须清零**，警告逐条看，C 级例外写理由。卡点类风格会查：
   - 重音命中率；
   - drop 段有没有变速坡、碎镜比例；
   - 素材复用。
3. `python3 $SK/scripts/coverage.py <proj>`：素材用了多少、复用最多的是哪条。用得少就回镜头日志里找新镜头，不要只在已有候选里互换。
4. 逐镜核对：`grab.py --proj <proj> --shots all --at start,mid,end`，看商标、号码、穿帮、文不对题。
5. 分镜审核台：`make_clips.py --draft` → `build_review.py --kind board`。用户逐镜通过后 `state.py approve board`。
6. **节奏预演**：
   - `python3 $SK/scripts/render.py <proj> --preview 15 --allow-draft` → `qa.py --kind preview`；
   - 自己先按 `references/review.md` 的节奏清单看一遍，再挂 media 页给用户看；
   - 通过后 `state.py approve rhythm`。

## 6 · 整片

```bash
python3 $SK/scripts/make_clips.py <proj>                  # 片段切成 final 质量（会自动排队）
npx tsc --noEmit
python3 $SK/scripts/render.py <proj> --name <片名>-v1      # 静音渲画面 → 规范混音 → 限幅 + 响度 → 封装 + 色彩标签 → 无声版 / 纯人声版
```

- 渲染前过一遍 `references/qa.md` 的「渲前清单」。
- **不要直接用 `npx remotion render` 的输出交付**：直出的音轨晚约 40 ms，色彩是全范围 BT.601。`render.py` 会处理好。

## 7 · 自审和交付 → 🛑 节点 6 `film`

```bash
python3 $SK/scripts/qa.py <proj> out/<片名>-v1.mp4 --kind film
python3 $SK/scripts/cuesheet.py <proj> --out out/<片名>-v1-卡点表.md
```

- `qa.py` 任何一项 fail，退出码就非零，结果在 `work/qa/`。检查项包括：
  - 帧数、色彩标签；
  - 音画对齐、响度、真峰值；
  - 切点和重音命中；
  - 意外黑场、抖动、二维码（按镜头补帧扫）；
  - 素材覆盖。
- 汇报时只说跑过的检查，写清覆盖范围；C 级例外单独列（见 `references/rules.md`）。
- 成片挂 media 页给用户看。定版后 `state.py approve film --quote "用户原话"`。
- 交付物：
  - 成片；
  - 无声版或纯人声版；
  - 卡点表；
  - QA 报告；
  - `HANDOFF.md`。

**没有亲眼看过真实渲染结果，不说「完成」。** 用户定版说明用户接受了成片，不等于每条规则都满足了。

## 必守规则速查

完整版见 `references/rules.md`。

1. **六个审核节点都停下来等用户**，审核材料全部挂本地网页。
2. **采访说话中一律硬切**。只在问题卡、章节卡出现时用小转场。
3. **画面不说谎，字卡不挡脸。** 素材里没有的东西用人加信息卡讲。
4. **字要清楚**：毛笔字用清楚好认的（模板默认马善政），不用程序飞白和抽象行草。组合元素按 1:1 静帧量像素居中，误差 ≤ 2 px。
5. **字幕只在词边界断。**
6. **「冷峻」不等于「暗」**：人脸和暗部不压死；强对比色表不叠 `levels: auto`。同段亮度差是告警，不是硬门槛。
7. **配乐第一小节就要有钩子**：不要慢前奏、平铺、战争片式史诗；卡点类开头 3 秒内要有重击。
8. **卡点要卡在重音上，不只是网格上**：
   - 燃剪 drop 段的硬切至少 60% 落在强重音 ±2 帧内；
   - 画面领先音频 0–2 帧（`lead_frames > 0` 表示画面先到）；
   - 配乐每秒起音超过 15 次时，按 4 分音符和重音切。
9. **燃剪要有速度变化，镜长按动作排**：
   - drop 段至少一处变速坡，连续恒速镜头不超过 4 个；
   - 动作留满，不机械地按固定拍数切；
   - 慢放不低于原生帧率。
10. **素材铺开用**：同一素材源区间不重叠，同一条最多用 2 次；不同素材数 / 镜头数 ≥ 0.7。
11. **音效少而准。**
12. **合规和品牌**：
    - 画面里不出现二维码、电话、地图、第三方商标、车牌 / 船号；
    - 不用绝对化用语；参数对官方表；
    - Logo、口号原样用；落版按 PLAN 的品牌层级。
    
    详见 `references/compliance.md`。

## 文件地图

| 路径 | 内容 |
|---|---|
| `references/edit-schema.md` | project.json / edit.json / timeline.json / beats.json 格式（必读） |
| `references/rules.md` | 规则分级，以及怎么汇报「检查通过」 |
| `references/review.md` | 六个审核节点的唯一定义、审核网页、节奏预演清单 |
| `references/style-picker.md` | 素材能撑起哪些风格、怎么挑 |
| `references/styles/*.md` | 四种风格手册：testimonial / launch / hype / manifesto |
| `references/color.md` | 机型识别、LUT、HALD 色表、逐镜校色、亮度测量口径 |
| `references/music-sfx.md` | 能力探测、作曲计划、测拍和重音、按小节重剪、音效、混音 |
| `references/interview-edit.md` | 转写、纸面剪辑、字幕、合规删句 |
| `references/qa.md` | 渲前清单、`render.py`、`qa.py`、卡点正负号、交付 |
| `references/pitfalls.md` | 工程坑：Remotion、ffmpeg、权限、休眠、审核介质 |
| `references/compliance.md` | 平台合规和品牌规范 |
| `assets/templates/PLAN.md` | 方案模板（含品牌层级、素材使用计划） |
| `scripts/` | 全部命令行工具，每个文件开头写了用法和检查边界 |
| `template/` | Remotion 项目模板（组件说明见其 README） |
| `review/` | 本地审核台：静帧页、媒体播放页、支持 Range 的服务器 |
| `assets/` | LUT（安装时下载）、作曲计划模板、bar_edit 示例、PLAN 模板 |
