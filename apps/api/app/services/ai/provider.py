"""
Talking to a language model.

Kept behind a small interface because the provider is a deployment decision, not
an architectural one -- the AI layer above this cares only that it gets JSON
back. Groq is what this installation uses; its API is OpenAI-compatible, so the
same class serves any endpoint that speaks that dialect, including OpenAI
itself, together with vLLM and Ollama behind a compatible shim.

The key lives encrypted in the metadata database like every other credential
here, and is never returned by any endpoint.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.secrets import SecretUnavailable, decrypt_password, encrypt_password
from app.models.metadata_models import AppSetting

logger = logging.getLogger(__name__)

#: Where the AI provider's configuration lives. One row, admin-managed.
SETTING_KEY = "ai_provider"

#: Groq speaks the OpenAI chat dialect, so this default serves any provider
#: that does. The model is a setting rather than a constant: hosted models are
#: renamed and retired often, and the screen lists what the key can actually
#: use rather than making somebody guess.
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "llama-3.3-70b-versatile"

#: Reasoning over a large schema is slow, and a suggestion that arrives late is
#: still useful. A suggestion that never arrives is not.
TIMEOUT = 120.0


class AIError(Exception):
    """A failure the person configuring or using the AI can act on."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class AIConfig:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str = ""
    enabled: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


# ---------------------------------------------------------------------------
# Stored configuration
# ---------------------------------------------------------------------------
def load_config(session: Session) -> AIConfig:
    """
    The stored provider settings.

    A key that cannot be decrypted is treated as absent rather than raised:
    the AI screen should say it needs configuring, not fail to load.
    """
    row = session.get(AppSetting, SETTING_KEY)
    value = row.value if row and isinstance(row.value, dict) else {}

    api_key = ""
    blob = value.get("api_key")
    if blob:
        try:
            api_key = decrypt_password(base64.b64decode(blob))
        except (SecretUnavailable, ValueError):
            logger.warning("Stored AI key could not be read; treating as unset")

    return AIConfig(
        base_url=value.get("base_url") or DEFAULT_BASE_URL,
        model=value.get("model") or DEFAULT_MODEL,
        api_key=api_key,
        enabled=bool(value.get("enabled", True)) and bool(api_key),
    )


def save_config(
    session: Session,
    base_url: str,
    model: str,
    api_key: str | None,
    enabled: bool,
) -> None:
    """Store the settings, encrypting the key. An omitted key keeps the stored one."""
    row = session.get(AppSetting, SETTING_KEY)
    current = row.value if row and isinstance(row.value, dict) else {}

    blob = current.get("api_key")
    if api_key:
        try:
            blob = base64.b64encode(encrypt_password(api_key)).decode("ascii")
        except SecretUnavailable as error:
            raise AIError(str(error)) from error

    value = {
        "base_url": (base_url or DEFAULT_BASE_URL).rstrip("/"),
        "model": model or DEFAULT_MODEL,
        "api_key": blob,
        "enabled": enabled,
    }
    if row is None:
        session.add(AppSetting(key=SETTING_KEY, value=value))
    else:
        row.value = value
    session.commit()


# ---------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------
class LLMProvider(Protocol):
    def complete(self, system: str, user: str, schema: dict | None = None) -> dict:
        ...


