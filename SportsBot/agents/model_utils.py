from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx
from openai import AsyncOpenAI

import config


_MODEL_FALLBACK_MARKERS = (
    "not_found_error",
    "model not found",
    "invalid model",
    "permission",
    "forbidden",
    "access denied",
    "not available",
    "unauthorized",
    "authentication",
    "invalid api key",
    "api key",
    "not configured",
    "missing",
    # Transient gateway/worker errors — a sick OmniRoute worker (e.g. gemini-cli
    # returning 500s) should yield to the configured fallback worker instead of
    # silently collapsing that agent's voice.
    "internal server error",
    "server error",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "502",
    "503",
    "504",
    "temporarily",
    "timed out",
    "timeout",
    "connection error",
    "connecterror",
    "read timeout",
)


@dataclass(slots=True)
class AgentClients:
    anthropic: anthropic.AsyncAnthropic | None = None
    openai: AsyncOpenAI | None = None


@dataclass(slots=True)
class ProviderModelSpec:
    provider: str
    model: str
    reasoning_effort: str | None = None


def is_model_access_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _MODEL_FALLBACK_MARKERS)


def build_backcompat_clients(
    *,
    clients: AgentClients | None = None,
    client: anthropic.AsyncAnthropic | None = None,
) -> AgentClients:
    if clients is not None:
        return clients
    return AgentClients(anthropic=client)


def _normalize_provider(provider: str) -> str:
    return str(provider or "").strip().lower()


def _spec_equal(left: ProviderModelSpec | None, right: ProviderModelSpec | None) -> bool:
    if left is None or right is None:
        return False
    return (
        _normalize_provider(left.provider) == _normalize_provider(right.provider)
        and str(left.model).strip() == str(right.model).strip()
        and (left.reasoning_effort or "").strip().lower()
        == (right.reasoning_effort or "").strip().lower()
    )


def _extract_anthropic_text(response: Any) -> str:
    content_blocks = []
    for block in getattr(response, "content", []):
        block_text = getattr(block, "text", "")
        if not block_text:
            continue
        block_type = getattr(block, "type", None)
        if block_type is None or block_type == "text" or not isinstance(block_type, str):
            content_blocks.append(block)
    if not content_blocks:
        raise ValueError(
            f"Anthropic returned no text block (stop_reason={getattr(response, 'stop_reason', None)})"
        )
    return content_blocks[0].text.strip()


def _extract_openai_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    text_parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
                continue
            value = getattr(text, "value", None)
            if isinstance(value, str) and value.strip():
                text_parts.append(value.strip())

    if text_parts:
        return "\n".join(text_parts).strip()
    raise ValueError("OpenAI Responses API returned an empty response")


def _extract_gemini_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        prompt_feedback = payload.get("promptFeedback") or {}
        block_reason = prompt_feedback.get("blockReason")
        if block_reason:
            raise ValueError(f"Gemini blocked the prompt: {block_reason}")
        raise ValueError("Gemini returned no candidates")

    text_parts: list[str] = []
    for part in candidates[0].get("content", {}).get("parts", []) or []:
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            text_parts.append(text.strip())

    if text_parts:
        return "\n".join(text_parts).strip()

    finish_reason = candidates[0].get("finishReason")
    raise ValueError(f"Gemini returned no text (finish_reason={finish_reason})")


async def _call_anthropic(
    *,
    client: anthropic.AsyncAnthropic | None,
    model: str,
    max_tokens: int,
    system: str,
    user_prompt: str,
) -> str:
    if client is None:
        raise ValueError("Anthropic client is not configured")
    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return _extract_anthropic_text(response)


async def _call_openai(
    *,
    client: AsyncOpenAI | None,
    model: str,
    max_tokens: int,
    system: str,
    user_prompt: str,
    reasoning_effort: str | None = None,
) -> str:
    if client is None:
        raise ValueError("OpenAI client is not configured")

    kwargs: dict[str, Any] = {
        "model": model,
        "instructions": system,
        "input": user_prompt,
        "max_output_tokens": max_tokens,
    }
    if reasoning_effort:
        kwargs["reasoning"] = {"effort": reasoning_effort}

    response = await client.responses.create(**kwargs)
    return _extract_openai_text(response)


