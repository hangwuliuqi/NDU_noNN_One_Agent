"""OpenAI-compatible LLM transport with retries and complete interaction logging."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from pyhocon import ConfigTree

from .json_utils import extract_json_object
from .run_logging import InteractionLogger


class LlmConnectionError(RuntimeError):
    """表示采样 LLM 在配置次数内始终未能返回可解析结果。"""


class _ResponseDiagnosticError(ValueError):
    """A response-shape error carrying safe diagnostics collected so far."""

    def __init__(self, message: str, diagnostics: dict[str, Any]) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics



class LlmClient:
    """面向 OpenAI 兼容接口的统一客户端，并负责重试和完整交互日志。"""

    def __init__(
        self,
        module: str,
        conf: ConfigTree,
        interactions: InteractionLogger,
        logger: logging.Logger,
    ) -> None:
        self.module = module
        self.root_conf = conf
        self.conf = conf.get_config(f"llm.{module}")
        self.interactions = interactions
        self.logger = logger
        self.provider = self.conf.get_string("provider").lower()
        self.model_id = self.conf.get_string("model_id")
        self.base_url = self.conf.get_string("base_url")
        self.max_attempts = conf.get_int("llm.max_connection_attempts")
        self.retry_delay = conf.get_float("llm.retry_delay_seconds")
        self.timeout = conf.get_float("llm.request_timeout_seconds")
        self.client: Any | None = None
        self._preflight_completed = False
        # The agents use these fields when a single parsed/transport request
        # needs a higher-level schema-correction turn.  They intentionally
        # describe only the most recent complete_json call; conversation
        # history belongs to the agent and is scoped to one iteration.
        self.last_response_text: str | None = None
        self.last_error: Exception | None = None

    def describe(self) -> str:
        """返回日志中显示的采样模块、厂商、模型 ID 和接口地址。"""
        return f"module={self.module}, provider={self.provider}, model_id={self.model_id}, base_url={self.base_url}"

    def complete_json(
        self,
        prompt: str,
        iteration: int,
        mock_factory: Callable[[], dict[str, Any]] | None = None,
        history_messages: list[dict[str, str]] | None = None,
        attempt_limit: int | None = None,
    ) -> dict[str, Any]:
        """请求一个 JSON 对象；连接失败、解析失败等异常都按统一次数重试。"""
        max_characters = self.root_conf.get_int("llm.max_prompt_characters")
        if len(prompt) > max_characters:
            raise ValueError(f"{self.module} prompt contains {len(prompt)} characters; limit is {max_characters}")
        attempts = self.max_attempts if attempt_limit is None else int(attempt_limit)
        if attempts < 1:
            raise ValueError("attempt_limit must be positive")
        self.last_response_text = None
        self.last_error = None
        for attempt in range(1, attempts + 1):
            response_text: str | None = None
            diagnostics: dict[str, Any] | None = None
            try:
                if self.provider == "mock":
                    # mock 不访问网络，只验证提示词构造、JSON 解析和后续闭环代码。
                    if mock_factory is None:
                        raise ValueError("mock provider requires a mock response factory")
                    response_text = json.dumps(mock_factory(), ensure_ascii=False)
                else:
                    if self.client is None:
                        # The project owns the retry loop below. Disable the SDK's
                        # hidden retries so one failed call cannot multiply into
                        # several timeout cycles before attempt=1 is recorded.
                        self.client = self._create_client(
                            self.timeout,
                            max_retries=self.root_conf.get_int("llm.sdk_max_retries"),
                        )
                    # OpenAI 新接口使用 max_completion_tokens；DeepSeek/千问兼容接口使用 max_tokens。
                    token_args = (
                        {"max_completion_tokens": self.conf.get_int("max_output_tokens")}
                        if self.provider == "openai"
                        else {"max_tokens": self.conf.get_int("max_output_tokens")}
                    )
                    messages = [
                        {
                            "role": "system",
                            "content": self.root_conf.get_string("llm.system_prompt"),
                        },
                    ]
                    if history_messages:
                        messages.extend(history_messages)
                    messages.append({"role": "user", "content": prompt})
                    response = self.client.chat.completions.create(
                        model=self.model_id,
                        messages=messages,
                        temperature=self.conf.get_float("temperature"),
                        **token_args,
                    )
                    response_text, diagnostics = self._response_text_and_diagnostics(response)
                self.last_response_text = response_text
                parsed = extract_json_object(response_text)

                # 成功和失败都按调用顺序写入同一缩进 JSON 数组，确保每次重试都可以完整追溯。
                self.interactions.write(
                    iteration=iteration,
                    module=self.module,
                    provider=self.provider,
                    model_id=self.model_id,
                    attempt=attempt,
                    prompt=prompt,
                    response=response_text,
                    status="success",
                    diagnostics=diagnostics,
                )
                return parsed
            except Exception as exc:  # noqa: BLE001 - 网络、解析与空响应都必须进入统一重试路径。
                self.last_response_text = response_text
                self.last_error = exc
                if isinstance(exc, _ResponseDiagnosticError):
                    diagnostics = exc.diagnostics
                # 这里不仅捕获网络异常，也捕获空响应、JSON 解析失败等“连接正常但结果不可用”的情况。
                self.interactions.write(
                    iteration=iteration,
                    module=self.module,
                    provider=self.provider,
                    model_id=self.model_id,
                    attempt=attempt,
                    prompt=prompt,
                    response=response_text,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                    diagnostics=diagnostics,
                )
                self.logger.warning(
                    "LLM request failed: module=%s attempt=%d/%d error=%s",
                    self.module,
                    attempt,
                    attempts,
                    exc,
                )
                if attempt < attempts:
                    time.sleep(self.retry_delay)
        raise LlmConnectionError(
            f"llm.{self.module} could not return a valid response after {attempts} attempts"
        )

    def preflight_once(self, iteration: int = 0) -> None:
        """Check the same model endpoint with a tiny unrelated JSON request.

        The check is intentionally process-scoped: after it succeeds (or is
        disabled), later formal LLM calls do not repeat it.
        """
        if self._preflight_completed:
            return
        cfg = self.root_conf.get_config("llm.preflight")
        if not cfg.get_bool("enabled"):
            self._preflight_completed = True
            return
        preflight_prompt = cfg.get_string("user_prompt")
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            response_text: str | None = None
            diagnostics: dict[str, Any] | None = None
            try:
                if self.provider == "mock":
                    response_text = '{"status":"ok"}'
                    diagnostics = {
                        "response_type": "mock",
                        "choices_count": 1,
                        "choice_index": 0,
                        "finish_reason": "stop",
                        "content_state": "nonempty",
                        "content_is_none": False,
                        "content_length": len(response_text),
                        "content_preview": response_text,
                    }
                else:
                    client = self._create_client(
                        cfg.get_float("timeout_seconds"),
                        max_retries=self.root_conf.get_int("llm.sdk_max_retries"),
                    )
                    response = client.chat.completions.create(
                        model=self.model_id,
                        messages=[
                            {"role": "system", "content": cfg.get_string("system_prompt")},
                            {"role": "user", "content": preflight_prompt},
                        ],
                        temperature=cfg.get_float("temperature"),
                        **self._token_args(cfg.get_int("max_output_tokens")),
                    )
                    response_text, diagnostics = self._response_text_and_diagnostics(response)
                parsed = extract_json_object(response_text)
                if parsed.get("status") != "ok":
                    raise ValueError("preflight response must contain status='ok'")
                self.interactions.write(
                    iteration=iteration,
                    module=f"{self.module}_preflight",
                    provider=self.provider,
                    model_id=self.model_id,
                    attempt=attempt,
                    prompt=preflight_prompt,
                    response=response_text,
                    status="success",
                    diagnostics=diagnostics,
                )
                self._preflight_completed = True
                return
            except Exception as exc:  # noqa: BLE001 - all preflight failures are retryable.
                if isinstance(exc, _ResponseDiagnosticError):
                    diagnostics = exc.diagnostics
                last_error = exc
                message = f"{type(exc).__name__}: {exc}"
                self.interactions.write(
                    iteration=iteration,
                    module=f"{self.module}_preflight",
                    provider=self.provider,
                    model_id=self.model_id,
                    attempt=attempt,
                    prompt=preflight_prompt,
                    response=response_text,
                    status="failed",
                    error=message,
                    diagnostics=diagnostics,
                )
                self.logger.warning(
                    "LLM preflight failed: module=%s attempt=%d/%d error=%s diagnostics=%s",
                    self.module,
                    attempt,
                    self.max_attempts,
                    exc,
                    diagnostics or {},
                )
                if attempt < self.max_attempts:
                    time.sleep(self.retry_delay)

        self._preflight_completed = not cfg.get_bool("block_on_failure")
        if cfg.get_bool("block_on_failure"):
            raise LlmConnectionError(
                f"llm.{self.module} preflight failed after {self.max_attempts} attempts; "
                f"the large prompt was not sent: {last_error}"
            ) from last_error

    @staticmethod
    def _preview(value: Any, limit: int = 1000) -> str | None:
        if value is None:
            return None
        text = value if isinstance(value, str) else repr(value)
        return text if len(text) <= limit else text[:limit] + "..."

    @staticmethod
    def _content_text(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(getattr(item, "text", None), str):
                    parts.append(item.text)
            if parts:
                return "".join(parts)
        return str(value)

    def _response_text_and_diagnostics(self, response: Any) -> tuple[str, dict[str, Any]]:
        choices = getattr(response, "choices", None)
        diagnostics: dict[str, Any] = {
            "response_type": type(response).__name__,
            "response_id": getattr(response, "id", None),
            "response_model": getattr(response, "model", None),
            "choices_count": len(choices) if choices is not None else None,
            "choice_index": None,
            "finish_reason": None,
            "content_state": "missing",
            "content_is_none": None,
            "content_length": 0,
            "content_preview": None,
            "reasoning_content_state": "missing",
            "reasoning_content_length": 0,
            "reasoning_content_preview": None,
            "refusal_state": "missing",
            "refusal_length": 0,
            "refusal_preview": None,
            "usage": self._usage_diagnostics(getattr(response, "usage", None)),
        }
        if not choices:
            raise _ResponseDiagnosticError("LLM response contains no choices", diagnostics)
        choice = choices[0]
        diagnostics["choice_index"] = getattr(choice, "index", 0)
        diagnostics["finish_reason"] = getattr(choice, "finish_reason", None)
        message = getattr(choice, "message", None)
        if message is None:
            raise _ResponseDiagnosticError(
                "LLM response choice contains no message",
                diagnostics,
            )

        raw_content = getattr(message, "content", None)
        content = self._content_text(raw_content)
        reasoning = self._content_text(getattr(message, "reasoning_content", None))
        refusal = self._content_text(getattr(message, "refusal", None))
        diagnostics.update(
            {
                "content_state": (
                    "null" if raw_content is None else
                    "blank" if not content or not content.strip() else "nonempty"
                ),
                "content_is_none": raw_content is None,
                "content_length": len(content or ""),
                "content_preview": self._preview(content),
                "reasoning_content_state": (
                    "missing" if reasoning is None else
                    "blank" if not reasoning.strip() else "nonempty"
                ),
                "reasoning_content_length": len(reasoning or ""),
                "reasoning_content_preview": self._preview(reasoning),
                "refusal_state": (
                    "missing" if refusal is None else
                    "blank" if not refusal.strip() else "nonempty"
                ),
                "refusal_length": len(refusal or ""),
                "refusal_preview": self._preview(refusal),
            }
        )
        if diagnostics["finish_reason"] == "length":
            raise _ResponseDiagnosticError(
                "LLM response may be truncated because finish_reason=length",
                diagnostics,
            )
        if refusal and refusal.strip():
            raise _ResponseDiagnosticError(
                "LLM returned a refusal instead of usable JSON content",
                diagnostics,
            )
        if content is None:
            if reasoning and reasoning.strip():
                raise _ResponseDiagnosticError(
                    "LLM response content is empty; reasoning_content is present",
                    diagnostics,
                )
            raise _ResponseDiagnosticError(
                "LLM response message.content is null",
                diagnostics,
            )
        if not content.strip():
            if reasoning and reasoning.strip():
                raise _ResponseDiagnosticError(
                    "LLM response content is empty; reasoning_content is present",
                    diagnostics,
                )
            raise _ResponseDiagnosticError(
                "LLM response message.content is empty or whitespace-only",
                diagnostics,
            )
        return content, diagnostics

    @staticmethod
    def _usage_diagnostics(usage: Any) -> dict[str, Any] | None:
        if usage is None:
            return None
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }

    def _create_client(self, timeout: float, max_retries: int | None = None) -> Any:
        """Create the OpenAI-compatible client with the configured credential."""
        from openai import OpenAI

        api_key = self.conf.get_string("api_key") or os.getenv(
            self.conf.get_string("api_key_env"), ""
        )
        if not api_key:
            raise ValueError(
                f"no API key configured for llm.{self.module}; set llm.{self.module}.api_key or "
                f"environment variable {self.conf.get_string('api_key_env')}"
            )
        options: dict[str, Any] = {
            "api_key": api_key,
            "base_url": self.base_url,
            "timeout": timeout,
        }
        if max_retries is not None:
            options["max_retries"] = max_retries
        return OpenAI(**options)

    def _token_args(self, max_output_tokens: int) -> dict[str, int]:
        """Keep the provider-specific output-token field in one place."""
        return (
            {"max_completion_tokens": max_output_tokens}
            if self.provider == "openai"
            else {"max_tokens": max_output_tokens}
        )
