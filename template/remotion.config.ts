// 命令行渲染的默认值（Node API 不读这个文件，tools/stills.mjs 里另外传参）。
// 交付用 skill 的 scripts/render.py：它静音渲画面、另外混音封装，命令行参数和这里一致。
import { Config } from "@remotion/cli/config";

// PNG 帧 + yuv420p + BT.709：JPEG 帧会让 ffmpeg 按全范围 yuvj420p、BT.601 矩阵编码，播放器上偏色。
// PNG 是 RGB，Remotion 用 zscale 按 BT.709 矩阵、tv 范围转成 yuv420p。
// 注意：Remotion 4.0.469 的输出里 primaries / transfer 标签仍是 unknown，render.py 封装时用 h264_metadata 补齐。
Config.setVideoImageFormat("png");
Config.setPixelFormat("yuv420p");
Config.setColorSpace("bt709");
Config.setOverwriteOutput(true);
// 模板里有 SVG 滤镜、蒙版和大量叠层：用 angle 走 GPU（命令行写了 --gl=angle 也一样）
Config.setChromiumOpenGlRenderer("angle");
