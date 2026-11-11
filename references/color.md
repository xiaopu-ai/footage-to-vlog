# 调色：技术还原 → 逐镜校正 → 创意色表

全部在 ffmpeg 里做，成片片段、风格帧、分镜底图走**同一条色彩链**，所以审过的颜色就是成片的颜色。Remotion 里只叠光效、暗角、天空压暗这类渐变，不在浏览器里调色。

## 1. 认机型和曲线（`inventory.py` 自动判断，认错了在 project.json 的 `profiles` 里改）

| profile | 典型机型 | 范围 | 还原 |
|---|---|---|---|
| `sony-slog3` | Sony FX3 / A7S3 等，S-Log3 / S-Gamut3.Cine，8-bit H.264 | **全范围**（yuvj420p） | `scale=…:in_range=pc:out_range=pc,format=rgb48le,lut3d=Slog3-S-Gamut3.Cine_To_s709_V200.cube:interp=tetrahedral` |
| `dji-dlogm` | 大疆 Mavic 4 Pro 等，D-Log M，10-bit HEVC | **tv 范围** | `scale=…:in_range=tv:out_range=pc,format=rgb48le,lut3d=DJI_Mavic_4_Pro_D-Log_M_to_Rec.709_V1.cube:interp=tetrahedral` |
| `rec709` | 运动相机、手机、普通模式 | 看素材 | 不套 LUT |

- 🔴 **大疆航拍是 D-Log M，不是 D-Log**：中位码值约 0.48–0.53。ChatCut 把它标成 D-Log，套错 LUT 高光会被压。
- LUT 放在 `assets/luts/`，来源和下载方法见那里的 `SOURCES.md`。大疆官网会把海外 IP 判到美区，要带 cookie `region=CN` 才能拿到国区直链。
- 新机型先拿 2 秒素材试：灰片看中位码值，套 LUT 后看肤色和天空。

## 2. 逐镜校正（`fix`）

- `measure.py` 量每镜调色后的亮度 p5 / p50 / p95、中间调（亮度 60–190）的平均 R−B，并给出建议值。
- **同一段的镜头**：p5 / p95 差不超过 3 个百分点，R−B 尽量拉到同一区间（水面镜头 −12 ~ −20）。
- `fix.warm = k`：k > 0 往冷拉，阴影 / 高光按 0.6 倍跟随：`colorbalance=rs=-0.6k:bs=0.6k:rm=-k:bm=k:rh=-0.6k:bh=0.6k`。
- `fix.exposure`：以档为单位；`"levels": "auto"` 按 p5 / p95 拉到全片目标。

## 3. 创意色表（HALD）

```bash
python3 scripts/look.py build A2 "<ffmpeg 滤镜链>" --out work/looks/
python3 scripts/look.py preview work/looks/*.png --frames work/frames/*.jpg
```

- 原理：`ffmpeg -f lavfi -i haldclutsrc=level=8 -vf "<滤镜链>"` 把整条调色链烘成一张 png。套用 `[0][1]haldclut`，抽帧和片段颜色一致。
- 起点（都被用户审过）：

| 名 | 用在 | 链 / 要点 |
|---|---|---|
| A 温暖人文 | 访谈、生活方式 | `eq=contrast=1.06:saturation=1.12,colorbalance=rs=0.02:bs=-0.02:rm=0.05:gm=0.01:bm=-0.05:rh=0.01:bh=-0.02,curves=master='0/0.01 0.22/0.19 0.5/0.50 0.8/0.82 1/0.93'` |
| B 冷峻诗意 | 口号片 | `curves=master='0/0.05 0.25/0.21 0.55/0.49 1/0.82',colorbalance=rs=-0.06:bs=0.06:gs=0.01:rm=-0.02:bm=0.02:rh=-0.02:bh=0.02,eq=saturation=0.86` |
| A2 冷峻不压暗 | 燃剪 | 黑位 0.05、gamma 1.0;要配合 `fix.levels: "auto"` 用。暗角(0.25)和天空压暗由模板画,冷感靠天空压暗 + 暗部偏青 |
| C 黑金 | 发布片 | 暗部干净、高光暖金，品牌色单独保留 |

- 风格帧阶段至少给 2 套，最好 3–4 套。

## 用户定下的口味

- 「冷峻」不等于「暗」：黑位不低于约 0.05，人脸和暗部不死压。燃剪原话：「A 的色调可以，但暗影压太低了」。
- 冷感靠天空压暗（模板的 `sky`）+ 暗部偏青。正俯拍别加天空压暗。
- 阴天素材不硬调暖色。
- 开头几镜也别太暗：发布片 v2 提亮约 25% 后，光带照样明显。
- 2.35:1 遮幅只是选项，用户没选过。

## 高帧率

片段一律「保留全部原生帧、按 30 fps 播」（`setpts=N/(30*TB)`）：120p 播出来是 0.25×，60p 是 0.5×。成片里的速度由 `timeline.json` 的 `clip_frames` 决定，不用 playbackRate，因为 `< 0.5` 时整片渲染会出黑帧。

## 解码细节(make_clips 已处理,了解即可)

- Sony 8-bit 解码显式按 BT.709 矩阵:ffmpeg 默认按 BT.601,平均差不到 1 级,饱和色最多差 18 级。
- 输出写全色彩标签(setparams),否则播放器把 Sony 片段当成 unknown。
- 8-bit tv 范围的素材加 accurate_rnd,不加会偏暗约 2 级。
- 片段按帧切:`-ss from_s` + `trim=end_frame=frames`。不用 `-frames:v`,它会把声音一起截短。
- 片段和直接抽帧的颜色一致(实测各通道均值差 ≤ 0.23 级)。
