#!/usr/bin/env python3
"""ElevenLabs 音效生成:按任务表逐个请求(并发会 429),每条可出 n 个版本,已存在的文件跳过(不重复扣费)。
key 从环境变量读,不打印。

用法(仓库根目录):
  set -a && . ./.env && set +a && python3 scripts/sfx_gen.py jobs.json <输出目录>
jobs.json = [{"name": "calendar", "text": "A heavy desk calendar ... set down on a wooden desk ... Dry, close-miked.", "sec": 0.6, "influence": 0.6, "n": 2}, ...]

经验(manus-2-cue v11):
- 提示词写「材质 + 动作 + 结果」,结尾加 "Dry, close-miked." 出来的是干净的单个事件;
- 很轻的动作(纸签、地球仪、点赞)常出成几乎无声的低频闷响,生成完用 sfx_sheet.py 看声谱挑,别盲用;
- 「chalk stroke / heavy stroke」容易出成多笔,要一笔的就挑包络只有一段的版本。
挑好后:裁剪 + 切头部静音 + 峰值 −3 dBFS,再用 sfx_probe.py --voice 定增益。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

KEY = os.environ.get("ELEVENLABS_API_KEY")
if not KEY:
    sys.exit("缺 ELEVENLABS_API_KEY(先 set -a && . ./.env && set +a)")
jobs = json.load(open(sys.argv[1]))
out = sys.argv[2]
os.makedirs(out, exist_ok=True)
URL = "https://api.elevenlabs.io/v1/sound-generation?output_format=mp3_44100_192"

for j in jobs:
    for k in range(1, j.get("n", 1) + 1):
        path = os.path.join(out, f"{j['name']}_v{k}.mp3")
        if os.path.exists(path):
            print("skip", path)
            continue
        body = json.dumps({"text": j["text"], "duration_seconds": j["sec"], "prompt_influence": j.get("influence", 0.6)}).encode()
        for attempt in range(5):
            req = urllib.request.Request(URL, data=body, headers={"xi-api-key": KEY, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                open(path, "wb").write(data)
                print(f"✓ {j['name']}_v{k}  {len(data)} bytes", flush=True)
                break
            except urllib.error.HTTPError as e:
                msg = e.read()[:200].decode("utf-8", "replace")
                print(f"  {j['name']}_v{k} HTTP {e.code}: {msg}", flush=True)
                if e.code in (429, 500, 502, 503):
                    time.sleep(6 * (attempt + 1))
                    continue
                break
            except Exception as e:  # 网络抖动
                print(f"  {j['name']}_v{k} {type(e).__name__}: {e}", flush=True)
                time.sleep(5)
        time.sleep(1.5)
