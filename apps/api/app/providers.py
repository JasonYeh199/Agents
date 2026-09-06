import json
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from .config import Settings, get_settings

T = TypeVar("T", bound=BaseModel)


@dataclass
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    response_id: str | None = None
    reasoning_summaries: list[str] | None = None


@dataclass
class ProviderTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any | Awaitable[Any]]


class ModelProvider(ABC):
    name: str

    @abstractmethod
    async def generate_structured(
        self, instructions: str, payload: dict[str, Any], schema: type[T], emit: Callable[[str], Any | Awaitable[Any]] | None = None
    ) -> tuple[T, ProviderUsage]: ...

    @abstractmethod
    async def run_tools(
        self,
        instructions: str,
        payload: dict[str, Any],
        tools: list[ProviderTool],
        max_calls: int,
    ) -> tuple[str, ProviderUsage, list[dict[str, Any]]]: ...


class OpenAIProvider(ModelProvider):
    name = "openai"

    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for the OpenAI provider")
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.settings = settings

    async def generate_structured(self, instructions, payload, schema, emit=None):
        started = time.perf_counter()
        import inspect

        summaries: list[str] = []
        reasoning = {"effort": self.settings.reasoning_effort}
        summary_mode = getattr(self.settings, "reasoning_summary", "auto")
        if summary_mode != "none":
            reasoning["summary"] = summary_mode

        async def invoke(reasoning_config):
            async with self.client.responses.stream(
                model=self.settings.openai_model,
                instructions=instructions,
                input=json.dumps(payload, ensure_ascii=False),
                text_format=schema,
                reasoning=reasoning_config,
                max_output_tokens=self.settings.max_output_tokens,
            ) as stream:
                async for event in stream:
                    if event.type == "response.reasoning_summary_text.delta":
                        delta = getattr(event, "delta", "")
                        if delta:
                            summaries.append(delta)
                            if emit:
                                result = emit(delta)
                                if inspect.isawaitable(result):
                                    await result
                return await stream.get_final_response()

        try:
            response = await invoke(reasoning)
        except Exception as exc:
            # Some otherwise-compatible models reject the optional summary field.
            # Retry once without it, while preserving the requested reasoning effort.
            if "summary" not in reasoning or type(exc).__name__ != "BadRequestError":
                raise
            summaries.clear()
            response = await invoke({"effort": self.settings.reasoning_effort})
        usage = response.usage
        return response.output_parsed, ProviderUsage(
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            duration_ms=int((time.perf_counter() - started) * 1000),
            response_id=response.id,
            reasoning_summaries=["".join(summaries)] if summaries else [],
        )

    async def run_tools(self, instructions, payload, tools, max_calls):
        """Provider-neutral bounded tool loop; callers supply only allowlisted handlers."""
        import inspect

        started = time.perf_counter()
        definitions = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "strict": True,
            }
            for tool in tools
        ]
        handlers = {tool.name: tool.handler for tool in tools}
        tool_trace: list[dict[str, Any]] = []
        input_items: list[Any] = [{"role": "user", "content": json.dumps(payload)}]
        totals = ProviderUsage()
        response = None
        for _ in range(max_calls + 1):
            response = await self.client.responses.create(
                model=self.settings.openai_model,
                instructions=instructions,
                input=input_items,
                tools=definitions,
                reasoning={"effort": self.settings.reasoning_effort},
                max_output_tokens=self.settings.max_output_tokens,
            )
            totals.input_tokens += getattr(response.usage, "input_tokens", 0)
            totals.output_tokens += getattr(response.usage, "output_tokens", 0)
            calls = [item for item in response.output if item.type == "function_call"]
            input_items.extend(item.model_dump() for item in response.output)
            if not calls:
                totals.response_id = response.id
                totals.duration_ms = int((time.perf_counter() - started) * 1000)
                return response.output_text, totals, tool_trace
            if len(tool_trace) + len(calls) > max_calls:
                raise RuntimeError("provider tool-call budget exceeded")
            for call in calls:
                if call.name not in handlers:
                    raise RuntimeError(f"provider requested unknown tool: {call.name}")
                arguments = json.loads(call.arguments)
                result = handlers[call.name](**arguments)
                if inspect.isawaitable(result):
                    result = await result
                tool_trace.append({"name": call.name, "arguments": arguments, "result": result})
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )
        raise RuntimeError("provider tool loop ended without a final response")


def get_provider(settings: Settings | None = None, run_config: dict[str, Any] | None = None) -> ModelProvider | None:
    settings = settings or get_settings()
    run_config = run_config or {}
    budgets = run_config.get("budgets", {})
    provider = run_config.get("provider", settings.model_provider)
    settings = settings.model_copy(update={
        "model_provider": provider,
        "openai_model": run_config.get("model") or settings.openai_model,
        "reasoning_effort": run_config.get("reasoning_effort", settings.reasoning_effort),
        "max_output_tokens": budgets.get("max_output_tokens", run_config.get("max_output_tokens", settings.max_output_tokens)),
        "reasoning_summary": run_config.get("reasoning_summary", "auto"),
    })
    return OpenAIProvider(settings) if settings.model_provider == "openai" and settings.openai_api_key else None
