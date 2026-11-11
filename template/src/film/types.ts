// timeline.json 的类型（格式见 skill 的 references/edit-schema.md 第 5 节）。
// 模板只读这个文件；字段缺省时按 schema 的默认值补（见 normalize.ts）。

export type TrType = "cut" | "dip" | "fade" | "whip" | "punch" | "sweep" | "wipe";
export type Tr = {
  type: TrType;
  len?: number;
  /** 切点前 / 后各占几帧（timeline.py 写；缺省 pre = len//2、post = len − pre，punch 的 post = len） */
  pre?: number;
  post?: number;
  /** whip：画面往哪边甩出，默认 left */
  dir?: "left" | "right";
  /** sweep：光带角度（CSS 渐变角度，90 = 从左往右），默认 100 */
  angle?: number;
};

export type MoveType = "none" | "push" | "pull" | "drift-left" | "drift-right" | "rise" | "interview";
export type Move = {
  type: MoveType;
  amt?: number;
  /** interview：同一段连续采访（机位运行）的起止帧 */
  run_from?: number;
  run_to?: number;
  /** interview：这一镜是近景（true）还是全景（false） */
  tight?: boolean;
  /** interview：整体左移，把右侧让给信息卡（"left" 则右移） */
  make_room?: "right" | "left" | null;
};

/** 镜头内的光扫（发布片「光从车身上走过」）。模板扩展字段，schema 里没有；帧号是镜内帧（相对 cut）。 */
export type Light = { from: number; to: number; angle?: number; width?: number; stay?: boolean; lift?: number };

export type Shot = {
  id: string;
  layer?: number;
  cut: number;
  /** 名义出点（下一刀）；from / to 是含转场重叠的可见区间 */
  end?: number;
  from: number;
  to: number;
  /** 片段路径（相对 public/）；黑场镜头为 null */
  clip: string | null;
  clip_frames: number[];
  src?: string | null;
  speed?: number | [number, number][];
  native_fps?: number;
  src_in_s?: number;
  src_out_s?: number;
  frame?: { scale?: number; x?: number; y?: number };
  move?: Move | MoveType | null;
  move_amt?: number;
  make_room?: "right" | "left" | null;
  in_tr?: Tr | null;
  out_tr?: Tr | null;
  sky?: number;
  vignette?: number;
  /** 从整片第几帧开始定格（绝对帧号）；定格已经算进 clip_frames，模板只拿它决定落版成雾从哪一帧开始 */
  hold_from?: number | null;
  light?: Light | null;
  audio?: unknown;
  note?: string;
};

export type Text = {
  id?: string;
  type: string;
  from: number;
  to: number;
  z?: number;
  [k: string]: unknown;
};

export type Caption = { text: string; from: number; to: number };

export type Music = {
  src: string;
  from?: number;
  trim_frames?: number;
  gain?: number;
  gain_keys?: [number, number][];
  duck_ranges?: [number, number][];
  duck_db?: number;
  attack?: number;
  release?: number;
};
export type Sfx = { id?: string; src: string; from: number; gain?: number; tone?: number; dur?: number };
export type Voice = {
  shot?: string;
  layer?: number;
  clip_start_s?: number;
  clip: string;
  from: number;
  to: number;
  clip_start_frame?: number;
  gain?: number;
  fade_in?: number;
  fade_out?: number;
};

export type FontSpec = string | { src: string; weight?: string; style?: string };

export type Timeline = {
  fps?: number;
  width?: number;
  height?: number;
  duration: number;
  style?: string;
  title?: string;
  /** 拍网格（已换算到成片时间）和段落表：模板不用，审核台用来标位置 */
  grid?: { bpm?: number; beat_s?: number; offset_s?: number; beats_per_bar?: number; lead_frames?: number; beat_frames?: number } | null;
  sections?: { name: string; from: number; to: number }[];
  fonts?: Record<string, FontSpec>;
  brand?: { accent?: string; logo_white?: string; logo_dark?: string; [k: string]: unknown };
  shots: Shot[];
  texts?: Text[];
  captions?: Caption[] | { items?: Caption[]; size?: number; bottom?: number; max?: number };
  caption_style?: { size?: number; bottom?: number; max?: number } | null;
  audio?: { music?: Music | null; sfx?: Sfx[]; voice?: Voice[]; voice_gain?: number };
  clips?: unknown;
  lint?: unknown;
};
