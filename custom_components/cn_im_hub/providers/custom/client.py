"""Custom provider - auto-detect email or HTTP API from YAML."""

from __future__ import annotations

import asyncio
import contextlib
import email as email_lib
import imaplib
import logging
import re
import smtplib
import ssl
import uuid
from email import encoders
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Any

import voluptuous as vol
import yaml
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TemplateSelector

from ...core.command import execute_command, parse_command
from ...core.known_targets import async_get_tracker
from ...models import ProviderRuntime
from ..base import ProviderSpec

_LOGGER = logging.getLogger(__name__)

PROVIDER_CUSTOM = "custom"
_CONF_CUSTOM_YAML = "custom_yaml"

EVENT_LIVE_PROGRESS = "ha_crack_live_progress"
_LIVE_PROGRESS_SEND_INTERVAL_SECONDS = 2.0

_NOREPLY_PATTERNS = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "bounce", "notifications@",
    "automated@", "auto-confirm", "auto-reply", "automailer",
)

_EXAMPLE_YAML = '''# ========== Email Example ==========
provider:
  key: my_email
  title: My Email

email:
  address: "your@gmail.com"
  password: "app_password"
  imap_host: "imap.gmail.com"
  imap_port: 993
  smtp_host: "smtp.gmail.com"
  smtp_port: 587
  poll_interval: 15

# Common email servers:
# Gmail: imap.gmail.com / smtp.gmail.com
# QQ: imap.qq.com / smtp.qq.com
# 163: imap.163.com / smtp.163.com
# Outlook: outlook.office365.com / smtp.office365.com

# ========== WhatsApp Example ==========
# provider:
#   key: whatsapp
#   title: WhatsApp
#
# connection:
#   bot_token: "your_access_token"
#   phone_number_id: "your_phone_number_id"
#
# receive:
#   type: polling
#   interval: 5
#   url: "https://graph.facebook.com/v18.0/{phone_number_id}/messages"
#   method: GET
#   headers:
#     Authorization: "Bearer {bot_token}"
#   message_path: "messages[*]"
#   text_path: "text.body"
#   chat_id_path: "from"
#   user_id_path: "from"
#
# send:
#   url: "https://graph.facebook.com/v18.0/{phone_number_id}/messages"
#   method: POST
#   headers:
#     Authorization: "Bearer {bot_token}"
#   body:
#     messaging_product: "whatsapp"
#     to: "{target}"
#     type: "text"
#     text:
#       body: "{message}"
'''


def _format_live_progress(payload: dict[str, Any]) -> str:
    display_text = str(payload.get("display_text") or "").strip()
    if display_text:
        cleaned = display_text.replace("┊", "").replace("*", "").strip()
        return cleaned[:200]
    tool_name = str(payload.get("tool_name") or "").strip()
    if tool_name:
        return f"🔧 {tool_name}"
    return ""


def _build_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                _CONF_CUSTOM_YAML,
                default=current.get(_CONF_CUSTOM_YAML, _EXAMPLE_YAML),
            ): TemplateSelector(),
        }
    )


def _detect_provider_type(parsed: dict[str, Any]) -> tuple[str, str | None]:
    """Auto-detect provider type from YAML config.
    
    Returns (type, error_message). error_message is None if valid.
    """
    has_email = "email" in parsed
    has_http = "connection" in parsed or "receive" in parsed or "send" in parsed
    
    if has_email and has_http:
        return "", "Config conflict: contains both email and HTTP API config, keep only one"
    if not has_email and not has_http:
        return "", "Config missing: need email or HTTP API (connection/receive/send) config"
    if has_email:
        return "email", None
    return "http", None


