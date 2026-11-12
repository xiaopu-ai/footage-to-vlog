# 审核节点和本地审核台

> 本文件是审核节点的**唯一定义**。SKILL.md 和其他文档都引用这张表，不另写一套。

跳过审核台的那条示例片，看完整片后改了 4 轮。先过审核台的几条，整片返工都在 0–1 次。唯一那次返工是节奏问题，静帧上看不出来，所以加了节奏预演。

## 节点表

| # | 节点（`state.py` 名） | 给用户看什么 | 用户拍板什么 | 审核页 |
|---|---|---|---|---|
| 1 | 方案 `plan` | `work/PLAN.md`（模板 `assets/templates/PLAN.md`） | 风格、时长、画幅、品牌层级、段落结构 | 直接给文件；看不了就用 media 页挂成网页 |
| 2 | 配乐 `music` | 2–3 首候选 + 每首的结构说明；选定后给按小节重剪的版本 | 选哪首、剪得对不对；有费用的生成先报价 | **media 页**（音频播放器） |
| 3 | 风格帧 `look` | 2–4 套调色 × 关键画面 × 字体和版式，定稿质量静帧 | 调色、字体、文字位置、落版 Logo | sf 页（静帧） |
| 4 | 分镜 `board` | 每镜按节拍的关键帧 + 转场拼图 + 素材覆盖统计 | 逐镜通过 / 要改 | board 页（静帧） |
| 5 | 节奏预演 `rhythm` | 开场 10–15 秒半分辨率小样（`render.py --preview 15`） | 节奏快慢、冲击、卡点体感 | **media 页**（视频播放器） |
| 6 | 成片 `film` | 成片 + 无声版 / 纯人声版 + 卡点表 + QA 汇总 | 定版或具体修改意见 | **media 页**（视频播放器） |

- 每个节点都**停下来等用户回复**，用户原话用 `state.py <proj> approve <节点> --quote "原话"` 记下来。
- 有费用的操作（生成配乐、音效）在执行前报价，用户同意后再做；同一会话里已经同意过同类费用的，沿用。
- 节点依赖：plan → music / look → board → rhythm → film。
  - **输入**变了，本节点和下游一起失效，要重新审：`music.wav`、`beats.json`、`edit.json`、`timeline.json`、look 色表文件，以及 `project.json` 的 `look` / `levels_target` 两个字段。
  - **产物**变了，只影响本节点：预演视频、成片。比如只补成片的色彩标签，前面的节点不受影响。
  - 用 `state.py <proj> status` 看。
- PLAN.md 批准后不要再往里追加内容，否则全部下游失效。新想法写进 `work/FEEDBACK.md` 或下一版 PLAN。
- 生成脚本要覆盖文件前，先跑 `state.py <proj> guard <文件>`。返回 3 表示这是已批准版本的输入，写到新文件，不要覆盖。
- 阶段开始、结束、重跑、失败记成事件：`state.py <proj> event <阶段>.start|.end|.rerun|.fail`，复盘时用来统计耗时。

## 审核介质：一律用本地网页

不同 agent 的界面能力差别很大：有的能在对话里显示图片、播放视频，有的连图片都显示不了。**不要在对话里直接贴 mp3 / mp4 / 图片当审核材料。** 从第 1 个节点开始，统一用本地网页：

```bash
python3 $SK/review/build_review.py <proj> --kind sf                     # 风格帧
python3 $SK/review/build_review.py <proj> --kind board                  # 分镜
python3 $SK/review/build_review.py <proj> --kind media --name music  --audio work/music/a.mp3 work/music/b.mp3   # 配乐试听
python3 $SK/review/build_review.py <proj> --kind media --name rhythm --video out/preview.mp4                    # 节奏预演
python3 $SK/review/build_review.py <proj> --kind media --name film   --video out/<片名>-v1.mp4                 # 成片
python3 $SK/review/serve_local.py <proj>/work/review/<sf|board|music|rhythm|film> --port 8765
```

- 把 `http://127.0.0.1:8765/` 单独一行发给用户。端口被占就换一个。
- 服务器支持 HTTP Range（断点续传），Safari 也能播放音视频。不要用 `python3 -m http.server` 代替：它不支持 Range，Safari 播不了。
- 每个页面加 `--serve` 可以生成完直接起服务。
- 意见存在 `<目录>/feedback.json`，结构是 `{条目: {status, note, played}}`，整体意见在 `_all` 下。
  - `played` 是用户实际播放过的比例（0–1）。音视频节点据此判断用户是不是真的听过、看过，没播过的不要当成已审。
  - 用户也常直接在聊天里说意见，两边都要看。
- 改版后重新生成页面，服务不用重启，用户刷新就行，已有意见保留。

**页面规则**：

| 页面 | 规则 |
|---|---|
| 风格帧 / 分镜（sf / board） | 只放静帧，不放视频（放视频会抬高前期成本）。一屏一镜、画面大、字放右侧窄栏；胶片条按时长比例排；键盘审片：←/→ 换镜，↑/↓ 换分镜图，1/2/3 = 通过 / 要改 / 待定 |
| 节奏预演 / 成片（media） | **必须能播放**：半分辨率预演就够，450 帧约十几秒就渲完。页面有段落表和关键时间点跳转 |

分镜图怎么取：
- 每镜取开场、每个文字或动作节拍落定后、落幅；
- 硬切做成「左：上一镜最后一帧 | 右：本镜第一帧」拼图；
- 特殊转场做成 2×2 四格过程。

## 风格帧

- 选 4–6 个最能代表这条片子的画面：开场、高潮、文字最多的一镜、落版。
- 每套调色出同一组画面，并排对比。
- 字体在这一步定。毛笔字至少给 3–6 款干净好认的候选。
- 落版 Logo 在这一步就出现在画面里，按 PLAN 的品牌层级核对：传播主体是谁就落谁的标。
- 渲染用模板的 `Frame` 合成（1 帧、30 fps），不要用 `<Still>`。

## 分镜

审核前自己先过一遍：

- `timeline.py` lint 零错误，警告逐条看过；
- `coverage.py`：用了多少条素材、复用最多的是哪条、还有多少条没用。页面顶部会显示。素材用得少、复用多，先回镜头日志里找新镜头，不要只在已有候选里互换；
- 每镜首、中、尾抽帧核对：商标、号码、第三方品牌、文不对题。

## 节奏预演（静帧看不出节奏，必须动起来看）

```bash
python3 $SK/scripts/render.py <proj> --preview 15 --allow-draft
python3 $SK/scripts/qa.py <proj> out/preview.mp4 --kind preview
```

交给用户之前，自己先按这张清单看一遍（可以 0.25 倍速逐帧看快剪段）：

- [ ] 第一刀和 drop 第一刀，是不是落在最重的那一下？
- [ ] 有没有连续 3 个以上纯硬切、无变速、无运镜的镜头？有的话，就是「素材在不停切换」的感觉。
- [ ] 有没有哪个动作（甩尾、水墙、掠过）被切成了两半？动作要留满。
- [ ] 切点是不是落在听得出来的重音上（底鼓、军鼓、乐句头），而不是 hi-hat 上？`qa.py` 会报重音命中率。
- [ ] 变速坡的坡心是不是落在拍上？
- [ ] 音画是否对齐（`qa.py` 的 audio_sync 项）。

用户只判断「快慢、冲击、卡点感觉」，不判断画质。
