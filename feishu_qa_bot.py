from __future__ import annotations

import json
import os
import re
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

from signal_assistant.qa_service import route_text  # noqa: E402


def _parse_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_incoming_text(raw_text: str) -> str:
    # Remove Feishu mention tags like: <at user_id="xxx">Name</at>
    text = re.sub(r"<at\b[^>]*>.*?</at>", " ", raw_text, flags=re.IGNORECASE | re.DOTALL)
    return " ".join(text.split()).strip()


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
        body_text = response.text
        body = {}
        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.status_code >= 400:
            raise RuntimeError(
                f"Feishu send HTTP {response.status_code}: {body or body_text}"
            )
        if body.get("code", -1) != 0:
            raise RuntimeError(f"Feishu send failed: {body}")


class FeishuQAHandler(BaseHTTPRequestHandler):
    client: Optional[FeishuClient] = None
    verify_token: str = ""
    debug_echo: bool = True
    chat_last_symbol: dict[str, str] = {}
    processed_message_ids: dict[str, float] = {}
    dedupe_ttl_seconds: int = 600
    last_reply_by_chat: dict[str, tuple[str, float]] = {}
    reply_dedupe_seconds: int = 90

    @classmethod
    def _prune_processed_ids(cls, now_ts: float) -> None:
        expired = [
            mid
            for mid, ts in cls.processed_message_ids.items()
            if now_ts - ts > cls.dedupe_ttl_seconds
        ]
        for mid in expired:
            cls.processed_message_ids.pop(mid, None)

    @classmethod
    def _is_duplicate_message(cls, message_id: str) -> bool:
        if not message_id:
            return False
        now_ts = time.time()
        cls._prune_processed_ids(now_ts)
        if message_id in cls.processed_message_ids:
            return True
        cls.processed_message_ids[message_id] = now_ts
        return False

    @classmethod
    def _should_skip_duplicate_reply(cls, chat_id: str, reply_text: str) -> bool:
        if not chat_id or not reply_text:
            return False
        now_ts = time.time()
        previous = cls.last_reply_by_chat.get(chat_id)
        if previous is not None:
            prev_text, prev_ts = previous
            if prev_text == reply_text and (now_ts - prev_ts) <= cls.reply_dedupe_seconds:
                return True
        cls.last_reply_by_chat[chat_id] = (reply_text, now_ts)
        return False

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
                # For Feishu URL verification, always echo challenge to avoid
                # console-side validation failure caused by token mismatch or
                # delayed env propagation on cloud platforms.
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
            message_id = str(message.get("message_id", "")).strip()
            if self._is_duplicate_message(message_id):
                print(f"Feishu duplicate message ignored: {message_id}")
                self._send_json(200, {"code": 0})
                return

            chat_id = str(message.get("chat_id", ""))
            if not chat_id:
                self._send_json(200, {"code": 0})
                return

            content_raw = str(message.get("content", "{}"))
            text = _normalize_incoming_text(json.loads(content_raw).get("text", ""))
            print(f"Feishu inbound text: {text}")

            default_symbol = self.chat_last_symbol.get(chat_id)
            handled, reply, used_symbol = route_text(text, default_symbol=default_symbol)
            if handled and used_symbol:
                self.chat_last_symbol[chat_id] = used_symbol
            if self.client is not None:
                if handled:
                    if self._should_skip_duplicate_reply(chat_id, reply):
                        print(f"Feishu duplicate reply skipped for chat: {chat_id}")
                    else:
                        self.client.send_text_to_chat(chat_id, reply)
                elif self.debug_echo:
                    self.client.send_text_to_chat(
                        chat_id,
                        (
                            f"我收到了这句话：{text or '(empty)'}\n"
                            "你直接自然语言问我就行，例如“NVDA 现在能买吗？”或“SPY 期权怎么做？”。"
                        ),
                    )
            self._send_json(200, {"code": 0})
        except Exception as exc:
            print(f"Feishu event handling error: {exc}")
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
    debug_echo = _parse_bool(os.getenv("FEISHU_QA_DEBUG_ECHO"), True)
    reply_dedupe_seconds = int(os.getenv("FEISHU_QA_REPLY_DEDUPE_SECONDS", "90"))

    if not app_id or not app_secret:
        raise RuntimeError("Missing FEISHU_QA_APP_ID or FEISHU_QA_APP_SECRET in .env")

    FeishuQAHandler.client = FeishuClient(app_id=app_id, app_secret=app_secret)
    FeishuQAHandler.verify_token = verify_token
    FeishuQAHandler.debug_echo = debug_echo
    FeishuQAHandler.reply_dedupe_seconds = max(10, reply_dedupe_seconds)
    server = HTTPServer((host, port), FeishuQAHandler)
    print(f"Feishu QA bot listening on http://{host}:{port}/feishu/events")
    server.serve_forever()


if __name__ == "__main__":
    main()
