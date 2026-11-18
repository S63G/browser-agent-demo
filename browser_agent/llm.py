"""OpenAI-compatible chat client (stdlib urllib) with record/replay, as in Demo A.

Replay mode only serves responses recorded from real model runs (``LLM_RECORD=1``); it never invents one,
so the evaluation can be reproduced offline at zero cost.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .config import Settings

Message = dict[str, object]
M = TypeVar("M", bound=BaseModel)


class LLMError(RuntimeError):
    """No usable answer: transport error, replay miss, or invalid output twice."""


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, usage: dict) -> None:
        self.calls += 1
        self.prompt_tokens += int(usage.get("prompt_tokens", 0))
        self.completion_tokens += int(usage.get("completion_tokens", 0))

    def as_dict(self) -> dict:
        return asdict(self)


class ChatClient(Protocol):
    usage: Usage

    def complete(self, messages: list[Message], *, model: str) -> str: ...


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 90.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.usage = Usage()
        self.last_usage: dict = {}

    def complete(self, messages: list[Message], *, model: str) -> str:
        body = {"model": model, "messages": messages, "temperature": 0, "response_format": {"type": "json_object"}}
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            with exc:
                detail = exc.read().decode("utf-8", "replace")[:300]
            raise LLMError(f"HTTP {exc.code} from model provider: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise LLMError(f"cannot reach model provider: {exc}") from exc
        self.last_usage = payload.get("usage") or {}
        self.usage.add(self.last_usage)
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {str(payload)[:300]}") from exc


def request_key(messages: list[Message], model: str) -> str:
    blob = json.dumps({"model": model, "messages": messages}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class RecordingClient:
    """Wraps a live client and stores every call (one file per distinct request, repeated requests appended)."""

    def __init__(self, inner: OpenAICompatibleClient, folder: Path, provider: str):
        self.inner, self.folder, self.provider = inner, folder, provider
        self._lock = threading.Lock()

    @property
    def usage(self) -> Usage:
        return self.inner.usage

    def complete(self, messages: list[Message], *, model: str) -> str:
        content = self.inner.complete(messages, model=model)
        key = request_key(messages, model)
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self.folder / f"{key}.json"
            record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
                "source": "live", "provider": self.provider, "model": model, "request_sha256": key, "calls": []}
            record["calls"].append({"recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                    "usage": self.inner.last_usage, "response": content})
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        return content


class ReplayClient:
    """Serves calls recorded by :class:`RecordingClient`, in recorded order; never invents one."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.usage = Usage()  # usage of the original live calls being replayed (no new cost)
        self._served: dict[str, int] = {}

    def complete(self, messages: list[Message], *, model: str) -> str:
        key = request_key(messages, model)
        path = self.folder / f"{key}.json"
        if not path.exists():
            raise LLMError("replay miss: no recorded model response for this request")
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("source") != "live" or not record.get("calls"):
            raise LLMError(f"replay record {path.name} was not produced by a live model run")
        index = self._served.get(key, 0)
        self._served[key] = index + 1
        if index < len(record["calls"]):
            call = record["calls"][index]
            self.usage.add(call.get("usage") or {})
        else:  # asked more often than the live run did: repeat the last answer, at no recorded cost
            call = record["calls"][-1]
        return call["response"]


class ModelGateway:
    """Asks for JSON, validates it with Pydantic plus an optional domain check, retries once with the error."""

    def __init__(self, client: ChatClient, model: str, label: str):
        self.client = client
        self.model = model
        self.label = label

    @property
    def usage(self) -> Usage:
        return self.client.usage

    def structured(self, messages: list[Message], schema: type[M],
                   check: Callable[[M], str | None] | None = None) -> M:
        content = self.client.complete(messages, model=self.model)
        error = _validate(content, schema, check)
        if isinstance(error, BaseModel):
            return error
        retry = messages + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": "上面的 JSON 不符合要求，请修正后只输出完整 JSON。问题：" + error},
        ]
        content = self.client.complete(retry, model=self.model)
        result = _validate(content, schema, check)
        if isinstance(result, BaseModel):
            return result
        raise LLMError(f"model output rejected twice: {result[:300]}")


def _validate(content: str, schema: type[M], check: Callable[[M], str | None] | None) -> M | str:
    try:
        parsed = schema.model_validate_json(_strip_fences(content))
    except ValidationError as exc:
        return exc.json(include_url=False)[:2000]
    problem = check(parsed) if check else None
    return problem if problem else parsed


def _strip_fences(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


def build_gateway(settings: Settings) -> ModelGateway | None:
    if settings.llm_mode == "off":
        return None
    client: ChatClient
    if settings.llm_mode == "replay":
        client = ReplayClient(settings.replay_dir)
    else:
        assert settings.llm_api_key and settings.llm_base_url
        live = OpenAICompatibleClient(settings.llm_base_url, settings.llm_api_key)
        client = RecordingClient(live, settings.replay_dir, settings.llm_provider) if settings.llm_record else live
    return ModelGateway(client, settings.llm_model or "", settings.mode_label)