async def _call_gemini(
    *,
    model: str,
    max_tokens: int,
    system: str,
    user_prompt: str,
) -> str:
    if not config.GEMINI_API_KEY:
        raise ValueError("Gemini API key is not configured")

    response = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        response = await client.post(
            f"{config.GEMINI_API_URL}/models/{model}:generateContent",
            params={"key": config.GEMINI_API_KEY},
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens},
            },
        )
        response.raise_for_status()
        payload = response.json()
    return _extract_gemini_text(payload)


async def _call_omniroute(
    *,
    model: str,
    max_tokens: int,
    system: str,
    user_prompt: str,
) -> str:
    """Route through the OmniRoute cookie-based gateway (zero API cost, + Grok).

    ``model`` is a worker name: ``claude-code`` / ``gpt-cursor`` / ``gemini-cli`` / ``grok``.
    The workers are chat UIs, so system + user are folded into one message.
    """
    if not config.OMNIROUTE_PASS:
        raise ValueError("OmniRoute password is not configured")
    base = config.OMNIROUTE_URL.rstrip("/")
    prompt = f"{system}\n\n{user_prompt}" if system else user_prompt
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(220.0, connect=15.0), verify=False
    ) as client:
        await client.post(
            f"{base}/api/auth/login",
            json={"username": config.OMNIROUTE_USER, "password": config.OMNIROUTE_PASS},
        )
        response = await client.post(
            f"{base}/api/managed-agents/chat-completion",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "tools": [],
                "tool_choice": "none",
                "max_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        payload = response.json()
    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"OmniRoute returned no content: {str(payload)[:200]}") from exc
    if not isinstance(text, str) or not text.strip():
        raise ValueError("OmniRoute returned empty content")
    return text.strip()


async def generate_text(
    *,
    clients: AgentClients,
    spec: ProviderModelSpec,
    max_tokens: int,
    system: str,
    user_prompt: str,
) -> str:
    provider = _normalize_provider(spec.provider)
    if provider == "anthropic":
        return await _call_anthropic(
            client=clients.anthropic,
            model=spec.model,
            max_tokens=max_tokens,
            system=system,
            user_prompt=user_prompt,
        )
    if provider == "openai":
        return await _call_openai(
            client=clients.openai,
            model=spec.model,
            max_tokens=max_tokens,
            system=system,
            user_prompt=user_prompt,
            reasoning_effort=spec.reasoning_effort,
        )
    if provider == "gemini":
        return await _call_gemini(
            model=spec.model,
            max_tokens=max_tokens,
            system=system,
            user_prompt=user_prompt,
        )
    if provider == "omniroute":
        return await _call_omniroute(
            model=spec.model,
            max_tokens=max_tokens,
            system=system,
            user_prompt=user_prompt,
        )
    raise ValueError(f"Unsupported provider: {spec.provider}")


async def generate_text_with_fallback(
    *,
    clients: AgentClients,
    primary_spec: ProviderModelSpec,
    fallback_spec: ProviderModelSpec | None,
    max_tokens: int,
    system: str,
    user_prompt: str,
    logger: logging.Logger,
    agent_label: str,
) -> str:
    try:
        return await generate_text(
            clients=clients,
            spec=primary_spec,
            max_tokens=max_tokens,
            system=system,
            user_prompt=user_prompt,
        )
    except Exception as exc:
        should_retry = (
            fallback_spec is not None
            and not _spec_equal(primary_spec, fallback_spec)
            and is_model_access_error(exc)
        )
        if should_retry:
            logger.warning(
                "%s primary %s:%s unavailable, retrying with %s:%s: %s",
                agent_label,
                primary_spec.provider,
                primary_spec.model,
                fallback_spec.provider,
                fallback_spec.model,
                exc,
            )
            return await generate_text(
                clients=clients,
                spec=fallback_spec,
                max_tokens=max_tokens,
                system=system,
                user_prompt=user_prompt,
            )
        raise


async def create_message_with_model_fallback(
    *,
    client: anthropic.AsyncAnthropic,
    primary_model: str,
    fallback_model: str,
    max_tokens: int,
    system: str,
    user_prompt: str,
    logger: logging.Logger,
    agent_label: str,
) -> Any:
    kwargs = {
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    try:
        return await client.messages.create(model=primary_model, **kwargs)
    except Exception as exc:
        if fallback_model != primary_model and is_model_access_error(exc):
            logger.warning(
                "%s primary model '%s' unavailable, retrying with '%s': %s",
                agent_label,
                primary_model,
                fallback_model,
                exc,
            )
            return await client.messages.create(model=fallback_model, **kwargs)
        raise
