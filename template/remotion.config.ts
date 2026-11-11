// 命令行渲染的默认值（Node API 不读这个文件，tools/stills.mjs 里另外传参）。
import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
// 模板里有 SVG 滤镜、蒙版和大量叠层：用 angle 走 GPU（命令行写了 --gl=angle 也一样）
Config.setChromiumOpenGlRenderer("angle");
