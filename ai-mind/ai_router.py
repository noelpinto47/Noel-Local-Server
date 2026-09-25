import os
import time
import threading
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


@dataclass
class ProviderState:
    name: str
    model: str
    available: bool = True
    failures: int = 0
    cooldown_until: float = 0
    last_latency_ms: float | None = None
    last_error: str | None = None


class AIRouter:
    """
    Multi-provider AI router.

    Internal AI Mind message metadata is deliberately removed before
    messages are sent to external providers.

    Providers are tried in configured priority order.
    Failed providers are temporarily put into cooldown.
    Successful providers recover automatically.
    """

    COOLDOWN_SECONDS = 60

    def __init__(self):
        self.lock = threading.Lock()

        self.max_tokens = int(
            os.getenv("AI_MAX_TOKENS", "4096")
        )

        self.test_fail_providers = {
            provider.strip().lower()
            for provider in os.getenv(
                "AI_TEST_FAIL_PROVIDERS",
                ""
            ).split(",")
            if provider.strip()
        }

        self.providers = self._build_providers()

    # ---------------------------------------------------------
    # Message sanitization
    # ---------------------------------------------------------

    @staticmethod
    def sanitize_messages(
        messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Remove AI Mind internal fields such as `metadata` before
        sending conversation history to external model APIs.

        External providers generally accept only the standard
        OpenAI-compatible message properties.
        """

        cleaned = []

        allowed_keys = {
            "role",
            "content",
            "name",
            "tool_calls",
            "tool_call_id",
        }

        for message in messages:
            if not isinstance(message, dict):
                continue

            clean_message = {
                key: value
                for key, value in message.items()
                if key in allowed_keys
            }

            # Role is required for chat messages.
            if not clean_message.get("role"):
                continue

            # Avoid accidentally sending None as content.
            if "content" not in clean_message:
                clean_message["content"] = ""

            cleaned.append(clean_message)

        return cleaned

    # ---------------------------------------------------------
    # Provider construction
    # ---------------------------------------------------------

    def _build_providers(self):
        providers = []

        # -----------------------------------------------------
        # Groq
        # -----------------------------------------------------
        if os.getenv("GROQ_API_KEY"):
            providers.append(
                self._create_provider(
                    name="groq",
                    api_key=os.getenv("GROQ_API_KEY"),
                    base_url="https://api.groq.com/openai/v1",
                    model=os.getenv(
                        "GROQ_MODEL",
                        "openai/gpt-oss-120b"
                    )
                )
            )

        # -----------------------------------------------------
        # Gemini
        # -----------------------------------------------------
        if os.getenv("GEMINI_API_KEY"):
            providers.append(
                self._create_provider(
                    name="gemini",
                    api_key=os.getenv("GEMINI_API_KEY"),
                    base_url=(
                        "https://generativelanguage.googleapis.com/"
                        "v1beta/openai/"
                    ),
                    model=os.getenv(
                        "GEMINI_MODEL",
                        "gemini-3.8-flash"
                    )
                )
            )

        # -----------------------------------------------------
        # Cloudflare Workers AI
        # -----------------------------------------------------
        if (
            os.getenv("CLOUDFLARE_API_KEY")
            and os.getenv("CLOUDFLARE_ACCOUNT_ID")
        ):
            account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")

            providers.append(
                self._create_provider(
                    name="cloudflare",
                    api_key=os.getenv("CLOUDFLARE_API_KEY"),
                    base_url=(
                        f"https://api.cloudflare.com/client/v4/"
                        f"accounts/{account_id}/ai/v1"
                    ),
                    model=os.getenv(
                        "CLOUDFLARE_MODEL",
                        "@cf/zai-org/glm-4.7-flash"
                    )
                )
            )

        # -----------------------------------------------------
        # Mistral
        # -----------------------------------------------------
        if os.getenv("MISTRAL_API_KEY"):
            providers.append(
                self._create_provider(
                    name="mistral",
                    api_key=os.getenv("MISTRAL_API_KEY"),
                    base_url="https://api.mistral.ai/v1",
                    model=os.getenv(
                        "MISTRAL_MODEL",
                        "mistral-small-latest"
                    )
                )
            )

        # -----------------------------------------------------
        # Hugging Face
        # -----------------------------------------------------
        if os.getenv("HUGGINGFACE_API_KEY"):
            providers.append(
                self._create_provider(
                    name="huggingface",
                    api_key=os.getenv("HUGGINGFACE_API_KEY"),
                    base_url="https://router.huggingface.co/v1",
                    model=os.getenv(
                        "HUGGINGFACE_MODEL",
                        "openai/gpt-oss-20b"
                    )
                )
            )

        # -----------------------------------------------------
        # OpenRouter
        # -----------------------------------------------------
        if os.getenv("OPENROUTER_API_KEY"):
            providers.append(
                self._create_provider(
                    name="openrouter",
                    api_key=os.getenv("OPENROUTER_API_KEY"),
                    base_url="https://openrouter.ai/api/v1",
                    model=os.getenv(
                        "OPENROUTER_MODEL",
                        "openrouter/free"
                    ),
                    headers={
                        "X-OpenRouter-Title": "Noel AI Mind"
                    }
                )
            )

        return providers

    def _create_provider(
        self,
        name: str,
        api_key: str,
        base_url: str,
        model: str,
        headers: dict[str, str] | None = None
    ):
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=headers or {}
        )

        return {
            "client": client,
            "state": ProviderState(
                name=name,
                model=model
            )
        }

    # ---------------------------------------------------------
    # Provider state
    # ---------------------------------------------------------

    def _is_available(self, state: ProviderState) -> bool:
        return time.time() >= state.cooldown_until

    def _mark_failure(
        self,
        state: ProviderState,
        error: Exception
    ):
        with self.lock:
            state.failures += 1
            state.available = False
            state.last_error = str(error)

            state.cooldown_until = (
                time.time() + self.COOLDOWN_SECONDS
            )

    def _mark_success(
        self,
        state: ProviderState,
        latency_ms: float
    ):
        with self.lock:
            state.failures = 0
            state.available = True
            state.cooldown_until = 0
            state.last_error = None
            state.last_latency_ms = latency_ms

    # ---------------------------------------------------------
    # Chat
    # ---------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None
    ):
        """
        Send a chat request through the provider pool.

        Internal message metadata is stripped before the request
        is sent to any external provider.

        Returns:

        {
            "content": "...",
            "provider": "...",
            "model": "...",
            "latency_ms": ...,
            "fallback_used": bool,
            "attempts": int
        }
        """

        if not self.providers:
            raise RuntimeError(
                "No AI providers configured. "
                "Add at least one provider API key to .env."
            )

        # IMPORTANT:
        # AI Mind stores internal metadata on messages for its own
        # database/UI. Providers such as Groq, Hugging Face and
        # Mistral reject that property. Sanitize once at the router
        # boundary so every provider gets the same clean payload.
        api_messages = self.sanitize_messages(messages)

        if not api_messages:
            raise RuntimeError(
                "No valid messages were supplied to the AI router."
            )

        max_tokens = (
            max_tokens
            if max_tokens is not None
            else self.max_tokens
        )

        attempts = 0
        fallback_used = False
        errors = []

        for provider in self.providers:
            client = provider["client"]
            state = provider["state"]

            # Skip providers currently in cooldown.
            if not self._is_available(state):
                continue

            # ------------------------------------------------
            # Test failure simulation
            # ------------------------------------------------
            if state.name.lower() in self.test_fail_providers:
                print(
                    f"[AI Router] TEST: Simulating failure "
                    f"for {state.name}"
                )

                error = RuntimeError(
                    f"Simulated failure for provider: {state.name}"
                )

                self._mark_failure(state, error)

                errors.append(
                    f"{state.name}: {error}"
                )

                attempts += 1
                fallback_used = True
                continue

            attempts += 1
            start = time.perf_counter()

            try:
                response = client.chat.completions.create(
                    model=state.model,
                    messages=api_messages,
                    max_tokens=max_tokens
                )

                latency_ms = round(
                    (time.perf_counter() - start) * 1000,
                    2
                )

                content = response.choices[0].message.content

                if not content:
                    raise RuntimeError(
                        f"{state.name} returned an empty response"
                    )

                self._mark_success(
                    state,
                    latency_ms
                )

                return {
                    "content": content,
                    "provider": state.name,
                    "model": state.model,
                    "latency_ms": latency_ms,
                    "fallback_used": fallback_used,
                    "attempts": attempts
                }

            except Exception as error:
                latency_ms = round(
                    (time.perf_counter() - start) * 1000,
                    2
                )

                state.last_latency_ms = latency_ms

                self._mark_failure(
                    state,
                    error
                )

                errors.append(
                    f"{state.name}: {error}"
                )

                fallback_used = True

                print(
                    f"[AI Router] {state.name} failed "
                    f"after {latency_ms}ms: {error}"
                )

        raise RuntimeError(
            "All AI providers failed.\n" +
            "\n".join(errors)
        )

    # ---------------------------------------------------------
    # Status
    # ---------------------------------------------------------

    def get_status(self):
        result = []

        for provider in self.providers:
            state = provider["state"]

            available = self._is_available(state)

            result.append({
                "provider": state.name,
                "model": state.model,
                "available": available,
                "failures": state.failures,
                "cooldown_until": (
                    state.cooldown_until
                    if state.cooldown_until > time.time()
                    else None
                ),
                "last_latency_ms": state.last_latency_ms,
                "last_error": state.last_error
            })

        return result


# Global router instance
ai_router = AIRouter()
