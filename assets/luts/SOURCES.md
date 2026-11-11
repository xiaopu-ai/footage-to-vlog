# 技术还原 LUT 来源

> 🔴 厂商 LUT 的再分发授权不明确，开源仓库里**不带 .cube 文件**。安装后跑一次 `scripts/fetch_luts.sh`，从下表的官方地址下载并校验，放进本目录。

这里只放「Log → Rec.709」的官方技术还原 LUT，不带风格。风格调色另用 HALD 色表（`scripts/look.py`）。
`project.json` 的 `luts` 没写某条曲线时，`make_clips.py` / `grab.py` / `inventory.py` 默认用这里的文件。

| 文件 | 机型曲线（profile） | 适用素材 | 来源 |
|---|---|---|---|
| `Slog3-S-Gamut3.Cine_To_s709_V200.cube` | `sony-slog3` | Sony S-Log3 / S-Gamut3.Cine（FX3、A7S III、A7 IV 等；示例项目实测 8-bit 全范围 H.264 和 10-bit 4:2:2 都对） | Sony 官网 sony-cinematography.com 的 LUT 下载页（Resources → LUTs，s709 V200，33³） |
| `DJI_Mavic_4_Pro_D-Log_M_to_Rec.709_V1.cube` | `dji-dlogm` | 大疆 Mavic 4 Pro 的 D-Log M（4K HEVC 10-bit，tv 范围）。别的大疆机型（Air / Mini / Osmo）的 D-Log M 要换成各自官方的 LUT，在 `project.json` 的 `luts` 里指定 | 大疆官网 dji.com 对应机型的「下载 → 软件 / LUT」页 |

## 注意

- **大疆官网会按 IP 把海外访问判到美区**，美区页面找不到这个 LUT 的下载项。要带 cookie `region=CN` 访问国区页面才能拿到国区直链（浏览器里把 cookie 改成 `region=CN` 再刷新，或下载命令带 `--cookie "region=CN"`）。
- **D-Log 和 D-Log M 是两条不同的曲线**，元数据里分不出来。示例项目的航拍实测是 D-Log M：同一帧套 D-Log LUT 水花高光削平、饱和过头；套 D-Log M LUT 是正常的阴天画面。原始码值中位约 0.48–0.53（全码值归一），按 D-Log 解读相当于整片过曝约 1.7 档。`inventory.py` 默认判成 D-Log M，判错了在 `project.json` 的 `profiles` 里改。
- Sony 文件内嵌的 NonRealTimeMeta XML 会写明 `CaptureGammaEquation`（如 `s-log3-cine`）和 `CaptureColorPrimaries`（如 `s-gamut3-cine`）。色域不是 S-Gamut3.Cine 时（比如 S-Gamut3），要换 Sony 对应的 LUT。
- 不需要 LUT 的：普通 Rec.709 素材（比如 Osmo Action 普通色彩模式、手机），`profile` 是 `rec709`。
