#!/usr/bin/env python3
# Простой HTTP-сервер с Basic Auth для раздачи «План.html».
# Логин/пароль и порт берутся из переменных окружения (см. systemd-юнит).
import base64
import http.server
import os
import socketserver

PORT = int(os.environ.get("PLAN_PORT", "8090"))
USER = os.environ.get("PLAN_USER", "plan")
PASS = os.environ.get("PLAN_PASS", "")
DIR = os.path.dirname(os.path.abspath(__file__))
FILE = "План.html"

EXPECTED = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DIR, **kw)

    def _deny(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Plan V3"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authed(self):
        return self.headers.get("Authorization", "") == EXPECTED

    def do_GET(self):
        if not self._authed():
            return self._deny()
        # Корень отдаёт основной файл; /download — скачивание вложением.
        if self.path in ("/", "/index.html"):
            return self._send_file()
        elif self.path == "/download":
            return self._download()
        return super().do_GET()

    def end_headers(self):
        # Запрет кэширования на ЛЮБОЙ ответ (в т.ч. прямой /План.html, /schema.html через
        # дефолтный обработчик) — иначе браузер держит старую версию, и правки «не подхватываются».
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def _send_file(self):
        path = os.path.join(DIR, FILE)
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _download(self):
        path = os.path.join(DIR, FILE)
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        # Кириллицу в заголовке latin-1 не закодирует: ASCII-fallback + RFC 5987.
        from urllib.parse import quote
        self.send_header(
            "Content-Disposition",
            "attachment; filename=\"Plan.html\"; "
            "filename*=UTF-8''" + quote(FILE),
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass  # тихий лог


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    with Server(("0.0.0.0", PORT), Handler) as httpd:
        httpd.serve_forever()
