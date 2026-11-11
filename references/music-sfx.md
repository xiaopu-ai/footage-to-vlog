# 配乐、节拍、音效、混音

## 1. 作曲计划 → 生成

- 从 `assets/music-plans/<style>.plan.json` 改起。格式是 ElevenLabs Music 的 `composition_plan`：全局正负风格词 + 若干段，每段有正负风格词和 `duration_ms`。
- 写法：
  - 全局写死 BPM、调性不变、无人声。
  - 每段按整小节写时长：BPM 128 时 3750 ms = 2 小节；BPM 120 时 2000 ms = 1 小节。**每段至少 3000 ms**，否则接口报 422。
  - 要冲击就写在段首：「huge impact exactly on the first beat」。
  - 负面词兜住用户讨厌的：`slow intro`、`ambient intro`、`fade in`、`fade out ending`、`war`、`military drums`、`taiko`、`epic trailer`、`tempo change`、`half-time`。
- 生成：`python3 scripts/music_gen.py plan.json work/music/a.mp3`。并发上限 2，遇到 429 等一下重试。
- **一次出 2–3 首给用户选**，同时附上每首的结构说明（用 `bands.py` 看：鼓和贝斯什么时候进出、哪里蓄力、哪里砸）。

### 用户的配乐口味（示例项目定下的）

- 🚫 前奏慢、前面很平、战争片 / 史诗预告片式。口号片 A / B 两首都因为这个被否。
- 🚫 卡点片开头没有冲击。发布片 v1 第一下重击在 7.2 s，被嫌「开头特别平」。
- ✅ 明亮的旋律 house（口号片）、冷开场第 0 秒重击 + 鼓不停的混合预告片风格（发布片）、暗黑失真贝斯（燃剪）。
- 判定：卡点类开头 3 秒内没有重击、前 10 秒没有鼓的，不用。

## 2. 测拍

```bash
python3 scripts/tempo.py work/music/a.mp3 --out beats.json        # 可加 --from/--to 只拟合某一段
```

- 🔴 别信单一的测拍结果：常见工具把 124 测成 83 / 99 / 164，把 128 测成 96。`tempo.py` 先用多频段起音自相关粗测，再在军鼓 / 拍手频段（1.5–4 kHz）和底鼓频段上做最小二乘细拟合，残差约 40 ms。
- 生成的曲子常常不按计划走：段落时长不守、中途变速、最后直接淡出。拿到后先测、先看结构，再决定怎么剪。
- `beats.json` 是 `timeline.py` 解析 `{"beat"}` / `{"bar"}` / `{"eighth"}` 的依据。剪辑后的配乐要**重新测一次**。

## 3. 按小节重剪

```bash
python3 scripts/bar_edit.py src.mp3 public/audio/music.wav edl.json
```

- EDL 是若干源区间，按顺序拼接。区间端点都取在拍点上，每段 10 ms 淡入淡出防爆音。单段可以加 `fade_in`、`fade_out`、`lowpass`、`gain_db`。示例在 `assets/music-plans/*.bar_edit.json`。
- 常用剪法：
  - **断音 / 停拍**：拿源曲里的近静音小节或余音小节，小节网格不断。
  - **复用最重的小节**：当第二个 drop。
  - **闷声开场**：开头一段加 600 Hz 低通。
  - **收尾对齐**：让源曲的最后一下重击落在成片落版那一帧。
  - 只在乐句边界拼接，结尾要自然落在片尾，不硬切、不淡出了事。
- 第一下重击要落在第 0 帧时，用 `audio.music.trim_s` 剪掉前面的空白。

## 4. 音效

```bash
python3 scripts/sfx_gen.py jobs.json work/sfx/raw/
python3 scripts/sfx_prep.py work/sfx/raw/*.mp3 --out public/audio/sfx/
```

`sfx_gen.py` 每条出 2 个版本；`sfx_prep.py` 切掉头部静音，峰值统一到 −3 dBFS。

- **提示词**写「材质 + 动作 + 结果」，结尾加 `Dry, close-miked.`。很轻的动作常常出成几乎无声的闷响，生成完要听或看声谱再挑。
- **数量**：

| 风格 | 数量 | 放在哪 |
|---|---|---|
| 访谈 | 10–15 个 | 倒计时、人物卡、问题卡、纯音乐段入口、Logo |
| 燃剪 | 主版约 19 个（15 秒版约 9 个），分层 | 重击挂段落点和黑场；whoosh 挂甩镜和掠过；riser 放在 drop 前 2–4 拍；水墙冲击挂甩尾峰值；金属扣和吸气挂暗场的刀 |
| 发布片 / 口号片 | 0–5 个 | 音乐撑满 |

- 🚫 不用 tick / snap / pop 这类短促尖锐、和画面无关的声音。访谈示例片配过约 70 个，用户说「画蛇添足」。
- 现场原声（风、引擎、水、人喊）比生成的音效更值钱：引擎声比画面早约 20 帧进（J 切）；停拍段只留真实风声 + 一声吸气。

## 5. 混音

| 层 | 典型值 |
|---|---|
| 配乐 | 0.8–0.9。访谈用 `duck.under: "voice"` 压 13 dB；原声段手写 `keys`（口号片：压到 0.36，6 帧压下、8 帧恢复） |
| 人声 / 原声 | 1.0。原声段比前后低 3–4 dB 时乘 1.8 拉平 |
| 音效 | 0.22–0.85，重击最响 |

**交付响度**：−14 LUFS、真峰值 −1.5 dBTP。直接 loudnorm 常常真峰值超标，要先限幅：

```bash
ffmpeg -i out/raw.mp4 -c:v copy -af "alimiter=limit=0.74" out/lim.mp4
python3 scripts/loudnorm.py out/lim.mp4 out/final.mp4 --I -14 --TP -1.5
```

## 6. 卡点表（交付物）

`python3 scripts/cuesheet.py <proj> --out out/<片名>-卡点表.md`

内容：配乐段落、每个镜头的时间 / 素材 / 转场 / 是否在拍上、每个文字、每个音效。客户换音乐、换音效时按它对。
