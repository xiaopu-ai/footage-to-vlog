#!/usr/bin/env python3
"""成片抖动扫描:第 i 帧和 i+1 帧不同、但和 i+2 帧几乎相同 = 来回跳(并发渲染 3D 场景的栅格抖动)。

用法:python3 scripts/jitter_scan.py <成片.mp4>
输出每秒出现 ≥3 次来回跳的时间点 + 变化区域(480×270 坐标);没有输出 = 干净。
"""
import os,sys,subprocess
from PIL import Image, ImageChops
W,H=480,270; path=sys.argv[1]; off=float(sys.argv[2]) if len(sys.argv)>2 else 0
p=subprocess.Popen([ (os.environ.get("FFMPEG") or __import__("shutil").which("ffmpeg") or "ffmpeg"),'-v','error','-i',path,'-vf',f'scale={W}:{H},format=gray','-f','rawvideo','-'],stdout=subprocess.PIPE)
buf=[];i=0;hits=[]
def cnt(a,b):
  d=ImageChops.difference(a,b).point(lambda v:255 if v>30 else 0); return d.histogram()[255], d.getbbox()
while True:
  b=p.stdout.read(W*H)
  if len(b)<W*H: break
  buf.append(Image.frombytes('L',(W,H),b)); buf=buf[-3:]
  if len(buf)==3:
    n1,bb=cnt(buf[0],buf[1]); n2,_=cnt(buf[0],buf[2])
    if 15<n1<4000 and n2<n1*0.25: hits.append((i-2,n1,bb))
  i+=1
# group into seconds
from collections import defaultdict
g=defaultdict(list)
for f,n,bb in hits: g[int(f/30)].append((f,n,bb))
for s in sorted(g):
  if len(g[s])>=3: print(f"{int(s//60)}:{int(s%60):02d}  {len(g[s])} 次  例 bbox(480 宽) {g[s][0][2]}")
