import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from bot import main


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] not in ("/", "/healthz"):
            self.send_error(404)
            return
        body = b"ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server():
    port = int(os.environ.get("PORT", 3000))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


if __name__ == "__main__":
    # اجرای وب‌سرور برای Health Check لیارا در ترد جداگانه
    server_thread = Thread(target=run_server, daemon=True)
    server_thread.start()

    # اجرای حلقه اصلی بات
    main()
