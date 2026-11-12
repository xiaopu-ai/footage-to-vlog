#!/usr/bin/env python3
"""本地审核台服务：把审核台目录放在本机端口上看，意见存在目录里的 feedback.json。

用法：python3 review/serve_local.py <审核台目录> [--port 8765]
  - 静态文件：<目录>/index.html、frames/、thumbs/、media/ 原样提供；media/ 里可以是指向原文件的软链。
  - 音视频：支持 HTTP Range（206 Partial Content、Accept-Ranges、Content-Range、416），HTTP/1.1 + 多线程。
    Safari 的 <audio> / <video> 先发 `Range: bytes=0-1` 试探，服务器不回 206 就不播（python3 -m http.server 不行）。
  - 意见：GET /api/feedback 返回 {条目号: {shot, status, note, updatedAt[, played]}}；POST /api/feedback/<条目号> 写一条；
    played 只有媒体页会写：这条音视频在浏览器里实际播放过的比例（0–1，只增不减），用来区分「用户真的听过 / 看过」和「只点了通过」。
    全部存在 <目录>/feedback.json（status：ok 通过 / fix 要改 / wait 待定；整体意见的条目号是 _all）。
    分镜 / 风格帧页的条目号是镜头号，媒体页（build_review.py --kind media）是每个音视频的 id。
  - 只监听 127.0.0.1。改版后重新生成 index.html，服务不用重启，用户刷新页面即可，已有意见保留。
  - 为什么要起服务：不少 agent 宿主不能预览本地 mp3 / mp4，聊天里的图片也不一定显示；浏览器打开本地 HTTP 页面一定能看能听。
"""
import argparse
import email.utils
import json
import os
import re
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".md": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8",
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav", ".flac": "audio/flac",
    ".ogg": "audio/ogg", ".opus": "audio/ogg",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
    ".svg": "image/svg+xml",
}
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class _Slice:
    """文件的一段 [start, start+n)：copyfile 读到 n 字节就停。"""

    def __init__(self, f, n):
        self.f, self.n = f, n

    def read(self, k=-1):
        if self.n <= 0:
            return b""
        if k is None or k < 0 or k > self.n:
            k = self.n
        d = self.f.read(k)
        self.n -= len(d)
        return d

    def close(self):
        self.f.close()


def make_handler(root, store, lock):
    def load():
        try:
            with open(store, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    class H(SimpleHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive + Range；每个响应都带 Content-Length
        extensions_map = {**SimpleHTTPRequestHandler.extensions_map, **MEDIA_TYPES}

        def __init__(self, *a, **k):
            super().__init__(*a, directory=root, **k)

        def log_message(self, *a):
            pass

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def send_head(self):
            """普通文件照旧；带 Range 的单段请求回 206。多段 Range 不支持，回整个文件（200，规范允许）。"""
            path = self.translate_path(self.path)
            rng = self.headers.get("Range")
            if os.path.isdir(path) or not os.path.isfile(path):
                return super().send_head()
            try:
                f = open(path, "rb")
            except OSError:
                self.send_error(404, "File not found")
                return None
            try:
                fs = os.fstat(f.fileno())
                size = fs.st_size
                ctype = self.guess_type(path)
                m = RANGE_RE.match(rng.strip()) if rng else None
                if m and (m.group(1) or m.group(2)):
                    a, b = m.group(1), m.group(2)
                    if a == "":  # bytes=-N：最后 N 字节
                        n = int(b)
                        start, end = max(0, size - n), size - 1
                    else:
                        start = int(a)
                        end = min(int(b), size - 1) if b else size - 1
                    if start >= size or end < start:
                        f.close()
                        self.send_response(416)
                        self.send_header("Content-Range", "bytes */%d" % size)
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return None
                    f.seek(start)
                    self.send_response(206)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
                    self.send_header("Content-Length", str(end - start + 1))
                    self.send_header("Last-Modified", email.utils.formatdate(fs.st_mtime, usegmt=True))
                    self.end_headers()
                    return _Slice(f, end - start + 1)
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(size))
                self.send_header("Last-Modified", email.utils.formatdate(fs.st_mtime, usegmt=True))
                self.end_headers()
                return f
            except Exception:
                f.close()
                raise

        def copyfile(self, source, outputfile):
            try:
                super().copyfile(source, outputfile)
            except (BrokenPipeError, ConnectionResetError):
                pass  # 播放器拖动进度条时会主动断开旧请求，正常

        def _json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/feedback"):
                with lock:
                    return self._json(200, load())
            return super().do_GET()

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""  # 先读完请求体，keep-alive 连接才不会错位
            if not self.path.startswith("/api/feedback/"):
                return self._json(404, {"error": "not found"})
            from urllib.parse import unquote

            sid = unquote(self.path[len("/api/feedback/"):].split("?")[0].strip("/"))
            if not sid:
                return self._json(400, {"error": "missing id"})
            try:
                doc = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            with lock:
                data = load()
                old = data.get(sid) if isinstance(data.get(sid), dict) else {}
                rec = {"shot": sid, "status": str(doc.get("status", "")), "note": str(doc.get("note", "")),
                       "updatedAt": str(doc.get("updatedAt", ""))}
                # played:媒体页记的「实际播放过的比例」(0–1,浏览器 played 区间合计),只增不减;没传就沿用旧值
                try:
                    pl = max(0.0, min(1.0, float(doc["played"]))) if "played" in doc else None
                except (TypeError, ValueError):
                    pl = None
                vals = [x for x in (pl, old.get("played")) if isinstance(x, (int, float))]
                if vals:
                    rec["played"] = round(max(vals), 3)
                data[sid] = rec
                tmp = store + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=1)
                os.replace(tmp, store)
            return self._json(200, {"ok": True})

    return H


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def main():
    ap = argparse.ArgumentParser(description="本地审核台服务（支持 Range，Safari 能播音视频）")
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    store = os.path.join(root, "feedback.json")
    if not os.path.exists(os.path.join(root, "index.html")):
        print(f"⚠ {root} 里没有 index.html，先运行 review/build_review.py 或 review/local_page.py")
    try:
        srv = Server(("127.0.0.1", a.port), make_handler(root, store, threading.Lock()))
    except OSError as e:
        sys.exit(f"端口 {a.port} 起不来（{e}），换一个：--port {a.port + 1}")
    print(f"审核台：http://localhost:{a.port}/   意见写在 {store}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
