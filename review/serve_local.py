#!/usr/bin/env python3
"""本地审核台服务：把审核台目录放在本机端口上看，意见存在目录里的 feedback.json。

用法：python3 review/serve_local.py <审核台目录> [--port 8765]
  - 静态文件：<目录>/index.html、frames/、thumbs/ 原样提供；
  - 意见：GET /api/feedback 返回 {镜头号: {shot, status, note, updatedAt}}；POST /api/feedback/<镜头号> 写一条；
    全部存在 <目录>/feedback.json（status：ok 通过 / fix 要改 / wait 待定；整体意见的镜头号是 _all）。
  - 只监听 127.0.0.1。改版后重新生成 index.html，服务不用重启，用户刷新页面即可，已有意见保留。
"""
import json
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else ".")
port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8765
store = os.path.join(root, "feedback.json")
lock = threading.Lock()


def load():
    try:
        return json.load(open(store, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=root, **k)

    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8"}

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

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
        if not self.path.startswith("/api/feedback/"):
            return self._json(404, {"error": "not found"})
        from urllib.parse import unquote

        sid = unquote(self.path[len("/api/feedback/"):].strip("/"))
        n = int(self.headers.get("Content-Length") or 0)
        try:
            doc = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        with lock:
            data = load()
            data[sid] = {"shot": sid, "status": str(doc.get("status", "")), "note": str(doc.get("note", "")), "updatedAt": str(doc.get("updatedAt", ""))}
            tmp = store + ".tmp"
            json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, store)
        return self._json(200, {"ok": True})


if __name__ == "__main__":
    if not os.path.exists(os.path.join(root, "index.html")):
        print(f"⚠ {root} 里没有 index.html，先运行 review/build_review.py 或 review/local_page.py")
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    except OSError as e:
        sys.exit(f"端口 {port} 起不来（{e}），换一个：--port {port + 1}")
    print(f"审核台：http://localhost:{port}/   意见写在 {store}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
