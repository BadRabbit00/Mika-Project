"""Async llama-server transport with optional durable call receipts."""

import asyncio
import math
import time
from collections import OrderedDict
from uuid import uuid4

import httpx
import numpy as np
import structlog

from src.core.context import ContextBuilder, Request
from src.core.context import ContextOverflow as ContextOverflow
from src.core.reasoning import END, START, ReasoningLimits, context_capacity
from src.core.runs import RunRecorder
from src.core.vectors import validate_vector

log = structlog.get_logger("blogai.llm_local")


class LocalLLM:
    def __init__(
        self,
        generation_url: str = "http://127.0.0.1:8080",
        embedding_url: str = "http://127.0.0.1:8081",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 300.0,
        token_cache_size: int = 256,
        database=None,
        settings=None,
    ):
        if token_cache_size < 0:
            raise ValueError("Token cache size cannot be negative")
        self.generation = httpx.AsyncClient(
            base_url=generation_url,
            transport=transport,
            timeout=timeout,
            trust_env=False,
        )
        self.embeddings = httpx.AsyncClient(
            base_url=embedding_url,
            transport=transport,
            timeout=timeout,
            trust_env=False,
        )
        self._tokens: OrderedDict[tuple[str, bool, bool], tuple[int, ...]] = (
            OrderedDict()
        )
        self._cache_size = token_cache_size
        self._embedding_model: str | None = None
        self.recorder = RunRecorder(database) if database is not None else None
        self.settings = settings

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self) -> None:
        await self.generation.aclose()
        await self.embeddings.aclose()

    async def _request(self, client, method, path, **kwargs):
        if self.settings is not None:
            kwargs.setdefault("timeout", self.settings.get("system.llm_timeout_sec"))
        for attempt in range(4):
            try:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
                return response.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                retryable = not isinstance(
                    error, httpx.HTTPStatusError
                ) or error.response.status_code in {429, 500, 502, 503, 504}
                if not retryable or attempt == 3:
                    log.exception(
                        "local_request_failed", endpoint=str(client.base_url.join(path))
                    )
                    raise
                log.warning("local_request_retry", path=path, attempt=attempt + 1)
                await asyncio.sleep(2**attempt)
        raise AssertionError("Unreachable retry state")

    async def tokenize(
        self, text: str, *, parse_special: bool = False, add_special: bool = False
    ) -> list[int]:
        key = (text, parse_special, add_special)
        if key in self._tokens:
            self._tokens.move_to_end(key)
            return list(self._tokens[key])
        response = await self._request(
            self.generation,
            "POST",
            "/tokenize",
            json={
                "content": text,
                "add_special": add_special,
                "parse_special": parse_special,
            },
        )
        tokens = response.get("tokens")
        if not isinstance(tokens, list) or any(
            type(token) is not int or token < 0 for token in tokens
        ):
            raise ValueError("Invalid tokenizer response")
        if self._cache_size:
            self._tokens[key] = tuple(tokens)
            if len(self._tokens) > self._cache_size:
                self._tokens.popitem(last=False)
        return list(tokens)

    async def detokenize(self, tokens: list[int]) -> str:
        response = await self._request(
            self.generation, "POST", "/detokenize", json={"tokens": tokens}
        )
        content = response.get("content")
        if not isinstance(content, str):
            raise ValueError("Invalid detokenizer response")
        return content

    def _reasoning_enabled(self):
        enabled = self.settings.get("llm.reasoning_enabled") if self.settings else False
        if type(enabled) is not bool:
            raise ValueError("Reasoning enablement must be a boolean")
        return enabled

    async def prompt_tokens(self, request: Request) -> list[int]:
        return await self._prompt_tokens(request, reasoning=self._reasoning_enabled())

    async def _prompt_tokens(self, request, *, reasoning):
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.user})
        rendered = await self._request(
            self.generation,
            "POST",
            "/apply-template",
            json={
                "messages": messages,
                "add_generation_prompt": True,
                "chat_template_kwargs": {"enable_thinking": reasoning},
            },
        )
        prompt = rendered.get("prompt")
        if not isinstance(prompt, str):
            raise ValueError("Invalid chat-template response")
        if reasoning:
            # Prime the budget sampler in its counting state, before generation.
            # A trailing newline would consume the forced delimiter at budget zero.
            prompt += START
        # /apply-template omits BOS; token-ID completion skips server tokenization.
        tokens = await self.tokenize(prompt, parse_special=True, add_special=True)
        return tokens

    async def generate(
        self, request: Request, *, grammar: str | None = None, max_tokens: int = 2048
    ) -> str:
        from src.core.admission import check_study

        check_study()
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError("A positive generation limit is required")
        reasoning = self._reasoning_enabled()
        limits = None
        deadline = None
        if reasoning:
            properties = await self._request(self.generation, "GET", "/props")
            capacity = context_capacity(properties)
            budget = self.settings.get("llm.reasoning_budget_tokens")
            deadline = self.settings.get("llm.reasoning_timeout_sec")
            if (
                type(deadline) not in (int, float)
                or not math.isfinite(deadline)
                or deadline <= 0
            ):
                raise ValueError("Reasoning timeout must be finite and positive")
        tokens = await self._prompt_tokens(request, reasoning=reasoning)
        ContextBuilder._enforce(request, tokens)
        payload = {
            "prompt": tokens,
            "n_predict": max_tokens,
            "temperature": request.temperature,
            "stream": False,
            "cache_prompt": False,
        }
        if reasoning:
            limits = ReasoningLimits.create(
                context=capacity,
                prompt=len(tokens),
                requested=budget,
                answer=max_tokens,
                end_tokens=await self.tokenize(END, parse_special=True),
            )
            payload.update(limits.parameters(grammar))
            if limits.thinking < budget:
                log.warning(
                    "reasoning_budget_reduced",
                    requested=budget,
                    effective=limits.thinking,
                    context_tokens=capacity,
                    prompt_tokens=len(tokens),
                    answer_tokens=max_tokens,
                )
        elif grammar is not None:
            payload["grammar"] = grammar
        if request.mode is not None and not reasoning:
            payload["stop"] = [f"</{request.mode}>"]
        call_id = uuid4().hex
        started = time.monotonic()
        log.info(
            "local_generation_started",
            call_id=call_id,
            profile=request.profile,
            system=request.system,
            user=request.user,
            params=payload,
            tokens_in=len(tokens),
        )
        if self.recorder:
            await self.recorder.begin(
                call_id=call_id,
                trace_id=structlog.contextvars.get_contextvars().get("trace_id")
                or call_id,
                actor="student",
                request=request,
                params=payload,
                tokens_in=len(tokens),
            )
        content = thought = tokens_out = None
        thinking_tokens = answer_tokens = 0
        response = {}
        try:
            check_study()
            async with asyncio.timeout(deadline):
                kwargs = {"timeout": deadline} if deadline is not None else {}
                response = await self._request(
                    self.generation, "POST", "/completion", json=payload, **kwargs
                )
            raw = response.get("content")
            if not isinstance(raw, str):
                raise ValueError("Invalid completion response")
            if (
                response.get("stopped_limit")
                or response.get("stop_type") == "limit"
                or response.get("truncated")
            ):
                raise ValueError(
                    "Generation or input was truncated; no output may be stored"
                )
            if limits is not None:
                # Native completion metadata omits n_ctx; query /props again.
                properties = await self._request(self.generation, "GET", "/props")
                if context_capacity(properties) != limits.context:
                    raise ContextOverflow(
                        "The server context changed during generation"
                    )
                thought, content, thinking_tokens, tokens_out = limits.separate(
                    response
                )
                answer_tokens = len(await self.tokenize(content))
                if answer_tokens > max_tokens:
                    raise ValueError("Final answer exceeds its token budget")
            else:
                content = raw
                thought = response.get("reasoning_content")
                if START in content or END in content:
                    raise ValueError("Unexpected reasoning channel in final output")
                if request.mode is not None:
                    closing = f"</{request.mode}>"
                    if (
                        response.get("stop_type") == "word"
                        and response.get("stopping_word") == closing
                        and content.lstrip().startswith(f"<{request.mode}>")
                        and closing not in content
                    ):
                        content += closing
                        log.info(
                            "output_stop_restored", call_id=call_id, mode=request.mode
                        )
                tokens_out = answer_tokens = len(await self.tokenize(content))
        except BaseException as error:
            message = str(error) or type(error).__name__
            if self.recorder:
                await self.recorder.finish(
                    call_id,
                    output=content,
                    thought=thought,
                    error=message,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
            log.error("local_generation_failed", call_id=call_id, error=message)
            raise
        if self.recorder:
            await self.recorder.finish(
                call_id,
                output=content,
                thought=thought,
                model=response.get("model"),
                tokens_out=tokens_out,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        log.info(
            "local_generation_finished",
            call_id=call_id,
            profile=request.profile,
            output=content,
            thought=thought,
            response={
                key: value
                for key, value in response.items()
                if key
                in {
                    "model",
                    "id_slot",
                    "index",
                    "stop",
                    "stop_type",
                    "stopping_word",
                    "truncated",
                    "tokens_predicted",
                    "tokens_evaluated",
                    "tokens_cached",
                    "timings",
                }
            },
            tokens_out=tokens_out,
            reasoning_tokens=thinking_tokens,
            answer_tokens=answer_tokens,
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return content

    async def embedding_model(self) -> str:
        if self._embedding_model is None:
            response = await self._request(self.embeddings, "GET", "/v1/models")
            models = response.get("data", [])
            if len(models) != 1 or not isinstance(models[0].get("id"), str):
                raise ValueError("Expected one named embedding model")
            self._embedding_model = models[0]["id"]
        return self._embedding_model

    async def embed(self, text: str) -> np.ndarray:
        response = await self._request(
            self.embeddings,
            "POST",
            "/v1/embeddings",
            json={"input": text, "encoding_format": "float"},
        )
        data = response.get("data", [])
        if len(data) != 1:
            raise ValueError("Expected one embedding")
        return validate_vector(data[0].get("embedding"))
