"""标准库本地静态服务：评委网站（绑定 127.0.0.1，默认端口 8765）。

用法：python -B serve.py --port 8765
访问：http://127.0.0.1:8765/
只服务 site/ 与 output/ 下的静态文件，不提供目录列表，不出网。
（路径白名单——/ 映射 site/index.html，其余仅限 /site/ 与 /output/ 前缀，
不暴露项目根目录列表或源码下载。此服务仅限本机演示，不得用于承载公网业务。）
"""

import argparse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlsplit

BASE_DIR = Path(__file__).resolve().parent
ALLOWED_ROOTS = ("site", "output")


class SiteHandler(SimpleHTTPRequestHandler):
    """静态文件服务：根路径映射到 site/index.html；仅允许 site/ 与 output/ 白名单。"""

    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=BASE_DIR if directory is None else directory,
                         **kwargs)

    def send_head(self):
        """GET 与 HEAD 共用真实路径校验，符号链接也不能越过允许目录。"""
        try:
            parsed = urlsplit(self.path)
            decoded = unquote(parsed.path, errors="strict")
            if parsed.scheme or parsed.netloc or "\x00" in decoded or "\\" in decoded:
                self.send_error(400, "Invalid resource path")
                return None
            if ".." in decoded.split("/"):
                self.send_error(403, "Resource outside allowed directories")
                return None
            base = Path(self.directory).resolve()
            resource = (base / ("site/index.html" if decoded == "/"
                                else decoded.lstrip("/"))).resolve()
            if not any(resource.is_relative_to(base / name) for name in ALLOWED_ROOTS):
                self.send_error(403, "Resource outside allowed directories")
                return None
            if resource.is_dir():
                self.send_error(403, "Directory listing disabled")
                return None
        except (ValueError, OSError, RuntimeError):
            self.send_error(400, "Invalid resource path")
            return None
        self._resource_path = resource
        return super().send_head()

    def translate_path(self, path):
        return str(self._resource_path)

    def list_directory(self, path):
        self.send_error(403, "Directory listing disabled")
        return None

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()


def main():
    parser = argparse.ArgumentParser(description="供需透镜本地演示网站（仅本机演示用）")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = HTTPServer((args.host, args.port), SiteHandler)
    print(f"供需透镜演示网站：http://{args.host}:{args.port}/ （Ctrl+C 停止）")
    print("说明：仅服务 site/ 与 output/ 白名单资源，仅限本机演示，不得用于公网业务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
