from __future__ import annotations

from dataclasses import dataclass
import logging
from textwrap import dedent
from typing import Any

import requests


LOGGER = logging.getLogger(__name__)


def _build_missing_config_message(*, missing_vars: list[str], mode_label: str) -> str:
    missing_joined = ", ".join(missing_vars)
    return dedent(
        f"""
        Telegram {mode_label} preflight failed.
        Missing required environment variables: {missing_joined}

        Required setup:
        1. Open Telegram and send /start or any test message to your bot first.
        2. Retrieve recent chat updates from PowerShell:
           $token = $env:TELEGRAM_BOT_TOKEN
           Invoke-RestMethod -Uri "https://api.telegram.org/bot$token/getUpdates"
        3. Find message.chat.id from the response and set TELEGRAM_CHAT_ID.

        PowerShell example:
        $env:TELEGRAM_BOT_TOKEN = "<your_bot_token>"
        $env:TELEGRAM_CHAT_ID = "<your_chat_id>"
        $env:TELEGRAM_DRY_RUN = "false"
        python -m src.main telegram-debug

        Direct send test:
        $token = $env:TELEGRAM_BOT_TOKEN
        $chatId = $env:TELEGRAM_CHAT_ID
        Invoke-RestMethod `
          -Uri "https://api.telegram.org/bot$token/sendMessage" `
          -Method Post `
          -ContentType "application/json" `
          -Body (@{{
              chat_id = $chatId
              text    = "Football-predictor Telegram test"
          }} | ConvertTo-Json)
        """
    ).strip()


@dataclass(frozen=True)
class TelegramUpdateRecord:
    chat_id: str
    chat_type: str
    title_or_username: str
    text_preview: str
    update_id: int


def validate_telegram_configuration(*, bot_token: str, chat_id: str, dry_run: bool, source: str) -> None:
    if dry_run:
        LOGGER.info("Telegram preflight: source=%s mode=dry-run", source)
        return

    missing_vars: list[str] = []
    if not bot_token.strip():
        missing_vars.append("TELEGRAM_BOT_TOKEN")
    if not chat_id.strip():
        missing_vars.append("TELEGRAM_CHAT_ID")
    if missing_vars:
        raise ValueError(_build_missing_config_message(missing_vars=missing_vars, mode_label=source))

    LOGGER.info("Telegram preflight: source=%s mode=live chat_id=%s", source, chat_id)


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, dry_run: bool = True) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.dry_run = dry_run

    def validate_live_configuration(self, *, source: str) -> None:
        validate_telegram_configuration(
            bot_token=self.bot_token,
            chat_id=self.chat_id,
            dry_run=self.dry_run,
            source=source,
        )

    def send_message(self, text: str, parse_mode: str = "Markdown") -> str:
        if self.dry_run:
            LOGGER.info("[TELEGRAM_DRY_RUN] message_prepared chars=%d", len(text))
            return f"DRY_RUN: {text}"

        self.validate_live_configuration(source="send_message")

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
        except requests.RequestException as exc:
            raise ValueError(f"Telegram sendMessage request failed: {exc}") from exc
        if response.status_code != 200:
            LOGGER.error("Telegram API error %s: %s", response.status_code, response.text)
            description = _extract_error_description(response)
            if response.status_code == 400 and "chat not found" in description.lower():
                raise ValueError(
                    "Telegram sendMessage failed: chat not found. "
                    "Your TELEGRAM_CHAT_ID likely does not belong to a recent chat for this bot. "
                    "Run 'python -m src.main telegram-debug' or call getUpdates from PowerShell, then set TELEGRAM_CHAT_ID to a chat.id that appears there."
                )
            raise ValueError(f"Telegram API request failed with status {response.status_code}: {description}")

        return "SENT"

    def get_updates(self, *, limit: int = 10) -> list[TelegramUpdateRecord]:
        if not self.bot_token.strip():
            raise ValueError(_build_missing_config_message(missing_vars=["TELEGRAM_BOT_TOKEN"], mode_label="telegram-debug"))

        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        try:
            response = requests.get(url, params={"limit": limit}, timeout=15)
        except requests.RequestException as exc:
            raise ValueError(f"Telegram getUpdates request failed: {exc}") from exc
        if response.status_code != 200:
            LOGGER.error("Telegram getUpdates error %s: %s", response.status_code, response.text)
            raise ValueError(f"Telegram getUpdates failed with status {response.status_code}")

        payload = response.json()
        results = payload.get("result", []) if isinstance(payload, dict) else []
        records: list[TelegramUpdateRecord] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            message = _extract_message_payload(item)
            chat = message.get("chat", {}) if isinstance(message, dict) else {}
            preview = str(message.get("text") or message.get("caption") or "")
            preview = preview.replace("\r", " ").replace("\n", " ").strip()[:80]
            title_or_username = str(chat.get("title") or chat.get("username") or chat.get("first_name") or "")
            records.append(
                TelegramUpdateRecord(
                    chat_id=str(chat.get("id", "")),
                    chat_type=str(chat.get("type", "unknown")),
                    title_or_username=title_or_username,
                    text_preview=preview,
                    update_id=int(item.get("update_id", 0) or 0),
                )
            )
        return records


def _extract_message_payload(update: dict[str, Any]) -> dict[str, Any]:
    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        value = update.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _extract_error_description(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict):
        return str(payload.get("description") or response.text)
    return response.text
