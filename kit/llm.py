"""Chat completions on Groq with automatic fallback between models."""
import logging

from groq import (APIConnectionError, APIStatusError, APITimeoutError, AsyncGroq, AuthenticationError,
                  BadRequestError, NotFoundError, RateLimitError)

from . import config

log = logging.getLogger("kit.llm")
_client: AsyncGroq | None = None
_last_good: dict = {"model": None}


class LLMUnavailable(Exception):
    pass


def _groq() -> AsyncGroq:
    global _client
    if _client is None:
        _client = AsyncGroq(api_key=config.GROQ_API_KEY, timeout=25, max_retries=0)
    return _client


def _order() -> list[str]:
    models = [_last_good["model"], *config.CHAT_MODELS]
    return list(dict.fromkeys(m for m in models if m))


async def complete(messages: list[dict], tools: list[dict] | None):
    """Returns the assistant message. Tries each model; retries malformed tool calls once."""
    use_tools = bool(tools)
    for model in _order():
        for attempt in range(2):
            kwargs = dict(model=model, messages=messages, temperature=0.2, max_tokens=900)
            if use_tools:
                kwargs.update(tools=tools, tool_choice="auto")
            if "gpt-oss" in model:
                kwargs["extra_body"] = {"reasoning_effort": "low"}     # faster replies
            try:
                resp = await _groq().chat.completions.create(**kwargs)
                _last_good["model"] = model
                return resp.choices[0].message
            except AuthenticationError as exc:
                raise LLMUnavailable("The Groq API key is missing or invalid.") from exc
            except BadRequestError as exc:
                text = str(exc)
                if "tool_use_failed" in text or "failed_generation" in text:
                    if attempt == 0:
                        continue                      # the model wrote a broken tool call; try again
                    break
                if use_tools:
                    log.warning("tool schema rejected (%s); answering without tools", text[:200])
                    use_tools = False
                    continue
                break
            except (NotFoundError, RateLimitError, APIConnectionError, APITimeoutError) as exc:
                log.warning("model %s unavailable: %s", model, type(exc).__name__)
                break
            except APIStatusError as exc:
                log.warning("model %s status %s", model, exc.status_code)
                break
    raise LLMUnavailable("No chat model is available right now.")
