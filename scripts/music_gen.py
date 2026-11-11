#!/usr/bin/env python3
"""ElevenLabs 作曲:composition_plan(plan.json,格式见 assets/music-plans/*.plan.json)→ mp3。key 从环境变量读,不打印。

用法(仓库根目录):
  set -a && . ./.env && set +a && python3 scripts/music_gen.py plan.json out.mp3

注意(manus-2-cue v11 实测):生成结果常常不按计划走——段落时长不守、中途变速(90 → 120 BPM 的误判也常见)、
最后一段直接淡出、写了「开头一个冲击」也没有。拿到后先用 bands.py 看结构(贝斯 / 鼓什么时候进出、哪里蓄力、哪里砸),
grid.py 测拍,再用 bar_edit.py 按画面切点剪拼,不要指望一次生成就对上画面。
"""
import json, os, sys, time, urllib.error, urllib.request
KEY = os.environ.get("ELEVENLABS_API_KEY") or sys.exit("缺 ELEVENLABS_API_KEY")
body = json.dumps(json.load(open(sys.argv[1]))).encode()
for attempt in range(4):
    req = urllib.request.Request("https://api.elevenlabs.io/v1/music?output_format=mp3_44100_192", data=body, headers={"xi-api-key": KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = r.read()
        open(sys.argv[2], "wb").write(data)
        print(f"✓ {sys.argv[2]} {len(data)} bytes")
        break
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}", flush=True)
        if e.code in (429, 500, 502, 503):
            time.sleep(15 * (attempt + 1)); continue
        break
