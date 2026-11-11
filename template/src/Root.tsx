// 只注册两个合成，都读 src/timeline.json（timeline.py 的编译产物，不要手改）：
//   Film   整片：时长、尺寸、帧率都取自 timeline.json
//   Frame  1 帧 30 fps：--props='{"frame":120}' 显示整片第 120 帧，给风格帧和分镜审核台用
// 两个合成都接受 props.timeline：传一份别的 timeline（tools/stills.mjs 的第一个参数）就渲那一份，不用改文件。
import React from "react";
import { Composition } from "remotion";
import { makeFilm, makeFrame, type FilmProps, type FrameProps } from "./film/Film";
import type { Timeline } from "./film/types";
import raw from "./timeline.json";

const TL = raw as unknown as Timeline;
const Film = makeFilm(TL);
const Frame = makeFrame(TL);

const meta = (t: Timeline) => ({
  fps: t.fps ?? 30,
  width: t.width ?? 1920,
  height: t.height ?? 1080,
});

export const RemotionRoot: React.FC = () => (
  <>
    <Composition
      id="Film"
      component={Film}
      durationInFrames={Math.max(1, TL.duration)}
      {...meta(TL)}
      defaultProps={{} as FilmProps}
      calculateMetadata={({ props }) => {
        const t = (props as FilmProps).timeline ?? TL;
        return { durationInFrames: Math.max(1, Math.round(t.duration)), ...meta(t) };
      }}
    />
    <Composition
      id="Frame"
      component={Frame}
      durationInFrames={1}
      fps={30}
      width={TL.width ?? 1920}
      height={TL.height ?? 1080}
      defaultProps={{ frame: 0 } as FrameProps}
      calculateMetadata={({ props }) => {
        const t = (props as FrameProps).timeline ?? TL;
        return { durationInFrames: 1, fps: 30, width: t.width ?? 1920, height: t.height ?? 1080 };
      }}
    />
  </>
);
