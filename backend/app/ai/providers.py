"""Provider boundary accepts data and returns data; it has no operational tools."""

import json
import os
from typing import Protocol
import aiohttp
from app.ai.prompts import Prompt
from app.workers.reports import PermanentJobError


class LLMProvider(Protocol):
    name: str
    model: str

    async def generate(self, prompt: Prompt, context: dict) -> dict: ...


class TransientLLMError(ConnectionError):
    """Retryable provider failure, with no response body or credentials attached."""


class FakeLLMProvider:
    name = "fake"
    model = "deterministic-v1"

    async def generate(self, prompt, context):
        if prompt.version == "incident_summary_v1":
            incident = context["incident"]
            return {
                "summary": f"Demo summary: {incident['id']} — {incident['title']}. {len(context['dispatches'])} recorded dispatch(es).",
                "key_events": [
                    f"{item['timestamp']}: {item['event_type']}"
                    for item in context["activity"][-50:]
                ],
                "current_status": incident["status"],
                "open_items": (
                    []
                    if incident["status"] == "resolved"
                    else ["Incident remains unresolved in the supplied snapshot."]
                ),
            }
        incidents, units = context["active_incidents"], context["units"]
        return {
            "overview": f"Demo handover: {len(incidents)} active incidents, {len(context['dispatches'])} current dispatches, {len(units)} units. Activity covers the latest 24 hours (maximum 200 records); each output list shows at most 50 items.",
            "active_incidents": [
                f"{i['id']}: {i['title']} ({i['status']})" for i in incidents[:50]
            ],
            "units_in_service": [f"{u['id']}: {u['status']}" for u in units[:50]],
            "priority_items": [
                f"Unresolved {i['severity']} incident: {i['id']}"
                for i in incidents
                if i["severity"] in {"high", "critical"}
            ][:50],
        }


class OpenAIProvider:
    name = "openai"

    def __init__(self):
        self.model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        self._key = os.environ.get("OPENAI_API_KEY", "")
        try:
            self.timeout = float(os.environ.get("LLM_TIMEOUT_SECONDS", "30"))
            if (
                not 1 <= self.timeout <= 120
                or not self._key.strip()
                or not self.model.strip()
            ):
                raise ValueError()
        except ValueError:
            raise PermanentJobError(
                "LLM_CONFIGURATION", "The AI provider is not configured correctly."
            ) from None

    async def generate(self, prompt, context):
        payload = {
            "model": self.model,
            "store": False,
            "max_output_tokens": 1500,
            "instructions": prompt.instructions,
            "input": [
                {
                    "role": "user",
                    "content": json.dumps(
                        {"untrusted_operational_data": context}, ensure_ascii=False
                    ),
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": prompt.version,
                    "strict": True,
                    "schema": prompt.schema.model_json_schema(),
                }
            },
        }
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout), trust_env=False
            ) as client:
                async with client.post(
                    "https://api.openai.com/v1/responses",
                    json=payload,
                    headers={"Authorization": "Bearer " + self._key},
                    allow_redirects=False,
                ) as response:
                    if (
                        response.status == 429
                        or 500 <= response.status < 600
                        or response.status == 408
                    ):
                        raise TransientLLMError(
                            "The AI provider is temporarily unavailable."
                        )
                    if response.status != 200:
                        raise PermanentJobError(
                            "LLM_REJECTED",
                            "The AI provider rejected the request; check configuration and credentials.",
                        )
                    raw = await response.content.read(1_000_001)
                    if len(raw) > 1_000_000:
                        raise PermanentJobError(
                            "LLM_INVALID_OUTPUT",
                            "The AI response exceeded the size limit.",
                        )
                    # read() may return a partial chunk. Accumulate under the same cap.
                    while not response.content.at_eof():
                        raw += await response.content.read(1_000_001 - len(raw))
                        if len(raw) > 1_000_000:
                            raise PermanentJobError(
                                "LLM_INVALID_OUTPUT",
                                "The AI response exceeded the size limit.",
                            )
                    data = json.loads(raw)
                    if data.get("status") != "completed":
                        raise PermanentJobError(
                            "LLM_INCOMPLETE",
                            "The AI provider did not produce a complete summary.",
                        )
                    parts = [
                        part
                        for item in data.get("output", [])
                        if item.get("type") == "message"
                        for part in item.get("content", [])
                    ]
                    if any(part.get("type") == "refusal" for part in parts):
                        raise PermanentJobError(
                            "LLM_REFUSED",
                            "The AI provider declined to summarize this input.",
                        )
                    return json.loads(
                        "".join(
                            part["text"]
                            for part in parts
                            if part.get("type") == "output_text"
                        )
                    )
        except (
            TimeoutError,
            aiohttp.ClientConnectionError,
            aiohttp.ClientPayloadError,
        ):
            raise TransientLLMError(
                "The AI provider connection failed or timed out."
            ) from None
        except (ValueError, KeyError, TypeError):
            raise PermanentJobError(
                "LLM_INVALID_OUTPUT", "The AI provider returned an invalid response."
            ) from None


def provider_from_config() -> LLMProvider:
    name = os.environ.get("LLM_PROVIDER", "fake")
    if name == "fake":
        return FakeLLMProvider()
    if name == "openai":
        return OpenAIProvider()
    raise PermanentJobError(
        "LLM_CONFIGURATION", "The selected AI provider is unsupported."
    )
