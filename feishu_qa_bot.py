from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys
import time
from typing import Optional

import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from signal_assistant.qa_service import route_command  # noqa: E402


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._tenant_token = ""
        self._token_expire_at = 0.0

    def _get_tenant_token(self) -> str:
        now = time.time()
        if self._tenant_token and now < self._token_expire_at:
            return self._tenant_token

        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {"app_id": self._app_id, "app_secret": self._app_secret}
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        body = response.json()
        if body.get("code", -1) != 0:
            raise RuntimeError(f"Feishu token request failed: {body}")

        self._tenant_token = str(body["tenant_access_token"])
        expire_sec = int(body.get("expire", 7200))
        self._token_expire_at = now + max(60, expire_sec - 120)
        return self._tenant_token

    def send_text_to_chat(self, chat_id: str, text: str) -> None:
        token = self._get_tenant_token()
        url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        payload = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        body = response.json()
        if body.get("code", -1) != 0:
            raise RuntimeError(f"Feishu send failed: {body}")


class FeishuQAHandler(BaseHTTPRequestHandler):
    client: Optional[FeishuClient] = None
    verify_token: str = ""

    def _send_json(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/feishu/events":
            self._send_json(404, {"code": 404, "msg": "not found"})
            return

        try:
            content_len = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_len).decode("utf-8")
            body = json.loads(raw or "{}")

            if body.get("type") == "url_verification":
                token = str(body.get("token", ""))
                if self.verify_token and token != self.verify_token:
                    self._send_json(403, {"code": 403, "msg": "invalid verify token"})
                    return
                self._send_json(200, {"challenge": body.get("challenge", "")})
                return

            header = body.get("header", {})
            event_type = str(header.get("event_type", ""))
            if event_type != "im.message.receive_v1":
                self._send_json(200, {"code": 0})
                return

            event = body.get("event", {})
            message = event.get("message", {})
            if str(message.get("message_type", "")) != "text":
                self._send_json(200, {"code": 0})
                return

            chat_id = str(message.get("chat_id", ""))
            if not chat_id:
                self._send_json(200, {"code": 0})
                return

            content_raw = str(message.get("content", "{}"))
            text = json.loads(content_raw).get("text", "").strip()

            handled, reply = route_command(text)
            if handled and self.client is not None:
                self.client.send_text_to_chat(chat_id, reply)
            self._send_json(200, {"code": 0})
        except Exception as exc:
            self._send_json(200, {"code": 0, "msg": f"ignored with error: {exc}"})

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def main() -> None:
    load_dotenv()
    app_id = os.getenv("FEISHU_QA_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_QA_APP_SECRET", "").strip()
    verify_token = os.getenv("FEISHU_QA_VERIFY_TOKEN", "").strip()
    host = os.getenv("FEISHU_QA_HOST", "0.0.0.0").strip()
    # Prefer explicit FEISHU_QA_PORT for platforms requiring fixed internal port mapping.
    # Fallback to generic PORT when FEISHU_QA_PORT is not provided.
    port = int(os.getenv("FEISHU_QA_PORT", os.getenv("PORT", "8091")))

    if not app_id or not app_secret:
        raise RuntimeError("Missing FEISHU_QA_APP_ID or FEISHU_QA_APP_SECRET in .env")

    FeishuQAHandler.client = FeishuClient(app_id=app_id, app_secret=app_secret)
    FeishuQAHandler.verify_token = verify_token
    server = HTTPServer((host, port), FeishuQAHandler)
    print(f"Feishu QA bot listening on http://{host}:{port}/feishu/events")
    server.serve_forever()


if __name__ == "__main__":
    main()
