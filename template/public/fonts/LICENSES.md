# 字体来源与授权

模板只自带 **SIL Open Font License 1.1（OFL）** 授权的字体。OFL 允许免费商用，也允许随项目一起再分发，条件是附上对应的 OFL 文本，并且不能单独售卖字体文件本身。

`timeline.json` 的 `fonts` 按「角色」引用字体，路径相对 `public/`；没写的角色用下表的默认值。

| 角色 | 文件 | 字体 | 授权 | 来源 |
|---|---|---|---|---|
| `brush` / `kai` | `MaShanZheng-Regular.ttf` | 马善政毛笔楷书 Ma Shan Zheng | OFL 1.1，见 `OFL-MaShanZheng.txt` | Google Fonts：github.com/googlefonts/mashanzheng |
| `sans` | `NotoSansSC-VF.ttf` | 思源黑体 Noto Sans SC（可变字重 100–900） | OFL 1.1，见 `OFL-NotoSansSC.txt` | Google Fonts：github.com/google/fonts/tree/main/ofl/notosanssc（原名 `NotoSansSC[wght].ttf`） |
| `title` | `NotoSansSC-VF.ttf`（锁定 900 字重） | 思源黑体 Black | 同上 | 同上 |
| `num` | `D-DINCondensed-Bold.ttf` | D-DIN Condensed Bold（Datto） | OFL 1.1，见 `OFL-D-DIN.txt` | www.datto.com/fonts/d-din |

## 换字体

1. 把字体文件放进 `public/fonts/`。只放你确认过可以商用的字体，并把授权文本一起放进来。
2. 在 `project.json` 的 `fonts` 里给角色指定新文件：
   - 单字重文件：`"brush": "fonts/新字体.ttf"`；
   - 可变字重文件锁定某个字重：`"title": {"src": "fonts/xxx-VF.ttf", "weight": "900"}`。
3. 文件名含 `VF`、`[wght]` 或 `Variable` 的可变字体，没指定字重时会自动按 100–900 注册。

## 示例片用过、但没有随模板分发的字体

| 字体 | 为什么不随模板分发 | 想用的话 |
|---|---|---|
| 演示夏行楷 | 授权来源不清楚（网上的「免费商用」标注来自第三方字体合集，不是作者声明） | 自行确认作者授权后下载，按上面的步骤指定给 `brush` |
| 阿里巴巴普惠体 Heavy | 官方允许免费商用，但不允许再分发字体文件 | 从官方字体站 www.alibabafonts.com 下载，指定给 `title` |

口号示例片的定稿用的是演示夏行楷，访谈示例片的倒计时用的是马善政。两款都是「清楚好认、不飞白」的毛笔字，符合用户定下的口味。
