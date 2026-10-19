"""List models available from the OpenAI-compatible endpoint in project.conf.

By default this script only requests ``GET /models`` and prints model IDs.
The optional chat-completion probe mirrors ``llmo.llm_client.LlmClient``.
No API key value is printed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    NotFoundError,
    OpenAI,
)

from llmo.config import load_config
from llmo.json_utils import extract_json_object


DEFAULT_CONFIG_PATH = Path(__file__).resolve().with_name("project.conf")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "List models available from the OpenAI-compatible endpoint "
            "configured in project.conf."
        )
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="path to the project HOCON configuration (default: project.conf beside this script)",
    )
    parser.add_argument(
        "--module",
        choices=("sampling",),
        default="sampling",
        help="which LLM configuration to test (sampling only)",
    )
    parser.add_argument(
        "--model",
        help="temporarily test another model ID without modifying project.conf",
    )
    parser.add_argument(
        "--base-url",
        help="temporarily test another base URL without modifying project.conf",
    )
    parser.add_argument(
        "--test-chat",
        action="store_true",
        help="after listing models, also test the configured model with Chat Completions",
    )
    return parser


def configured_api_key(module_name: str, module_conf: Any) -> tuple[str, str]:
    """Return the configured key and a safe description of its source."""
    direct_key = module_conf.get_string("api_key")
    environment_name = module_conf.get_string("api_key_env")
    if direct_key:
        return direct_key, f"project.conf llm.{module_name}.api_key"
    environment_key = os.getenv(environment_name, "")
    if environment_key:
        return environment_key, f"environment variable {environment_name}"
    raise ValueError(
        "no API key is available; configure the module api_key or set "
        f"environment variable {environment_name}"
    )


def token_arguments(provider: str, max_output_tokens: int) -> dict[str, int]:
    """Match LlmClient._token_args exactly."""
    if provider == "openai":
        return {"max_completion_tokens": max_output_tokens}
    return {"max_tokens": max_output_tokens}


def print_status_error(exc: APIStatusError, *, operation: str) -> None:
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    print(f"[FAIL] {operation}: HTTP {status}")
    if body is not None:
        print(f"       response body: {body}")
    else:
        print(f"       error: {exc}")

    body_text = str(body or exc).lower()
    if status in {401, 403}:
        print("       diagnosis: the URL responded, but the API key is invalid or lacks permission.")
    elif status == 404 and "model" in body_text:
        print("       diagnosis: the URL responded, but this model is unavailable for the key/group.")
    elif status == 404:
        print("       diagnosis: the server responded, but this OpenAI-compatible route was not found.")
    elif status == 429:
        print("       diagnosis: the server responded, but quota or rate limits rejected the request.")
    elif status is not None and status >= 500:
        print("       diagnosis: the URL responded, but the upstream service has a server-side failure.")


def probe_models(client: OpenAI, target_model: str) -> list[str] | None:
    """Return and display model IDs available to the configured key/group."""
    print("\nAvailable-model query: GET /models")
    try:
        page = client.models.list()
        model_ids = sorted(
            str(item.id) for item in page.data if getattr(item, "id", None)
        )
        print(f"[OK] /models returned {len(model_ids)} available model(s).")
        if model_ids:
            print("Available model IDs:")
            for index, model_id in enumerate(model_ids, start=1):
                marker = "  [configured]" if model_id == target_model else ""
                print(f"  {index:>2}. {model_id}{marker}")
        if target_model in model_ids:
            print(f"[OK] configured model is available: {target_model}")
        else:
            print(f"[WARN] configured model is not in the available list: {target_model}")
        return model_ids
    except APIStatusError as exc:
        print_status_error(exc, operation="GET /models")
        return None
    except (APIConnectionError, APITimeoutError) as exc:
        print(f"[FAIL] GET /models could not reach the endpoint: {type(exc).__name__}: {exc}")
        return None


def probe_chat_completion(
    client: OpenAI,
    *,
    model_id: str,
    provider: str,
    preflight_conf: Any,
) -> int:
    """Send the exact short preflight request used before a project LLM call."""
    print("\n[2/2] Project-equivalent probe: POST /chat/completions")
    print(f"      model={model_id}")
    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {
                    "role": "system",
                    "content": preflight_conf.get_string("system_prompt"),
                },
                {
                    "role": "user",
                    "content": preflight_conf.get_string("user_prompt"),
                },
            ],
            temperature=preflight_conf.get_float("temperature"),
            **token_arguments(
                provider,
                preflight_conf.get_int("max_output_tokens"),
            ),
        )
        response_text = response.choices[0].message.content or ""
        print(f"[OK] chat completion returned HTTP success. response={response_text!r}")
        try:
            parsed = extract_json_object(response_text)
        except Exception as exc:  # noqa: BLE001 - report compatibility failures clearly.
            print(
                "[FAIL] the endpoint answered, but the response is not the JSON object "
                f"required by this project: {type(exc).__name__}: {exc}"
            )
            return 5
        if parsed.get("status") != "ok":
            print(
                "[FAIL] JSON was returned, but the project preflight requires "
                "the field status='ok'."
            )
            return 5
        print("[PASS] URL, API key, model route, Chat Completions API, and preflight JSON all work.")
        return 0
    except AuthenticationError as exc:
        print_status_error(exc, operation="POST /chat/completions")
        return 2
    except NotFoundError as exc:
        print_status_error(exc, operation="POST /chat/completions")
        return 3
    except APITimeoutError as exc:
        print(f"[FAIL] POST /chat/completions timed out: {exc}")
        print("       diagnosis: DNS/TLS may work, but the endpoint did not answer within the timeout.")
        return 4
    except APIConnectionError as exc:
        print(f"[FAIL] POST /chat/completions could not connect: {exc}")
        print("       diagnosis: check DNS, proxy, TLS certificate, firewall, and base_url.")
        return 4
    except APIStatusError as exc:
        print_status_error(exc, operation="POST /chat/completions")
        return 1
    except Exception as exc:  # noqa: BLE001 - this is a standalone diagnostic.
        print(f"[FAIL] unexpected client/response error: {type(exc).__name__}: {exc}")
        return 1


def main() -> int:
    args = build_argument_parser().parse_args()
    config_path = Path(args.config).expanduser().resolve()
    try:
        conf = load_config(config_path)
        module_conf = conf.get_config(f"llm.{args.module}")
        preflight_conf = conf.get_config("llm.preflight")
        provider = module_conf.get_string("provider").lower()
        model_id = args.model or module_conf.get_string("model_id")
        base_url = args.base_url or module_conf.get_string("base_url")
        api_key, key_source = configured_api_key(args.module, module_conf)
        timeout = preflight_conf.get_float("timeout_seconds")
        max_retries = conf.get_int("llm.sdk_max_retries")
    except Exception as exc:  # noqa: BLE001 - config errors should be user-readable.
        print(f"[FAIL] configuration error: {type(exc).__name__}: {exc}")
        return 1

    print("LLM endpoint diagnostic")
    print(f"  config:       {config_path}")
    print(f"  module:       {args.module}")
    print(f"  provider:     {provider}")
    print(f"  base_url:     {base_url}")
    print(f"  model_id:     {model_id}")
    print(f"  key source:   {key_source} (value hidden)")
    print(f"  timeout:      {timeout} seconds")
    print(f"  SDK retries:  {max_retries}")

    if provider == "mock":
        print("[FAIL] provider=mock does not send an HTTP request; select a real module/provider.")
        return 1

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=max_retries,
    )
    model_ids = probe_models(client, model_id)
    if model_ids is None:
        return 1
    if not args.test_chat:
        return 0
    return probe_chat_completion(
        client,
        model_id=model_id,
        provider=provider,
        preflight_conf=preflight_conf,
    )


if __name__ == "__main__":
    sys.exit(main())