async def async_validate_config(hass: HomeAssistant, config: dict[str, Any]) -> None:
    yaml_content = str(config.get(_CONF_CUSTOM_YAML, "")).strip()
    if not yaml_content:
        raise ValueError("YAML configuration is required")

    def _parse_yaml() -> dict[str, Any]:
        return yaml.safe_load(yaml_content)

    try:
        parsed = await hass.async_add_executor_job(_parse_yaml)
    except yaml.YAMLError as err:
        raise ValueError(f"Invalid YAML: {err}") from err

    if not isinstance(parsed, dict):
        raise ValueError("YAML must be a dictionary")

    provider = parsed.get("provider", {})
    if not provider.get("key"):
        raise ValueError("provider.key is required")
    if not provider.get("title"):
        raise ValueError("provider.title is required")

    provider_type, error = _detect_provider_type(parsed)
    if error:
        raise ValueError(error)

    if provider_type == "email":
        email_cfg = parsed.get("email", {})
        if not email_cfg.get("address"):
            raise ValueError("email.address is required")
        if not email_cfg.get("password"):
            raise ValueError("email.password is required")
        if not email_cfg.get("imap_host"):
            raise ValueError("email.imap_host is required")
        if not email_cfg.get("smtp_host"):
            raise ValueError("email.smtp_host is required")

        address = str(email_cfg.get("address", "")).strip()
        password = str(email_cfg.get("password", "")).strip()
        imap_host = str(email_cfg.get("imap_host", "")).strip()
        imap_port = int(email_cfg.get("imap_port", 993))
        smtp_host = str(email_cfg.get("smtp_host", "")).strip()
        smtp_port = int(email_cfg.get("smtp_port", 587))

        def _test_imap() -> None:
            imap = imaplib.IMAP4_SSL(imap_host, imap_port, timeout=30)
            try:
                imap.login(address, password)
            finally:
                try:
                    imap.logout()
                except Exception:
                    pass

        def _test_smtp() -> None:
            smtp = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
            try:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(address, password)
            finally:
                try:
                    smtp.quit()
                except Exception:
                    smtp.close()

        try:
            await hass.async_add_executor_job(_test_imap)
        except Exception as err:
            raise ValueError(f"IMAP connection failed: {err}") from err

        try:
            await hass.async_add_executor_job(_test_smtp)
        except Exception as err:
            raise ValueError(f"SMTP connection failed: {err}") from err

    else:
        connection = parsed.get("connection", {})
        if not connection.get("bot_token"):
            raise ValueError("connection.bot_token is required")
        send = parsed.get("send", {})
        if not send.get("url"):
            raise ValueError("send.url is required")


