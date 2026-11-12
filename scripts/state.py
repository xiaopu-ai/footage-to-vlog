#!/usr/bin/env python3
"""state.py · 审核节点的状态和审批记录:谁在什么时候批了哪个版本(文件 sha256 + 用户原话),输入一变就失效,下游跟着失效。

用法:
  python3 scripts/state.py <proj> approve <节点> --quote "用户原话" [--note "备注"] [--file 额外锁定的文件 …]
  python3 scripts/state.py <proj> status [--json]          # 每个节点:✓ 通过 / · 待审 / ✗ 已失效(为什么)
  python3 scripts/state.py <proj> check <节点>             # 通过且仍有效 → 退出码 0,否则 1(渲染前做闸门)
  python3 scripts/state.py <proj> guard <文件> …           # 文件属于已通过节点 → 退出码 3 并说明会让哪些节点失效;否则 0
  python3 scripts/state.py <proj> event <名称> [--node 节点] [--detail "…"]   # 记一条事件到 work/events.jsonl
  python3 scripts/state.py <proj> revoke <节点> [--quote "用户原话"]          # 撤销批准(用户改主意了)

节点、锁定的文件(相对 <proj>)和上下游:
  plan    结构方案    work/PLAN.md
  music   配乐        public/audio/music.wav、beats.json                      上游 plan
  look    调色        project.json 的 look / levels_target 字段 + look 色表文件   上游 plan
  board   分镜        edit.json、src/timeline.json                            上游 music、look
  rhythm  节奏预演    edit.json;预演视频(--file,默认 out/preview.mp4)          上游 board
  film    成片        成片文件(--file;out/ 下只有一个非 preview 的 mp4 时自动用它)  上游 rhythm
  「输入」会传给下游:music.wav 变了,music 自己和 board / rhythm / film 都失效。
  「产物」(预演视频、成片、--file 加的)只锁本节点:只改成片封装(比如色彩标签、重新封装音轨),只有 film 失效,music / board 不受影响。
  approve 时记下本节点输入 + 产物 + 所有上游节点输入的 sha256。status 时任何一个变了就是「已失效」,并指出是哪个文件、属于哪个节点。
  缺失的输入也记(比如还没有 beats.json):之后出现了也算变化。
  上游没通过时也能批(会提示),记录照样生效。

约定:
  - 用户的原话一字不改写进 --quote;只是「按你说的办」这种笼统确认,也照原话记。
  - 生成脚本覆盖文件前先 guard:退出码 3 = 这是已批准的版本,改了会让节点失效。写到新文件,或先跟用户确认。
  - 事件名用「阶段.动作」:music.start / music.end / clips.rerun / render.fail …;status 会把成对的 .start / .end 算成耗时,
    并统计 rerun / fail 次数,复盘时直接用。approve / revoke 自动记事件。
  - PLAN.md 定稿批准后不要再往里面追加流水账(会让全部下游失效),后续决定记进 work/FEEDBACK.md 或 event。

数据:work/state.json(节点记录 + 批准历史)、work/events.jsonl(一行一个事件)。
只依赖标准库,兼容 Python 3.9。
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

NODES = [
    # 名称, 中文, 上游, 输入(传给下游), 默认产物(只锁本节点)
    ("plan", "结构方案", [], ["work/PLAN.md"], []),
    ("music", "配乐", ["plan"], ["public/audio/music.wav", "beats.json"], []),
    ("look", "调色", ["plan"], ["@look"], []),
    ("board", "分镜", ["music", "look"], ["edit.json", "src/timeline.json"], []),
    ("rhythm", "节奏预演", ["board"], ["edit.json"], ["@preview"]),
    ("film", "成片", ["rhythm"], [], ["@film"]),
]
NODE = {n[0]: {"cn": n[1], "deps": n[2], "inputs": n[3], "artifacts": n[4]} for n in NODES}
LOOK_KEY = "project.json#look"
BIG = 64 << 20  # 大文件:大小和修改时间都没变时沿用记录里的 hash,不重算


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def die(msg, code=2):
    print("✗ " + msg, file=sys.stderr)
    sys.exit(code)


class Proj:
    def __init__(self, path):
        self.root = Path(path).resolve()
        if not self.root.is_dir():
            die("项目目录不存在:%s" % self.root)
        self.work = self.root / "work"
        self.state_f = self.work / "state.json"
        self.events_f = self.work / "events.jsonl"
        self._hash = {}
        self.state = self._load()

    # ── 存取 ──
    def _load(self):
        if self.state_f.exists():
            try:
                d = json.loads(self.state_f.read_text("utf-8"))
                d.setdefault("nodes", {})
                d.setdefault("history", [])
                return d
            except ValueError as e:
                die("work/state.json 坏了(%s):修好或挪走再跑" % e)
        return {"version": 1, "nodes": {}, "history": []}

    def save(self):
        self.work.mkdir(parents=True, exist_ok=True)
        tmp = self.state_f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, self.state_f)

    def event(self, name, node=None, detail=None, **extra):
        self.work.mkdir(parents=True, exist_ok=True)
        rec = {"t": now_iso(), "ts": round(time.time(), 3), "event": name}
        if node:
            rec["node"] = node
        if detail:
            rec["detail"] = detail
        rec.update(extra)
        with open(self.events_f, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    # ── 路径和 hash ──
    def rel(self, p):
        """统一成「相对 <proj> 的 posix 路径」;项目外的文件用绝对路径。"""
        ap = Path(p)
        ap = (ap if ap.is_absolute() else Path.cwd() / ap).resolve()
        try:
            return ap.relative_to(self.root).as_posix()
        except ValueError:
            return ap.as_posix()

    def abs(self, rel):
        return Path(rel) if os.path.isabs(rel) else self.root / rel

    def project_json(self):
        f = self.root / "project.json"
        try:
            return json.loads(f.read_text("utf-8")) if f.exists() else {}
        except ValueError:
            return {"__bad__": True}

    def fingerprint(self, key, old=None):
        """→ {"sha256": … | None(文件不存在), "size", "mtime_ns"}。key 可以是虚拟键 project.json#look。"""
        if key == LOOK_KEY:
            p = self.project_json()
            v = {"look": p.get("look"), "levels_target": p.get("levels_target")}
            return {"sha256": hashlib.sha256(json.dumps(v, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
                    "value": v}
        f = self.abs(key)
        if not f.is_file():
            return {"sha256": None}
        st = f.stat()
        if (old and old.get("sha256") and st.st_size > BIG and old.get("size") == st.st_size
                and old.get("mtime_ns") == st.st_mtime_ns):
            return {"sha256": old["sha256"], "size": st.st_size, "mtime_ns": st.st_mtime_ns}
        ck = (str(f), st.st_size, st.st_mtime_ns)
        if ck not in self._hash:
            h = hashlib.sha256()
            with open(f, "rb") as fh:
                for b in iter(lambda: fh.read(1 << 20), b""):
                    h.update(b)
            self._hash[ck] = h.hexdigest()
        return {"sha256": self._hash[ck], "size": st.st_size, "mtime_ns": st.st_mtime_ns}

    # ── 节点的文件 ──
    def expand_inputs(self, node):
        out = []
        for k in NODE[node]["inputs"]:
            if k == "@look":
                out.append(LOOK_KEY)
                look = self.project_json().get("look")
                if look:
                    out.append(self.rel(look if os.path.isabs(look) else self.root / look))
            else:
                out.append(k)
        return out

    def default_artifacts(self, node):
        arts = []
        for k in NODE[node]["artifacts"]:
            if k == "@preview":
                f = self.root / "out" / "preview.mp4"
                if f.is_file():
                    arts.append("out/preview.mp4")
            elif k == "@film":
                outd = self.root / "out"
                cands = sorted(p for p in outd.glob("*.mp4") if "preview" not in p.name.lower()) if outd.is_dir() else []
                if len(cands) == 1:
                    arts.append(self.rel(cands[0]))
                elif len(cands) > 1:
                    die("out/ 里有 %d 个 mp4(%s):用 --file 指定批准的是哪个成片" % (len(cands), "、".join(p.name for p in cands)))
        return arts

    def ancestors(self, node):
        seen, stack = [], list(NODE[node]["deps"])
        while stack:
            n = stack.pop(0)
            if n not in seen:
                seen.append(n)
                stack.extend(NODE[n]["deps"])
        return seen

    def descendants(self, node):
        return [n for n, _, _, _, _ in NODES if node in self.ancestors(n)]

    def owner_of(self, key):
        """输入键属于哪个节点(用于说明「上游 X 的 Y 变了」)。"""
        for n, _, _, _, _ in NODES:
            if key in self.expand_inputs(n):
                return n
        return None

    # ── 判定 ──
    def node_status(self, node):
        rec = self.state["nodes"].get(node)
        if not rec:
            return {"node": node, "status": "pending", "why": []}
        why = []
        for group, label in (("inputs", "输入"), ("artifacts", "产物")):
            for k, fp in rec.get(group, {}).items():
                cur = self.fingerprint(k, fp)
                if cur["sha256"] != fp.get("sha256"):
                    why.append({"file": k, "kind": label, "node": node,
                                "change": "不见了" if cur["sha256"] is None else ("新出现" if fp.get("sha256") is None else "变了")})
        own = set(rec.get("inputs", {})) | set(rec.get("artifacts", {}))
        for k, fp in rec.get("upstream", {}).items():
            if k in own:  # 同一个文件既是自己的输入又是上游的(比如 edit.json),只报一次
                continue
            cur = self.fingerprint(k, fp)
            if cur["sha256"] != fp.get("sha256"):
                why.append({"file": k, "kind": "上游", "node": fp.get("node") or self.owner_of(k) or "?",
                            "change": "不见了" if cur["sha256"] is None else ("新出现" if fp.get("sha256") is None else "变了")})
        return {"node": node, "status": "stale" if why else "approved", "why": why, "rec": rec}

    def lockset(self, node):
        rec = self.state["nodes"].get(node) or {}
        return set(rec.get("inputs", {})) | set(rec.get("artifacts", {})) | set(rec.get("upstream", {}))


def fmt_why(w):
    name = "project.json 的 look / levels_target" if w["file"] == LOOK_KEY else w["file"]
    if w["kind"] == "上游":
        return "上游 %s(%s)的 %s %s" % (w["node"], NODE.get(w["node"], {}).get("cn", ""), name, w["change"])
    return "%s %s %s" % (w["kind"], name, w["change"])


def cmd_approve(P, a):
    node = a.node
    if node not in NODE:
        die("没有节点 %s(可选:%s)" % (node, " / ".join(NODE)))
    if not a.quote or not a.quote.strip():
        die("--quote 必填:写用户的原话")
    inputs = P.expand_inputs(node)
    if node == "look" and len(inputs) < 2:
        die("project.json 里没有 look:先把选定的色表写进 project.json 再批 look")
    arts = [P.rel(f) for f in (a.file or [])] or P.default_artifacts(node)
    if NODE[node]["artifacts"] and not arts:
        die("%s 要指定批准的文件:--file <预演 / 成片>" % node)
    rec = {"approved_at": now_iso(), "quote": a.quote.strip(), "note": a.note or "", "inputs": {}, "artifacts": {}, "upstream": {}}
    for k in inputs:
        rec["inputs"][k] = P.fingerprint(k)
    for k in arts:
        fp = P.fingerprint(k)
        if fp["sha256"] is None:
            die("文件不存在:%s" % k)
        rec["artifacts"][k] = fp
    if not any(v["sha256"] for v in list(rec["inputs"].values()) + list(rec["artifacts"].values())):
        die("%s 要锁定的文件一个都不存在(%s):先产出再批" % (node, "、".join(inputs + arts)))
    missing = [k for k, v in rec["inputs"].items() if v["sha256"] is None]
    for anc in P.ancestors(node):
        for k in P.expand_inputs(anc):
            fp = P.fingerprint(k)
            fp["node"] = anc
            rec["upstream"][k] = fp
    warn = []
    for anc in P.ancestors(node):
        st = P.node_status(anc)
        if st["status"] != "approved":
            warn.append("%s(%s)%s" % (anc, NODE[anc]["cn"], "还没通过" if st["status"] == "pending" else "已失效"))
    prev = P.state["nodes"].get(node)
    P.state["nodes"][node] = rec
    P.state["history"].append(dict(rec, node=node, action="approve"))
    P.save()
    P.event("approve", node=node, detail=a.quote.strip(), files=sorted(list(rec["inputs"]) + list(rec["artifacts"])))
    print("✓ %s(%s)已批准 · %s · 用户原话「%s」" % (node, NODE[node]["cn"], rec["approved_at"], rec["quote"]))
    for k, v in list(rec["inputs"].items()) + list(rec["artifacts"].items()):
        print("  锁定 %-40s %s" % ("project.json 的 look / levels_target" if k == LOOK_KEY else k,
                                  (v["sha256"] or "(还不存在)")[:12]))
    if missing:
        print("  ⚠️ 还不存在:%s(之后出现也算改动,%s 会失效、要重批)" % ("、".join(missing), node))
    if warn:
        print("  ⚠️ 上游 %s:先批也可以,记录照样生效" % "、".join(warn))
    if prev:
        print("  (覆盖了 %s 的上一次批准)" % prev.get("approved_at"))


def cmd_revoke(P, a):
    if a.node not in NODE:
        die("没有节点 %s" % a.node)
    rec = P.state["nodes"].pop(a.node, None)
    if not rec:
        print("%s 本来就没有批准记录" % a.node)
        return
    P.state["history"].append({"node": a.node, "action": "revoke", "at": now_iso(), "quote": (a.quote or "").strip()})
    P.save()
    P.event("revoke", node=a.node, detail=(a.quote or "").strip() or None)
    print("✓ 撤销 %s(%s)的批准(原来 %s)" % (a.node, NODE[a.node]["cn"], rec.get("approved_at")))


def durations(P):
    """成对的 X.start / X.end → 耗时;X.rerun / X.fail 计次数。"""
    if not P.events_f.exists():
        return {}
    open_, out = {}, {}
    for ln in P.events_f.read_text("utf-8").splitlines():
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        name = str(e.get("event", ""))
        if "." not in name:
            continue
        stage, act = name.rsplit(".", 1)
        d = out.setdefault(stage, {"seconds": 0.0, "runs": 0, "rerun": 0, "fail": 0, "open": False})
        if act == "start":
            open_[stage] = e.get("ts")
            d["open"] = True
        elif act == "end" and stage in open_:
            d["seconds"] += max(0.0, float(e.get("ts", 0)) - float(open_.pop(stage) or 0))
            d["runs"] += 1
            d["open"] = False
        elif act in ("rerun", "fail"):
            d[act] += 1
    return out


def cmd_status(P, a):
    rows = [P.node_status(n) for n, _, _, _, _ in NODES]
    if a.json:
        print(json.dumps({"nodes": [{k: v for k, v in r.items() if k != "rec"} | (
            {"approved_at": r["rec"]["approved_at"], "quote": r["rec"]["quote"]} if r.get("rec") else {}) for r in rows],
            "durations": durations(P)}, ensure_ascii=False, indent=1))
        return
    mark = {"approved": "✓ 通过", "pending": "· 待审", "stale": "✗ 已失效"}
    print("审核节点 · %s" % P.root)
    for r in rows:
        n = r["node"]
        line = "  %-7s %-6s %s" % (n, NODE[n]["cn"], mark[r["status"]])
        if r.get("rec"):
            line += "  %s 「%s」" % (r["rec"]["approved_at"][:16].replace("T", " "), r["rec"]["quote"][:40])
        print(line)
        for w in r["why"][:6]:
            print("            └ " + fmt_why(w))
        if len(r["why"]) > 6:
            print("            └ … 还有 %d 处" % (len(r["why"]) - 6))
    stale = [r["node"] for r in rows if r["status"] == "stale"]
    if stale:
        print("⚠️ 已失效的节点要重新给用户看、拿到原话后再 approve:%s" % "、".join(stale))
    ds = durations(P)
    if ds:
        print("阶段耗时(events.jsonl 里成对的 .start / .end):")
        for st, d in sorted(ds.items()):
            print("  %-14s %7.1f min · %d 次%s%s%s" % (st, d["seconds"] / 60, d["runs"],
                                                   " · 重跑 %d" % d["rerun"] if d["rerun"] else "",
                                                   " · 失败 %d" % d["fail"] if d["fail"] else "", " · 进行中" if d["open"] else ""))


def cmd_check(P, a):
    if a.node not in NODE:
        die("没有节点 %s" % a.node)
    r = P.node_status(a.node)
    if r["status"] == "approved":
        print("✓ %s 通过且仍有效" % a.node)
        sys.exit(0)
    print("✗ %s %s" % (a.node, "还没通过" if r["status"] == "pending" else "已失效:" + ";".join(fmt_why(w) for w in r["why"][:3])))
    sys.exit(1)


def cmd_guard(P, a):
    rc = 0
    for f in a.files:
        rel = P.rel(f)
        keys = {rel}
        if rel == "project.json":
            keys.add(LOOK_KEY)
        hit = []
        for n, _, _, _, _ in NODES:
            st = P.node_status(n)
            if st["status"] == "approved" and P.lockset(n) & keys:
                hit.append(n)
        if not hit:
            print("✓ 可以改:%s 不属于任何已通过且仍有效的节点" % rel)
            continue
        rc = 3
        own = [n for n in hit if keys & (set(P.state["nodes"][n].get("inputs", {})) | set(P.state["nodes"][n].get("artifacts", {})))]
        src = own[0] if own else hit[0]
        rec = P.state["nodes"][src]
        print("🔒 %s 是已批准的版本:节点 %s(%s)%s 批准,用户原话「%s」" % (
            rel, src, NODE[src]["cn"], rec["approved_at"][:16].replace("T", " "), rec["quote"][:60]))
        print("   改动会让这些节点失效:%s" % "、".join("%s(%s)" % (n, NODE[n]["cn"]) for n in hit))
        if rel == "project.json":
            print("   (project.json 只有 look / levels_target 两个字段被锁;只改别的字段不会失效)")
        print("   生成脚本请写到新文件;真要覆盖,先跟用户确认,改完重新过审再 approve")
    sys.exit(rc)


def cmd_event(P, a):
    if a.node and a.node not in NODE:
        print("⚠️ %s 不是审核节点名(%s),照样记录" % (a.node, " / ".join(NODE)))
    rec = P.event(a.name, node=a.node, detail=a.detail)
    print("✓ 记下事件 %s · %s" % (rec["event"], rec["t"]))


def main():
    ap = argparse.ArgumentParser(description="审核节点状态和审批记录")
    ap.add_argument("proj")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("approve")
    p.add_argument("node")
    p.add_argument("--quote", required=True, help="用户原话")
    p.add_argument("--note")
    p.add_argument("--file", nargs="+", action="extend", help="额外锁定的文件(预演 / 成片 / 候选配乐…),只锁本节点")
    p = sub.add_parser("revoke")
    p.add_argument("node")
    p.add_argument("--quote")
    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("check")
    p.add_argument("node")
    p = sub.add_parser("guard")
    p.add_argument("files", nargs="+")
    p = sub.add_parser("event")
    p.add_argument("name")
    p.add_argument("--node")
    p.add_argument("--detail")
    a = ap.parse_args()
    if not a.cmd:
        ap.print_help()
        sys.exit(2)
    P = Proj(a.proj)
    {"approve": cmd_approve, "revoke": cmd_revoke, "status": cmd_status, "check": cmd_check, "guard": cmd_guard,
     "event": cmd_event}[a.cmd](P, a)


if __name__ == "__main__":
    main()
