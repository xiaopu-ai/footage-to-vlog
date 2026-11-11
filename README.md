# footage-to-vlog

**把一堆实拍素材交给 AI，剪出有风格的成片。**

适用的 agent：Codex、Claude Code、DeepSeek 等能跑命令行的 coding agent。

给它一个素材文件夹（采访、产品、航拍都行，Sony S-Log3、大疆 D-Log M 这类灰片也行），说清楚要哪种片子。它会一步步做下来：

- 盘点素材，给出方案；
- 生成配乐、调色；
- 出风格帧和分镜，让你在浏览器里审；
- 最后渲出成片、无声版和卡点表。

| 风格 | 典型时长 | 一句话 |
|---|---|---|
| 人物见证访谈 | 3–4 分钟 | 一个人讲自己的经历，品牌想问的问题做成问题卡，说到什么画面出什么 |
| 产品发布大片 | 30–40 秒 | 无口播：开场第一帧就重击，光带扫出产品，参数大字卡在拍子上 |
| 红牛式卡点燃剪 | 45 秒 + 15 秒 | 暗场蓄力 → 加速蒙太奇 → 停拍 → drop；靠段落反差燃起来，不是全程快切 |
| 品牌口号 / 宣言片 | 60 秒 | 一章一句毛笔字，换字落在重拍上；收尾断音，静音里落版 |

这四种都有真实客户项目定版交付过。skill 里的结构、剪辑密度、字的位置、调色和配乐参数，都是那几条片子的实际数值。

## 它怎么保证不同模型剪得一样好

- **颜色和节拍交给脚本**：
  - 按机型套官方 LUT，做 HALD 色表，切片段；
  - 用稳健算法测拍，核对卡点；
  - 统一响度、扫二维码。
  
  这些都是确定性的命令，不靠模型手感。
- **剪辑决定写成数据**：agent 只写一个 `edit.json`。编译器会把它换算成帧，并按风格检查，比如说话时只能硬切、慢放不能低于素材帧率、切点要落在拍子上。
- **画面交给模板**：自带一个 Remotion 项目，包含 7 种转场和 10 种文字包装，都被真人审过。agent 不用从头写特效。
- **你在几个节点拍板**：方案 → 配乐 → 风格帧 → 分镜和节奏预演 → 成片。每个节点它都会停下来等你。

## 准备环境（只做一次）

需要 macOS 或 Linux（Windows 没测过）。

**最省事**：先按下面「安装」把 skill 装好，然后对 agent 说一句：

```
按 footage-to-vlog 的 README 帮我把环境装好：跑 scripts/doctor.sh，缺什么装什么，直到全部打勾。
```

它会自己装，你按提示点允许就行。

**手动装**（macOS，用 Homebrew）：

```bash
brew install node ffmpeg python
python3 -m pip install --user numpy pillow jieba opencv-python-headless
```

如果第二条报 `externally-managed-environment`（新版 Homebrew Python 会这样），在命令末尾加 `--break-system-packages` 再跑一次。

- **Node**：18 或更新。
- **ffmpeg**：要带 libx264，Homebrew 版就行。
- **ElevenLabs API Key**：生成配乐、音效和语音转写要用，在 elevenlabs.io 注册后拿到。放进环境变量：

```bash
echo 'export ELEVENLABS_API_KEY=你的key' >> ~/.zshrc && source ~/.zshrc
```

  没有 key 也能用，但配乐和音效就得你自己提供，采访类也没法自动转写。

## 安装

### Codex

```bash
git clone https://github.com/xiaopu-ai/footage-to-vlog.git ~/.codex/skills/footage-to-vlog
bash ~/.codex/skills/footage-to-vlog/scripts/fetch_luts.sh   # 从 Sony、大疆官方地址下载调色用的 LUT
bash ~/.codex/skills/footage-to-vlog/scripts/doctor.sh       # 检查环境，全打勾就行
```

也可以直接在 Codex 里说：「用 skill-installer 从 https://github.com/xiaopu-ai/footage-to-vlog 安装这个 skill」。

装完**重开一个 Codex 会话**，问它「你有哪些 skill」，能看到 footage-to-vlog 就对了。

### Claude Code

```bash
git clone https://github.com/xiaopu-ai/footage-to-vlog.git ~/.claude/skills/footage-to-vlog
bash ~/.claude/skills/footage-to-vlog/scripts/fetch_luts.sh
bash ~/.claude/skills/footage-to-vlog/scripts/doctor.sh
```

### DeepSeek 和其他 agent

