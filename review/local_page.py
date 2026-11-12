#!/usr/bin/env python3
"""把 page.json 填进模板，生成本地审核台的 index.html（配合 serve_local.py 用）。

用法：python3 review/local_page.py <page.json> <审核台目录>
  page.json 里 "kind": "media" 时用 player.html（媒体审核页），否则用 template.html（分镜 / 风格帧）。

分镜 / 风格帧 page.json：
  {"slug": "demo-hype-board", "title": "燃剪示例 · 分镜", "brand": "分镜 v1 · 46.3 秒 · 32 镜", "version": "board-v1",
   "end": 46.3, "sub": "分镜 v1 · 2026-11-11", "notes": ["<b>…</b>…", …],
   "shots": [{"id", "title", "t": [起秒, 止秒], "line": 口播 / 画面字, "concept": 画面说明, "sound": 声音, "prev": 上一版意见(可选),
              "boards": [[图名, "@镜内帧", 说明, 整片帧, "beat"|"end"|"trans"], …], "rep": 代表图名(可选)}],
   "music": [[起秒, 止秒, "段名"], …], "hits": [[秒, "冲击点"], …],
   "coverage": {"used", "total", "unused_n", "summary", "top_reused": [[名, 次]], "unused": {目录: [名]}}（可选，顶栏显示素材用量）}
  审核台目录里要有 frames/<图名>.jpg（1600×900）和 thumbs/<图名>.jpg（384×216）。转场图的图名以 T- 开头。
  版式和交互沿用 Remotion 工作区审核台（一屏一镜大画面、右侧窄栏、胶片条、←/→ ↑/↓ 1/2/3 键盘审片）；意见存在 serve_local.py 的 feedback.json。

媒体审核页 page.json（build_review.py --kind media 生成）：
  {"kind": "media", "slug", "title", "brand", "version", "sub", "notes": [...],
   "items": [{"id", "name", "file", "kind": "audio"|"video", "src": "media/<文件>", "dur": 秒, "size": 字节, "desc": 说明,
              "chapters": [[起秒, 止秒, "段名"], …], "marks": [[秒, "说明"], …], "peaks": [0–100 的波形峰值…]}]}
  审核台目录里要有 media/<文件>（可以是软链 / 硬链）。
"""
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MARK = "const PAGE = /*PAGE*/"


def _fill(tpl_name: str, page: dict, cfg: dict, n: int) -> str:
    t = open(os.path.join(HERE, tpl_name), encoding="utf-8").read()
    data = json.dumps(page, ensure_ascii=False).replace("</", "<\\/")
    assert t.count(MARK) == 1, f"{tpl_name} 里找不到唯一的 PAGE 数据行，模板被改过了？"
    a = t.index(MARK) + len(MARK)
    b = t.index("/*END*/;", a)
    t = t[:a] + data + t[b:]
    t = t.replace("__TITLE__", html.escape(cfg.get("title", "审核台")))
    t = t.replace("__BRAND__", html.escape(cfg.get("brand", "")))
    t = t.replace("__N__", str(n))
    t = t.replace("__SUB__", html.escape(cfg.get("sub", "")))
    # notes 允许少量 HTML（<b>），由生成脚本写，不来自用户输入
    t = t.replace("__NOTES__", "\n".join(f"      <li>{x}</li>" for x in cfg.get("notes", [])) or "      <li>（没有改动说明）</li>")
    return t


def build(cfg: dict) -> str:
    if cfg.get("kind") == "media":
        return build_player(cfg)
    shots = cfg["shots"]
    page = {
        "slug": cfg.get("slug", "ftv"),
        "version": str(cfg.get("version", "v1")),
        "end": cfg.get("end", 0),
        "shots": [
            {k: s[k] for k in ("id", "title", "t", "line", "concept", "sound", "prev", "note") if k in s}
            | {"beats": [[b[1], b[2]] for b in s["boards"] if b[4] != "trans"]}
            for s in shots
        ],
        "music": cfg.get("music", []),
        "hits": cfg.get("hits", []),
        "boards": {s["id"]: s["boards"] for s in shots},
    }
    if cfg.get("coverage"):
        page["coverage"] = cfg["coverage"]
    page["boards"]["__rep"] = {s["id"]: s.get("rep") or next((b[0] for b in reversed(s["boards"]) if b[4] != "trans"), s["boards"][0][0]) for s in shots if s["boards"]}
    return _fill("template.html", page, cfg, len(shots))


def build_player(cfg: dict) -> str:
    items = cfg.get("items") or []
    page = {"slug": cfg.get("slug", "ftv-media"), "version": str(cfg.get("version", "v1")), "items": items}
    return _fill("player.html", page, cfg, len(items))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    out_dir = sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    if cfg.get("kind") == "media":
        missing = [it["src"] for it in cfg.get("items", []) if not os.path.exists(os.path.join(out_dir, it["src"]))]
        if missing:
            print(f"⚠ 审核台目录里缺 {len(missing)} 个媒体文件：{missing[:6]}")
        open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8").write(build_player(cfg))
        print(f"✓ {os.path.join(out_dir, 'index.html')}：{len(cfg.get('items', []))} 条音视频")
        return
    missing = [b[0] for s in cfg["shots"] for b in s["boards"] if not os.path.exists(os.path.join(out_dir, "frames", b[0] + ".jpg"))]
    if missing:
        print(f"⚠ frames/ 里缺 {len(missing)} 张图：{missing[:6]}…")
    open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8").write(build(cfg))
    print(f"✓ {os.path.join(out_dir, 'index.html')}：{len(cfg['shots'])} 镜，{sum(len(s['boards']) for s in cfg['shots'])} 张图")


if __name__ == "__main__":
    main()