class OpenAICompatibleProvider:
    """
    Groq, and anything else speaking the OpenAI chat dialect.

    Asks for JSON back and parses it here, so every caller above receives a
    dictionary or an error -- never a string of prose that has to be scraped.
    """

    def __init__(self, config: AIConfig):
        if not config.configured:
            raise AIError(
                "No AI provider is configured. Add an API key on the AI Suggestions "
                "screen before asking for suggestions."
            )
        self._config = config

    def complete(self, system: str, user: str, schema: dict | None = None) -> dict:
        body: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Low but not zero: identical suggestions every time are less useful
            # than a little variety, and the output is validated either way.
            "temperature": 0.2,
        }
        if schema is not None:
            # Constrained decoding where the provider supports it. Where it does
            # not, the parse below still catches anything malformed.
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "report_suggestions", "schema": schema,
                                "strict": False},
            }
        else:
            body["response_format"] = {"type": "json_object"}

        try:
            response = httpx.post(
                f"{self._config.base_url}/chat/completions",
                json=body,
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=TIMEOUT,
            )
        except httpx.TimeoutException as error:
            raise AIError(
                f"The AI provider did not answer within {int(TIMEOUT)} seconds. "
                "A smaller question, or fewer tables, usually helps.",
                retryable=True,
            ) from error
        except httpx.HTTPError as error:
            raise AIError(
                f"Could not reach the AI provider: {self._redact(str(error))}",
                retryable=True,
            ) from error

        if response.status_code >= 400:
            # Not every model accepts constrained decoding, and the ones that
            # refuse it say so as a 400. Asking again without the constraint is
            # better than telling somebody their key is broken -- the prompt
            # names the fields too, and the answer is validated either way.
            if schema is not None and self._rejected_the_schema(response):
                logger.info("Provider refused json_schema; retrying without it")
                return self.complete(system, user, schema=None)
            raise self._explain(response)

        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as error:
            raise AIError("The AI provider returned something unreadable.") from error

        return self._as_json(content)

    @staticmethod
    def _as_json(content: str) -> dict:
        """
        The model's answer as a dictionary.

        Models wrap JSON in prose and fences even when told not to, so the
        object is extracted rather than assumed -- failing here would discard a
        perfectly good answer over a stray sentence.
        """
        text = (content or "").strip()
        if text.startswith("```"):
            text = text.split("```")[1] if "```" in text[3:] else text[3:]
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:]
        text = text.strip()

        try:
            parsed = json.loads(text)
        except ValueError:
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                raise AIError(
                    "The AI answered with prose rather than a report. Try asking "
                    "for something more specific."
                ) from None
            try:
                parsed = json.loads(text[start : end + 1])
            except ValueError as error:
                raise AIError("The AI's answer was not valid JSON.") from error

        if not isinstance(parsed, dict):
            raise AIError("The AI's answer was not in the expected shape.")
        return parsed

    @staticmethod
    def _rejected_the_schema(response: httpx.Response) -> bool:
        try:
            body = response.json()
        except ValueError:
            return False
        message = str(body.get("error", {}) if isinstance(body, dict) else body).lower()
        return "response_format" in message or "json_schema" in message

    def _redact(self, text: str) -> str:
        key = self._config.api_key
        return text.replace(key, "[the API key]") if len(key) >= 8 else text

    def _explain(self, response: httpx.Response) -> AIError:
        """The provider's own words, where they help, and the fix where they do not."""
        detail = ""
        try:
            body = response.json()
            error = body.get("error")
            if isinstance(error, dict):
                detail = str(error.get("message") or "")
            elif isinstance(error, str):
                detail = error
            detail = self._redact(detail)[:220]
        except ValueError:
            detail = self._redact(response.text[:200])

        status = response.status_code
        if status == 401:
            return AIError("The AI provider rejected this API key.")
        if status == 403:
            return AIError(
                f"This API key is not allowed to use the model '{self._config.model}'. "
                f"{detail}".strip()
            )
        if status == 404:
            return AIError(
                f"The model '{self._config.model}' was not found at "
                f"{self._config.base_url}. Check the model name."
            )
        if status == 429:
            return AIError(
                "The AI provider is rate-limiting this key. Try again shortly.",
                retryable=True,
            )
        if status >= 500:
            return AIError(
                "The AI provider is having trouble at their end.", retryable=True
            )
        return AIError(f"The AI provider refused the request. {detail}".strip())


    def models(self) -> list[str]:
        """
        The models this key can actually use.

        Asked rather than assumed, for the same reason the database picker lists
        real databases: a model name typed from memory fails with "not found",
        and hosted models are renamed and retired more often than anyone tracks.
        """
        try:
            response = httpx.get(
                f"{self._config.base_url}/models",
                headers={"Authorization": f"Bearer {self._config.api_key}"},
                timeout=30.0,
            )
        except httpx.HTTPError as error:
            raise AIError(
                f"Could not reach the AI provider: {self._redact(str(error))}",
                retryable=True,
            ) from error

        if response.status_code >= 400:
            raise self._explain(response)

        try:
            body = response.json()
        except ValueError as error:
            raise AIError("The AI provider's model list was not JSON.") from error

        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            return []
        return sorted(
            str(item.get("id"))
            for item in data
            if isinstance(item, dict) and item.get("id")
        )


def build_provider(session: Session) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(load_config(session))
