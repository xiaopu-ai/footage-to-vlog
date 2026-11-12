#!/usr/bin/env python3
"""capabilities.py · 能力探测:配乐、音效、转写这三样,这次从哪来。先找已有的,再要 key,最后才找用户要。

起因:同一个 skill,一个 agent 用宿主已经连好的音乐工具直接生成了配乐;另一个 agent 严格照文档走,一开工就让用户去接
ElevenLabs。「缺指定供应商的 key」和「完全没有生成能力」是两回事,要分开说。

用法:
  python3 scripts/capabilities.py <proj> [--music-file 路径…] [--sfx-file 路径…] [--transcript-file 路径…]
                                         [--host-tool "名称:说明"…] [--json]
    --music-file / --sfx-file / --transcript-file   用户给的现成文件(可多个;相对路径按当前目录解析)
    --host-tool  宿主(agent 运行环境)里已经连着、能直接调用的工具。脚本自己看不到宿主的工具清单,由 agent 查完传进来。
                 写法 "名称:说明",按说明里的关键词判断它能做哪几样(音乐 / 配乐 / music → 配乐;音效 / sfx / sound → 音效;
                 转写 / 字幕 / transcri / asr / speech-to-text → 转写);也可以写 "名称[music,sfx]:说明" 直接指定。
                 例:--host-tool "ChatCut[music,sfx,transcribe]:已连接,Mureka 生成配乐、生成音效、云端转写"
    --json       把结果 JSON 打印到标准输出

探测顺序(每一样各自找,找到第一条能用的就定下来,其余的记进 alternatives):
  1. 用户给的文件:命令行传的,加上项目里已有的(配乐:public/audio/music.*、work/music/*;音效:public/audio/sfx/*;
     转写:work/transcripts/*.json)。
  2. 宿主已连接的工具(--host-tool)。
  3. 环境变量 ELEVENLABS_API_KEY 有没有设置(本 skill 的 music_gen.py / sfx_gen.py / transcribe.py 用它)。
     🔴 只看有没有,绝不打印值、长度;不读 .env 的内容;不去别的目录找 key。
  4. 都没有。这时再看 <proj>/.env 和当前目录的 .env **文件在不在**(只看文件存在,不打开):
     在 → 「有配置但当前环境读不到」,多半是没加载(命令前加 set -a && . ./.env && set +a 再跑一遍本脚本确认);
     不在 → 确实要用户给文件或接入一个生成 / 转写服务。

产物 <proj>/work/capabilities.json:
  {"version": 1, "generated": 时间,
   "elevenlabs": {"env": "present" | "missing", "dotenv_files": [存在的 .env 路径…]},
   "host_tools": [{"name", "desc", "caps": [...]}],
   "music" | "sfx" | "transcribe": {
     "available": bool,
     "provider":  "user_file" | 宿主工具名 | "elevenlabs" | null,
     "transport": "file" | "host_tool" | "env_key" | "none",
     "credential_status": "n/a"(文件)| "managed_by_connection"(宿主工具)| "present" | "missing",
     "requires_user_setup": bool,      只有「确实要用户给文件或接入」时为 true
     "state": "ready" | "ready_without_elevenlabs" | "config_not_loaded" | "need_user",
     "evidence": 判断依据(一句话),
     "files": [用到的文件…](transport 为 file 时),
     "alternatives": [{"provider", "transport", "evidence"}](排在后面、也能用的路),
     "next_step": 下一步怎么做}}
  四种 state 就是要分开告诉用户的四种情况:
    ready                    能用,走 provider 这条路(ElevenLabs key 也在)
    ready_without_elevenlabs 没有 ElevenLabs key,但有别的能力可用(用户文件 / 宿主工具),不需要用户去接 ElevenLabs
    config_not_loaded        什么都没找到,但 .env 文件在:有配置,当前环境没加载
    need_user                确实没有:请用户给文件,或接入一个服务
有费用的生成(宿主工具或 ElevenLabs)先报价、用户同意再生成,见 references/music-sfx.md。
依赖:只用标准库(有 ffmpeg 时读一下音频文件头拿时长,不解码)。退出码:0(探测本身不判失败)。
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

CAPS = ("music", "sfx", "transcribe")
CAP_NAME = {"music": "配乐", "sfx": "音效", "transcribe": "转写"}
KEYWORDS = {
    "music": r"音乐|配乐|作曲|曲子|music|song|bgm|soundtrack|compose",
    "sfx": r"音效|sfx|sound ?effect|foley|sound",
    "transcribe": r"转写|转录|字幕|听写|语音识别|transcri|asr|speech[- ]to[- ]text|stt|scribe|whisper",
}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".aif", ".aiff"}
ENV_KEY = "ELEVENLABS_API_KEY"


def audio_duration(path):
    """读文件头拿时长(ffmpeg -i,不解码);拿不到返回 None。"""
    ff = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not ff:
        return None
    try:
        r = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None


def check_files(paths, kind):
    """→ (能用的文件[{path, note}], 问题[str])。"""
    ok, bad = [], []
    for p in paths:
        ap_ = os.path.abspath(p)
        if not os.path.isfile(ap_):
            bad.append(f"{p} 不存在")
            continue
        if os.path.getsize(ap_) == 0:
            bad.append(f"{p} 是空文件")
            continue
        ext = os.path.splitext(ap_)[1].lower()
        if kind in ("music", "sfx"):
            if ext not in AUDIO_EXT:
                bad.append(f"{p} 不是常见音频格式({ext or '无扩展名'})")
                continue
            d = audio_duration(ap_)
            ok.append({"path": ap_, "note": f"{d:.1f} s" if d else "时长未知"})
        else:
            ok.append({"path": ap_, "note": ext.lstrip(".") or "文件"})
    return ok, bad


def project_files(proj, kind):
    pats = {
        "music": ["public/audio/music.*", "work/music/*"],
        "sfx": ["public/audio/sfx/*"],
        "transcribe": ["work/transcripts/*.json"],
    }[kind]
    out = []
    for p in pats:
        out += sorted(x for x in glob.glob(os.path.join(proj, p)) if os.path.isfile(x))
    if kind in ("music", "sfx"):
        out = [x for x in out if os.path.splitext(x)[1].lower() in AUDIO_EXT]
    return out


def parse_host_tools(specs):
    tools = []
    for raw in specs:
        name, _, desc = raw.partition(":")
        name, desc = name.strip(), desc.strip()
        caps = None
        m = re.match(r"^(.*?)\[([^\]]*)\]\s*$", name)
        if m:
            name = m.group(1).strip()
            caps = [c.strip().lower() for c in m.group(2).split(",") if c.strip()]
            caps = [{"transcription": "transcribe", "asr": "transcribe", "sound": "sfx"}.get(c, c) for c in caps]
            caps = [c for c in caps if c in CAPS]
        if caps is None:
            text = f"{name} {desc}".lower()
            caps = [c for c in CAPS if re.search(KEYWORDS[c], text, re.I)]
            # 「sound」也会出现在「soundtrack」里:只说了配乐的不算音效
            if "sfx" in caps and not re.search(r"音效|sfx|sound ?effect|foley", text, re.I) and "music" in caps:
                caps.remove("sfx")
        tools.append({"name": name or "宿主工具", "desc": desc, "caps": caps})
    return tools


def main():
    ap = argparse.ArgumentParser(description="能力探测:配乐 / 音效 / 转写从哪来 → work/capabilities.json")
    ap.add_argument("proj")
    ap.add_argument("--music-file", nargs="+", action="extend", default=[])
    ap.add_argument("--sfx-file", nargs="+", action="extend", default=[])
    ap.add_argument("--transcript-file", nargs="+", action="extend", default=[])
    ap.add_argument("--host-tool", action="append", default=[], help="\"名称:说明\" 或 \"名称[music,sfx,transcribe]:说明\"")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    proj = os.path.abspath(a.proj)
    if not os.path.isdir(proj):
        sys.exit(f"✗ 项目目录不存在:{proj}")

    key_present = bool(os.environ.get(ENV_KEY))  # 只看有没有,不碰值
    dotenvs = []
    for d in dict.fromkeys([proj, os.getcwd()]):
        p = os.path.join(d, ".env")
        if os.path.isfile(p):  # 只看文件在不在,不打开
            dotenvs.append(p)
    tools = parse_host_tools(a.host_tool)
    given = {"music": a.music_file, "sfx": a.sfx_file, "transcribe": a.transcript_file}

    res = {"version": 1, "generated": datetime.now().isoformat(timespec="seconds"), "project": proj,
           "elevenlabs": {"env": "present" if key_present else "missing", "dotenv_files": dotenvs},
           "host_tools": tools}
    lines = []
    for cap in CAPS:
        paths = []
        g_ok, g_bad = check_files(given[cap], cap)
        p_ok, _ = check_files(project_files(proj, cap), cap)
        seen = set()
        for f in g_ok + p_ok:
            if f["path"] not in seen:
                seen.add(f["path"])
                paths.append({"provider": "user_file", "transport": "file", "credential_status": "n/a",
                              "files": [f], "evidence": f"现成文件 {f['path']}({f['note']})"
                              + ("" if f in g_ok else ",项目里已有")})
        if paths:
            files = [p["files"][0] for p in paths]
            paths = [{"provider": "user_file", "transport": "file", "credential_status": "n/a", "files": files,
                      "evidence": "现成文件:" + "、".join(f"{os.path.relpath(f['path'], proj) if f['path'].startswith(proj) else f['path']}({f['note']})"
                                                       for f in files[:6]) + (f" 等 {len(files)} 个" if len(files) > 6 else "")}]
        for t in tools:
            if cap in t["caps"]:
                paths.append({"provider": t["name"], "transport": "host_tool", "credential_status": "managed_by_connection",
                              "evidence": f"宿主已连接的工具「{t['name']}」" + (f":{t['desc']}" if t["desc"] else "")})
        if key_present:
            paths.append({"provider": "elevenlabs", "transport": "env_key", "credential_status": "present",
                          "evidence": f"环境变量 {ENV_KEY} 已设置(值不显示);用本 skill 的 "
                          + {"music": "music_gen.py", "sfx": "sfx_gen.py", "transcribe": "transcribe.py"}[cap]})
        bad_note = ("用户给的文件有问题:" + ";".join(g_bad)) if g_bad else ""
        if paths:
            best = dict(paths[0])
            state = "ready" if key_present or best["transport"] == "env_key" else "ready_without_elevenlabs"
            entry = {"available": True, "provider": best["provider"], "transport": best["transport"],
                     "credential_status": best["credential_status"], "requires_user_setup": False, "state": state,
                     "evidence": best["evidence"] + (f"。{bad_note}" if bad_note else "")}
            if best.get("files"):
                entry["files"] = [f["path"] for f in best["files"]]
            entry["alternatives"] = [{k: p[k] for k in ("provider", "transport", "evidence")} for p in paths[1:]]
            if best["transport"] == "file":
                entry["next_step"] = {"music": "直接用这首(先 tempo.py 测拍、bands.py 看结构);不需要任何生成能力",
                                      "sfx": "从这些文件里挑,sfx_prep.py 切头部静音、统一峰值",
                                      "transcribe": "直接用现成转写(要有逐字时间码),跳过 transcribe.py"}[cap]
            elif best["transport"] == "host_tool":
                entry["next_step"] = f"用宿主工具「{best['provider']}」生成;有费用的先报价、用户同意再调,回执记进 work/"
            else:
                entry["next_step"] = {"music": "music_gen.py 生成 2–3 首(先报价)", "sfx": "sfx_gen.py 生成(先报价)",
                                      "transcribe": "transcribe.py 转写"}[cap]
            msg = f"{CAP_NAME[cap]}:能用 → {best['provider'] if best['transport'] != 'file' else '现成文件'}({best['transport']})"
            if state == "ready_without_elevenlabs":
                msg += ";没有 ElevenLabs key,但这条路不需要它,不用让用户去接 ElevenLabs"
        else:
            if dotenvs:
                state, setup = "config_not_loaded", False
                ev = (f"环境变量 {ENV_KEY} 没设置,但 " + "、".join(dotenvs) + " 存在(没打开看内容):多半是 .env 没加载")
                nxt = "命令前加 set -a && . ./.env && set +a,再跑一次本脚本确认;加载后还没有 key,才算 need_user"
                msg = f"{CAP_NAME[cap]}:有配置但当前环境读不到 —— {ev}"
            else:
                state, setup = "need_user", True
                ev = f"没有现成文件、没有报告宿主工具、环境变量 {ENV_KEY} 没设置、项目和当前目录都没有 .env"
                nxt = {"music": "请用户二选一:给一个音乐文件(--music-file),或接入一个配乐生成服务(比如设置 ELEVENLABS_API_KEY)",
                       "sfx": "请用户给音效文件(--sfx-file),或接入音效生成服务;纯音乐的片子也可以不要音效",
                       "transcribe": "请用户给带逐字时间码的转写(--transcript-file),或接入转写服务(比如 ELEVENLABS_API_KEY);纯音乐的片子不需要"}[cap]
                msg = f"{CAP_NAME[cap]}:确实没有 —— {nxt}"
            entry = {"available": False, "provider": None, "transport": "none",
                     "credential_status": "missing", "requires_user_setup": setup, "state": state,
                     "evidence": ev + (f"。{bad_note}" if bad_note else ""), "alternatives": [], "next_step": nxt}
        res[cap] = entry
        lines.append(msg + (f"(⚠️ {bad_note})" if bad_note else ""))

    out = os.path.join(proj, "work", "capabilities.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    print(f"ElevenLabs:环境变量 {ENV_KEY} {'已设置(值不显示)' if key_present else '没设置'}"
          + (f";.env 文件存在:{'、'.join(dotenvs)}(没打开)" if dotenvs and not key_present else ""))
    if tools:
        print("宿主工具:" + ";".join(f"{t['name']} → {'/'.join(CAP_NAME[c] for c in t['caps']) or '(没认出能做哪样,用 名称[music,sfx]:说明 指定)'}"
                                    for t in tools))
    for ln in lines:
        print("  " + ln)
    states = {res[c]["state"] for c in CAPS}
    if states <= {"ready", "ready_without_elevenlabs"}:
        concl = "三样都有来源,不需要用户额外接入。"
    elif "need_user" in states:
        concl = "有缺的:" + "、".join(CAP_NAME[c] for c in CAPS if res[c]["state"] == "need_user") + " 确实要用户给文件或接入(纯音乐片不需要转写)。"
    else:
        concl = "有配置但没加载:先加载 .env 再跑一次本脚本,别急着让用户重新接入。"
    print(f"结论:{concl}")
    print(f"✓ {out}")


if __name__ == "__main__":
    main()
