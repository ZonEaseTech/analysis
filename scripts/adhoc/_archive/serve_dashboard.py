#!/usr/bin/env python3
"""
对账大屏本地 web server

启动后浏览器访问 http://localhost:<port>/

目录解析顺序 (默认):
  1. --dir 指定的目录
  2. 脚本同目录 (适合解压 tar 包后直接运行)
  3. 脚本父目录的 exports/ (适合在源码 repo 里运行)
  4. 当前工作目录

特性:
  - serve 目录里所有文件 (含 dashboard.html, 对账 Excel, tar 包)
  - 根路径 / 自动列出所有对账产物 + 跳转 dashboard
  - 自动选可用端口 (8765 起, 冲突就换)
  - 默认绑 127.0.0.1 (本机), --host 0.0.0.0 可对外

Usage:
    python3 serve_dashboard.py                # 默认: 解压目录 / exports/
    python3 serve_dashboard.py --port 9000
    python3 serve_dashboard.py --dir /some/path
    python3 serve_dashboard.py --host 0.0.0.0  # 局域网可访问
    python3 serve_dashboard.py --open          # 启动后自动打开浏览器
"""

import argparse
import http.server
import os
import socket
import socketserver
import sys
import webbrowser
from pathlib import Path


def resolve_serve_dir(cli_dir: str | None) -> Path:
    """按优先级解析 serve 目录"""
    if cli_dir:
        return Path(cli_dir).resolve()
    here = Path(__file__).resolve().parent
    # 1) 脚本同目录有 dashboard.html → tar 解压场景
    if (here / "dashboard.html").exists():
        return here
    # 2) 父目录 exports/ → 源码 repo 场景
    sib = here.parent / "exports"
    if sib.exists() and (sib / "dashboard.html").exists():
        return sib
    # 3) 当前 cwd
    cwd = Path.cwd()
    if (cwd / "dashboard.html").exists():
        return cwd
    # 4) fallback 到脚本同目录 (即使没 dashboard 也 serve)
    return here


def pick_free_port(start: int, host: str = "127.0.0.1") -> int:
    """从 start 开始找一个能 bind 的端口"""
    for p in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"端口 {start}-{start+49} 都被占用")


def index_html(serve_dir: Path) -> bytes:
    """根路径首页：列出 serve 目录下所有产物"""
    files = sorted([f for f in serve_dir.iterdir() if f.is_file()],
                   key=lambda f: (-f.stat().st_mtime,))

    rows = []
    for f in files:
        size_kb = f.stat().st_size / 1024
        size_str = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
        icon = {'.xlsx': '📊', '.html': '📺', '.gz': '📦',
                '.json': '📄', '.csv': '📋'}.get(f.suffix, '📁')
        rows.append(f"<tr><td>{icon}</td>"
                    f'<td><a href="/{f.name}">{f.name}</a></td>'
                    f"<td>{size_str}</td></tr>")
    rows_html = "\n".join(rows)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>华莱士对账数据 Server</title>
<style>
body{{background:#0a1628;color:#e8eef5;font-family:-apple-system,"PingFang SC",sans-serif;padding:32px;max-width:1000px;margin:0 auto}}
h1{{font-size:32px;background:linear-gradient(90deg,#4dd0e1,#80cbc4);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:8px}}
p.sub{{color:#7a99c2;margin-bottom:24px}}
.cta{{display:inline-block;background:#1976d2;color:white;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:600;font-size:16px;margin-bottom:32px}}
.cta:hover{{background:#1565c0}}
table{{width:100%;border-collapse:collapse;background:#162234;border-radius:8px;overflow:hidden}}
th{{background:#1e3a5f;color:#b3e5fc;padding:12px;text-align:left}}
td{{padding:10px 12px;border-bottom:1px solid #243958;color:#cfd8dc}}
td a{{color:#80cbc4;text-decoration:none}}
td a:hover{{color:#4dd0e1;text-decoration:underline}}
tr:hover td{{background:#1a2740}}
.section{{margin-top:24px;color:#b3e5fc;font-size:18px;border-left:4px solid #4dd0e1;padding-left:12px;margin-bottom:12px}}
</style></head><body>
<h1>🏪 华莱士 (泰国) 对账数据中心</h1>
<p class="sub">本地 Web Server · serve exports/ 目录</p>
<a class="cta" href="/dashboard.html">📺 打开对账大屏</a>
<div class="section">📂 所有产出文件 (按修改时间倒序)</div>
<table>
<thead><tr><th></th><th>文件名</th><th>大小</th></tr></thead>
<tbody>
{rows_html}
</tbody></table>
</body></html>
""".encode("utf-8")


def make_handler(serve_dir: Path):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kw):
            super().__init__(*args, directory=str(serve_dir), **kw)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                body = index_html(serve_dir)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def log_message(self, fmt, *args):
            sys.stderr.write(f"  [{self.address_string()}] {fmt % args}\n")
    return Handler


def main():
    p = argparse.ArgumentParser(description="对账大屏本地 web server")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="127.0.0.1",
                   help="默认 127.0.0.1; 0.0.0.0 局域网可访问")
    p.add_argument("--dir", help="serve 目录 (默认: 脚本同目录)")
    p.add_argument("--open", action="store_true", help="自动打开浏览器")
    args = p.parse_args()

    serve_dir = resolve_serve_dir(args.dir)
    if not serve_dir.exists():
        sys.exit(f"❌ {serve_dir} 不存在")

    port = pick_free_port(args.port, args.host)

    print("=" * 60)
    print(f"  📺 对账大屏 Server 已启动")
    print("=" * 60)
    print(f"  📂 目录: {serve_dir}")
    has_dash = (serve_dir / "dashboard.html").exists()
    print(f"  📊 dashboard.html: {'✅ 找到' if has_dash else '❌ 未找到'}")
    print(f"  🌐 访问: http://{args.host}:{port}/")
    if has_dash:
        print(f"  📊 大屏: http://{args.host}:{port}/dashboard.html")
    if args.host != "127.0.0.1":
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
            print(f"  🌍 局域网: http://{local_ip}:{port}/")
        except Exception:
            pass
    print(f"  🛑 Ctrl+C 停止")
    print("=" * 60)

    if args.open:
        webbrowser.open(f"http://127.0.0.1:{port}/")

    with socketserver.TCPServer((args.host, port), make_handler(serve_dir)) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  已停止")


if __name__ == "__main__":
    main()
