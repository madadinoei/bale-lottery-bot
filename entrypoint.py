from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from threading import Thread
from bot import api, main


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


def register_commands():
  commands = [
      {"command": "join", "description": "ثبت نام در قرعه کشی"},
      {"command": "leave", "description": "خروج از قرعه کشی"},
      {"command": "count", "description": "تعداد افراد ثبت نام شده"},
      {"command": "draw", "description": "انجام قرعه کشی (مخصوص مدیران)"},
  ]
  try:
    res = api("setMyCommands", json={"commands": commands})
    print(f"setMyCommands success: {res}", flush=True)
  except Exception as e:
    print(f"setMyCommands warning: {e}", flush=True)


if __name__ == "__main__":
  try:
    api("deleteWebhook")
  except Exception as e:
    print(f"deleteWebhook warning: {e}", flush=True)

  # ثبت خودکار کامندها در بله
  register_commands()

  port = int(os.getenv("PORT", "3000"))
  server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
  Thread(target=server.serve_forever, daemon=True).start()
  main()
