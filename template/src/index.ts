// 入口：npx remotion render src/index.ts Film out/film.mp4
import { registerRoot } from "remotion";
import { RemotionRoot } from "./Root";

registerRoot(RemotionRoot);