1. 把仓库克隆到任意目录，同样跑一次 `fetch_luts.sh` 和 `doctor.sh`。
2. 放到工具认的位置：
   - 支持 Agent Skills（`SKILL.md` 格式）的工具，放进它的 skills 目录（很多工具认 `~/.agents/skills/`）；
   - 不支持的，在第一句话里告诉它：「先完整读 `<克隆路径>/SKILL.md`，按它的流程做」。

### 更新

```bash
cd ~/.codex/skills/footage-to-vlog && git pull     # Claude Code 换成 ~/.claude/skills/footage-to-vlog
```

## 第一次用：手把手

### 1. 准备

- **素材**：放进一个文件夹，比如 `~/Documents/我的素材/`。子文件夹随意，agent 只读，不会改。
- **品牌物料**（有就准备）：
  - Logo 文件，SVG 最好；
  - 口号的官方写法；
  - 产品参数表。
- **项目目录**：新建一个空文件夹，比如 `~/Documents/我的片子/`。成片、中间文件都会放在这里。

⚠️ macOS 上，agent 可能读不了「桌面」文件夹。素材请放在「文稿」或别的目录。

### 2. 打开 agent

在 Codex 或 Claude Code 里打开刚才那个空的项目目录，开一个新会话。

- Codex 的权限选能联网、能运行命令的那一档：要装 npm 包、调 ElevenLabs、下载 LUT。

### 3. 发第一句话

复制下面的模板，把尖括号换成你的内容：

```
用 footage-to-vlog 这个 skill，帮我把 <素材文件夹路径> 里的素材剪成一条 <风格> 的片子，大约 <时长>。
这是 <品牌名> 的 <什么产品 / 什么活动>，给 <谁看>，发在 <哪个平台>。
官方物料：Logo 在 <路径>；口号是「<原文>」；参数 <列出来，或参数表路径>。
项目建在当前文件夹。素材目录只读，不要改。每个审核节点停下来等我确认。
```

不知道选哪种风格，就写「先帮我看看这批素材适合做哪几种风格」。它会先盘点素材，再给你 2–3 个建议。

### 4. 它会在这几处停下来等你

| 节点 | 你要做的 |
|---|---|
| 方案（`work/PLAN.md`） | 看风格、时长、段落结构对不对，有意见直接说 |
| 配乐 | 听它生成的 2–3 首，选一首；不喜欢就说哪里不对，比如「前奏太慢」「太像战争片」 |
| 风格帧 | 它会给你一个本地网址，比如 `http://localhost:8765/`，浏览器打开就是审核台。逐张看调色、字体、版式，按 1 / 2 / 3 标「通过 / 要改 / 待定」，写意见 |
| 分镜 + 节奏预演 | 同一个审核台逐镜过；再看它渲的开场 10–15 秒小样，判断节奏快慢 |
| 成片 | 看 `out/` 里的成片。不满意就说具体哪一秒哪里不对；满意就说「定版」 |

常用的说法：
- 「第 3 镜太暗了，提亮一点」
- 「这个字体太糊，换清楚的」
- 「开头节奏太平，第一秒就要有冲击」
- 「这段说话的时候别加转场」
- 「换一首配乐，要明亮一点的」

### 5. 拿成片

交付物都在项目的 `out/` 里：
- 成片：1080p，响度 −14 LUFS；
- 无声版（纯音乐类片子）或纯人声版（采访类）；
- 卡点表：哪一秒切什么镜头、配什么音效，方便你自己改。

## 时间和费用（参考）

- 一条片子从开工到定版，agent 干活约 1.5–2.5 小时，不算你审片的时间。电脑会持续渲染，期间别同时跑别的大任务。
- ElevenLabs：每条片子大约 2–5 首配乐候选、0–20 条音效，采访类另加转写。
- 模型费用取决于你用的 agent 和套餐。

## 常见问题

- **电脑很烫、风扇狂转**：skill 会把转码、渲染排队，同一时间只跑一个。不要同时开好几个 agent 渲片。
- **LUT 下载失败**：大疆官网会把海外 IP 判到美区，详细的手动下载方法见 `assets/luts/SOURCES.md`。
- **想换字体**：见 `template/public/fonts/LICENSES.md`。默认只带可以随项目分发的 OFL 字体：思源黑体、马善政毛笔楷书、D-DIN。
- **模型看不了图**（比如纯文本模型）：流程照样能走。但素材内容要你在审核节点看图把关，它会在镜头日志那一步请你补充。
- **画面里有二维码、电话、别家商标**：skill 会提醒你换镜或裁掉，交付前也会自动扫二维码。

## 授权

- 代码和文档：MIT，见 `LICENSE`。
- 字体：SIL Open Font License 1.1，见 `template/public/fonts/`。
- LUT：不随仓库分发，安装时从 Sony、大疆官方地址下载。