class HttpProviderClient:
    """Generic HTTP-based provider client."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict[str, Any],
        *,
        agent_id: str,
        show_live_progress: bool = False,
    ) -> None:
        self._hass = hass
        self._config = config
        self._agent_id = agent_id
        self._show_live_progress = show_live_progress
        self._session = async_get_clientsession(hass)
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._status = "disconnected"
        self._last_update_id = 0
        self._tracker = None
        self._lock = asyncio.Lock()

        provider = config.get("provider", {})
        self._key = str(provider.get("key", "custom"))
        self._title = str(provider.get("title", "Custom"))

        connection = config.get("connection", {})
        self._bot_token = str(connection.get("bot_token", ""))

        receive = config.get("receive", {})
        self._receive_type = str(receive.get("type", "polling"))
        self._poll_interval = max(1, int(receive.get("interval", 2)))
        self._receive_url = str(receive.get("url", ""))
        self._receive_method = str(receive.get("method", "GET")).upper()
        self._message_path = str(receive.get("message_path", ""))
        self._text_path = str(receive.get("text_path", "text"))
        self._chat_id_path = str(receive.get("chat_id_path", "chat.id"))
        self._user_id_path = str(receive.get("user_id_path", "from.id"))

        send = config.get("send", {})
        self._send_url = str(send.get("url", ""))
        self._send_method = str(send.get("method", "POST")).upper()
        self._send_body = dict(send.get("body", {}))
        self._send_headers = dict(send.get("headers", {}))

        receive_headers = receive.get("headers", {})
        self._receive_headers = dict(receive_headers) if isinstance(receive_headers, dict) else {}

    @property
    def status(self) -> str:
        return self._status

    @property
    def key(self) -> str:
        return self._key

    @property
    def title(self) -> str:
        return self._title

    def _interpolate(self, template: str, **kwargs: str) -> str:
        result = template
        result = result.replace("{bot_token}", self._bot_token)
        for key, value in kwargs.items():
            result = result.replace(f"{{{key}}}", str(value))
        return result

    def _extract_path(self, data: Any, path: str) -> Any:
        if not path:
            return data
        parts = path.replace("[*]", "").split(".")
        current = data
        for part in parts:
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit():
                idx = int(part)
                current = current[idx] if idx < len(current) else None
            else:
                return None
        return current

    async def async_start(self) -> None:
        async with self._lock:
            if self._running:
                return
            self._running = True
            if self._receive_type == "polling" and self._receive_url:
                self._poll_task = self._hass.async_create_background_task(
                    self._async_poll_loop(),
                    f"cn_im_hub_custom_{self._key}_poll",
                )
            self._status = "connected"

    async def async_stop(self) -> None:
        async with self._lock:
            self._running = False
            if self._poll_task is not None:
                self._poll_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._poll_task
                self._poll_task = None
            self._status = "disconnected"

    async def _async_poll_loop(self) -> None:
        while self._running:
            try:
                await self._async_poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.debug("Custom provider poll error: %s", err)
            await asyncio.sleep(self._poll_interval)

    async def _async_poll_once(self) -> None:
        if not self._receive_url:
            return

        url = self._interpolate(self._receive_url)
        if "getUpdates" in url and self._last_update_id:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}offset={self._last_update_id + 1}"

        headers = {}
        for key, value in self._receive_headers.items():
            if isinstance(value, str):
                headers[key] = self._interpolate(value)
            else:
                headers[key] = value

        try:
            async with asyncio.timeout(30):
                async with self._session.request(self._receive_method, url, headers=headers or None) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
        except asyncio.TimeoutError:
            _LOGGER.debug("Custom provider poll timeout")
            return
        except Exception as err:
            _LOGGER.debug("Custom provider poll request error: %s", err)
            return

        messages = self._extract_messages(data)
        for msg in messages:
            self._hass.async_create_task(
                self._async_handle_message(msg),
                f"cn_im_hub_custom_{self._key}_handle_msg",
            )

    def _extract_messages(self, data: Any) -> list[dict[str, Any]]:
        if not self._message_path:
            return [data] if isinstance(data, dict) else []

        path_parts = self._message_path.split("[*]")
        if len(path_parts) != 2:
            return []

        prefix, suffix = path_parts
        container = self._extract_path(data, prefix.rstrip("."))
        if not isinstance(container, list):
            return []

        results = []
        for item in container:
            if isinstance(item, dict) and "update_id" in item:
                self._last_update_id = max(self._last_update_id, item.get("update_id", 0))

            extracted = self._extract_path(item, suffix.lstrip(".")) if suffix else item
            if extracted and isinstance(extracted, dict):
                results.append(extracted)

        return results

    async def _async_handle_message(self, msg: dict[str, Any]) -> None:
        text = str(self._extract_path(msg, self._text_path) or "").strip()
        chat_id = str(self._extract_path(msg, self._chat_id_path) or "")
        user_id = str(self._extract_path(msg, self._user_id_path) or "")

        if not text or not chat_id:
            return

        if self._tracker is not None:
            try:
                await self._tracker.async_record(
                    provider=self._key,
                    target=chat_id,
                    target_type="chat_id",
                    display_name=user_id or chat_id,
                )
            except Exception as err:
                _LOGGER.debug("Custom provider tracker error: %s", err)

        try:
            command = parse_command(text)
        except ValueError as err:
            await self.async_send_text(chat_id, f"Invalid command: {err}", "chat_id")
            return

        if command is None:
            return

        conversation_id = f"{self._key}:{chat_id}"

        async def _run_live_progress_bridge() -> None:
            if not self._show_live_progress:
                await asyncio.Future()

            queue: asyncio.Queue[str] = asyncio.Queue()

            @callback
            def _listener(event) -> None:
                payload = event.data or {}
                if payload.get("conversation_id") != conversation_id:
                    return
                progress_text = _format_live_progress(payload)
                if progress_text:
                    queue.put_nowait(progress_text)

            unsub = self._hass.bus.async_listen(EVENT_LIVE_PROGRESS, _listener)
            last_sent = ""
            pending_tasks: list[asyncio.Task] = []
            
            async def _fire_and_forget(msg: str) -> None:
                with contextlib.suppress(Exception):
                    await self.async_send_text(chat_id, msg, "chat_id")
            
            try:
                while True:
                    progress_text = await queue.get()
                    if progress_text == last_sent:
                        continue
                    task = asyncio.create_task(_fire_and_forget(progress_text))
                    pending_tasks.append(task)
                    last_sent = progress_text
            except asyncio.CancelledError:
                if pending_tasks:
                    await asyncio.gather(*pending_tasks, return_exceptions=True)
            finally:
                unsub()

        progress_task = asyncio.create_task(_run_live_progress_bridge())

        try:
            reply = await execute_command(
                self._hass,
                command,
                conversation_id=conversation_id,
                agent_id=self._agent_id,
                user_id=user_id or chat_id,
            )
        except Exception as err:
            reply = f"Execution failed: {type(err).__name__}"
            _LOGGER.warning("Custom provider command execution failed: %s", err)
        finally:
            progress_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await progress_task

        if reply:
            await self.async_send_text(chat_id, reply, "chat_id")

    async def async_send_text(self, target: str, message: str, target_type: str) -> None:
        if not self._send_url:
            return

        url = self._interpolate(self._send_url, target=target, message=message)
        body = {}
        for key, value in self._send_body.items():
            if isinstance(value, str):
                body[key] = self._interpolate(value, target=target, message=message)
            else:
                body[key] = value

        headers = {}
        for key, value in self._send_headers.items():
            if isinstance(value, str):
                headers[key] = self._interpolate(value, target=target, message=message)
            else:
                headers[key] = value

        try:
            async with asyncio.timeout(30):
                async with self._session.request(
                    self._send_method,
                    url,
                    headers=headers or None,
                    json=body if self._send_method in ("POST", "PUT", "PATCH") else None,
                    params=body if self._send_method == "GET" else None,
                ) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        _LOGGER.debug("Custom provider send failed: %s", text[:200])
        except asyncio.TimeoutError:
            _LOGGER.debug("Custom provider send timeout")
        except Exception as err:
            _LOGGER.debug("Custom provider send error: %s", err)


def _decode_header_value(raw: str) -> str:
    parts = decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _extract_text_body(msg: email_lib.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                continue
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                continue
            if content_type == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    text = re.sub(r"<[^>]+>", "", html)
                    return text.strip()
        return ""
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        return ""


def _extract_email_address(raw: str) -> str:
    match = re.search(r"<([^>]+)>", raw)
    if match:
        return match.group(1).strip().lower()
    return raw.strip().lower()


def _is_automated_sender(address: str) -> bool:
    addr = address.lower()
    return any(pattern in addr for pattern in _NOREPLY_PATTERNS)


class EmailProviderClient:
    """Email provider client using IMAP and SMTP."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict[str, Any],
        *,
        agent_id: str,
        show_live_progress: bool = False,
    ) -> None:
        self._hass = hass
        self._agent_id = agent_id
        self._show_live_progress = show_live_progress

        provider = config.get("provider", {})
        self._key = str(provider.get("key", "email"))
        self._title = str(provider.get("title", "Email"))

        email_cfg = config.get("email", {})
        self._address = str(email_cfg.get("address", "")).strip()
        self._password = str(email_cfg.get("password", "")).strip()
        self._imap_host = str(email_cfg.get("imap_host", "")).strip()
        self._imap_port = int(email_cfg.get("imap_port", 993))
        self._smtp_host = str(email_cfg.get("smtp_host", "")).strip()
        self._smtp_port = int(email_cfg.get("smtp_port", 587))
        self._poll_interval = max(5, int(email_cfg.get("poll_interval", 15)))

        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._status = "disconnected"
        self._tracker = None
        self._lock = asyncio.Lock()

        self._seen_uids: set[bytes] = set()
        self._seen_uids_max = 2000
        self._thread_context: dict[str, dict[str, str]] = {}

    @property
    def status(self) -> str:
        return self._status

    @property
    def key(self) -> str:
        return self._key

    @property
    def title(self) -> str:
        return self._title

    async def async_start(self) -> None:
        async with self._lock:
            if self._running:
                return
            self._running = True

            def _init_seen_uids() -> set[bytes]:
                seen = set()
                try:
                    imap = imaplib.IMAP4_SSL(self._imap_host, self._imap_port, timeout=30)
                    try:
                        imap.login(self._address, self._password)
                        imap.select("INBOX")
                        status, data = imap.uid("search", None, "ALL")
                        if status == "OK" and data and data[0]:
                            for uid in data[0].split():
                                seen.add(uid)
                    finally:
                        try:
                            imap.logout()
                        except Exception:
                            pass
                except Exception as err:
                    _LOGGER.debug("Email init seen UIDs failed: %s", err)
                return seen

            self._seen_uids = await self._hass.async_add_executor_job(_init_seen_uids)
            if len(self._seen_uids) > self._seen_uids_max:
                sorted_uids = sorted(self._seen_uids, key=lambda u: int(u))
                self._seen_uids = set(sorted_uids[-self._seen_uids_max // 2:])

            self._poll_task = self._hass.async_create_background_task(
                self._async_poll_loop(),
                f"cn_im_hub_email_{self._key}_poll",
            )
            self._status = "connected"
            _LOGGER.info("Email provider started for %s", self._address)

    async def async_stop(self) -> None:
        async with self._lock:
            self._running = False
            if self._poll_task is not None:
                self._poll_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._poll_task
                self._poll_task = None
            self._status = "disconnected"

    async def _async_poll_loop(self) -> None:
        while self._running:
            try:
                await self._async_check_inbox()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.debug("Email poll error: %s", err)
            await asyncio.sleep(self._poll_interval)

    async def _async_check_inbox(self) -> None:
        messages = await self._hass.async_add_executor_job(self._fetch_new_messages)
        for msg_data in messages:
            self._hass.async_create_task(
                self._async_handle_message(msg_data),
                f"cn_im_hub_email_{self._key}_handle_msg",
            )

    def _fetch_new_messages(self) -> list[dict[str, Any]]:
        results = []
        try:
            imap = imaplib.IMAP4_SSL(self._imap_host, self._imap_port, timeout=30)
            try:
                imap.login(self._address, self._password)
                imap.select("INBOX")
                status, data = imap.uid("search", None, "UNSEEN")
                if status != "OK" or not data or not data[0]:
                    return results

                for uid in data[0].split():
                    if uid in self._seen_uids:
                        continue
                    self._seen_uids.add(uid)

                    status, msg_data = imap.uid("fetch", uid, "(RFC822)")
                    if status != "OK":
                        continue

                    raw_email = msg_data[0][1]
                    msg = email_lib.message_from_bytes(raw_email)

                    sender_raw = msg.get("From", "")
                    sender_addr = _extract_email_address(sender_raw)
                    sender_name = _decode_header_value(sender_raw)
                    if "<" in sender_name:
                        sender_name = sender_name.split("<")[0].strip().strip('"')

                    if _is_automated_sender(sender_addr):
                        continue

                    subject = _decode_header_value(msg.get("Subject", "(no subject)"))
                    message_id = msg.get("Message-ID", "")
                    body = _extract_text_body(msg)

                    results.append({
                        "sender_addr": sender_addr,
                        "sender_name": sender_name,
                        "subject": subject,
                        "message_id": message_id,
                        "body": body,
                    })
            finally:
                try:
                    imap.logout()
                except Exception:
                    pass
        except Exception as err:
            _LOGGER.debug("Email IMAP fetch error: %s", err)
        return results

    async def _async_handle_message(self, msg_data: dict[str, Any]) -> None:
        sender_addr = msg_data["sender_addr"]
        if sender_addr == self._address.lower():
            return

        subject = msg_data["subject"]
        body = msg_data["body"].strip()
        text = body
        if subject and not subject.startswith("Re:"):
            text = f"[Subject: {subject}]\n\n{body}"

        self._thread_context[sender_addr] = {
            "subject": subject,
            "message_id": msg_data["message_id"],
        }

        if self._tracker is not None:
            try:
                await self._tracker.async_record(
                    provider=self._key,
                    target=sender_addr,
                    target_type="email",
                    display_name=msg_data["sender_name"] or sender_addr,
                )
            except Exception as err:
                _LOGGER.debug("Email tracker error: %s", err)

        try:
            command = parse_command(text)
        except ValueError as err:
            await self.async_send_text(sender_addr, f"Invalid command: {err}", "email")
            return

        if command is None:
            return

        conversation_id = f"{self._key}:{sender_addr}"

        async def _run_live_progress_bridge() -> None:
            if not self._show_live_progress:
                await asyncio.Future()

            queue: asyncio.Queue[str] = asyncio.Queue()

            @callback
            def _listener(event) -> None:
                payload = event.data or {}
                if payload.get("conversation_id") != conversation_id:
                    return
                progress_text = _format_live_progress(payload)
                if progress_text:
                    queue.put_nowait(progress_text)

            unsub = self._hass.bus.async_listen(EVENT_LIVE_PROGRESS, _listener)
            last_sent = ""
            pending_tasks: list[asyncio.Task] = []
            
            async def _fire_and_forget(msg: str) -> None:
                with contextlib.suppress(Exception):
                    await self.async_send_text(sender_addr, f"[Progress] {msg}", "email")
            
            try:
                while True:
                    progress_text = await queue.get()
                    if progress_text == last_sent:
                        continue
                    task = asyncio.create_task(_fire_and_forget(progress_text))
                    pending_tasks.append(task)
                    last_sent = progress_text
            except asyncio.CancelledError:
                if pending_tasks:
                    await asyncio.gather(*pending_tasks, return_exceptions=True)
            finally:
                unsub()

        progress_task = asyncio.create_task(_run_live_progress_bridge())

        try:
            reply = await execute_command(
                self._hass,
                command,
                conversation_id=conversation_id,
                agent_id=self._agent_id,
                user_id=sender_addr,
            )
        except Exception as err:
            reply = f"Execution failed: {type(err).__name__}"
            _LOGGER.warning("Email command execution failed: %s", err)
        finally:
            progress_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await progress_task

        if reply:
            await self.async_send_text(sender_addr, reply, "email")

    async def async_send_text(self, target: str, message: str, target_type: str) -> None:
        try:
            await self._hass.async_add_executor_job(self._send_email, target, message)
        except Exception as err:
            _LOGGER.warning("Email send failed: %s", err)

    def _send_email(self, to_addr: str, body: str) -> str:
        msg = MIMEMultipart()
        msg["From"] = self._address
        msg["To"] = to_addr

        ctx = self._thread_context.get(to_addr, {})
        subject = ctx.get("subject", "Home Assistant")
        if not subject.startswith("Re:"):
            subject = f"Re: {subject}"
        msg["Subject"] = subject

        original_msg_id = ctx.get("message_id")
        if original_msg_id:
            msg["In-Reply-To"] = original_msg_id
            msg["References"] = original_msg_id

        msg["Date"] = formatdate(localtime=True)
        domain = self._address.split("@")[1] if "@" in self._address else "localhost"
        msg_id = f"<ha-{uuid.uuid4().hex[:12]}@{domain}>"
        msg["Message-ID"] = msg_id

        msg.attach(MIMEText(body, "plain", "utf-8"))

        smtp = smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=30)
        try:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self._address, self._password)
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:
                smtp.close()

        return msg_id


async def async_setup_provider(
    hass: HomeAssistant,
    config: dict[str, Any],
    *,
    agent_id: str,
    subentry_id: str,
) -> ProviderRuntime:
    yaml_content = str(config.get(_CONF_CUSTOM_YAML, "")).strip()

    def _parse_yaml() -> dict[str, Any]:
        return yaml.safe_load(yaml_content)

    parsed = await hass.async_add_executor_job(_parse_yaml)
    provider_type, _ = _detect_provider_type(parsed)
    tracker = await async_get_tracker(hass, subentry_id)

    if provider_type == "email":
        client = EmailProviderClient(
            hass,
            parsed,
            agent_id=agent_id,
            show_live_progress=False,
        )
        client._tracker = tracker
        await client.async_start()

        async def _async_send(target: str, message: str, target_type: str) -> None:
            await client.async_send_text(target, message, target_type)

        return ProviderRuntime(
            key=client.key,
            title=client.title,
            subentry_id=subentry_id,
            client=client,
            stop=client.async_stop,
            send_text=_async_send,
            status=lambda: client.status,
            known_targets=tracker.snapshot,
            selected_target=tracker.selected_target,
            select_target=tracker.async_select_target,
        )

    client = HttpProviderClient(
        hass,
        parsed,
        agent_id=agent_id,
        show_live_progress=False,
    )
    tracker = await async_get_tracker(hass, subentry_id)
    client._tracker = tracker
    await client.async_start()

    async def _async_send(target: str, message: str, target_type: str) -> None:
        await client.async_send_text(target, message, target_type)

    return ProviderRuntime(
        key=client.key,
        title=client.title,
        subentry_id=subentry_id,
        client=client,
        stop=client.async_stop,
        send_text=_async_send,
        status=lambda: client.status,
        known_targets=tracker.snapshot,
        selected_target=tracker.selected_target,
        select_target=tracker.async_select_target,
    )


PROVIDER_SPEC = ProviderSpec(
    key=PROVIDER_CUSTOM,
    schema_builder=_build_schema,
    validate_config=async_validate_config,
    setup_provider=async_setup_provider,
    allow_multiple=True,
)
