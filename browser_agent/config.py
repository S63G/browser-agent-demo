"""Runtime settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

PROJECT_ROOT = Path(__file__).resolve().parent.parent

LlmMode = Literal["off", "live", "replay"]

# OpenAI-compatible endpoints of the two supported domestic providers (same as Demo A).
PROVIDERS: dict[str, dict[str, str]] = {
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    out_dir: Path
    portal_url: str = "http://127.0.0.1:8770"
    portal_user: str = "op01"
    portal_password: str = "Ruifeng#2026"     # fictional account of the fake portal (data/portal_seed.json)
    headless: bool = True
    slow_mo_ms: int = 0
    selector_timeout_ms: int = 1500           # how long a scripted selector may take before we call it broken
    navigation_timeout_ms: int = 10000
    submit_timeout_ms: int = 5000             # waiting for the portal after the final 提交 click
    approval_timeout_s: float = 300.0
    approval_port: int = 8771
    approval_web: bool = True
    max_model_calls: int = 60                 # per tool call; above this the agent hands over to a human
    llm_mode: LlmMode = "off"
    llm_provider: str = "deepseek"
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_record: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        key = env.get("LLM_API_KEY") or None
        mode = env.get("LLM_MODE", "live" if key else "off").lower()
        if mode not in ("off", "live", "replay"):
            raise ValueError(f"LLM_MODE must be off, live or replay, not {mode!r}")
        if mode == "live" and not key:
            raise ValueError("LLM_MODE=live needs LLM_API_KEY")
        provider = env.get("LLM_PROVIDER", "deepseek").lower()
        if provider not in PROVIDERS:
            raise ValueError(f"LLM_PROVIDER must be one of {sorted(PROVIDERS)}, not {provider!r}")
        return cls(
            data_dir=Path(env.get("AGENT_DATA", PROJECT_ROOT / "data")),
            out_dir=Path(env.get("AGENT_OUT", PROJECT_ROOT / "out")),
            portal_url=env.get("PORTAL_URL", cls.portal_url).rstrip("/"),
            portal_user=env.get("PORTAL_USER", cls.portal_user),
            portal_password=env.get("PORTAL_PASSWORD", cls.portal_password),
            headless=env.get("BROWSER_HEADLESS", "1") != "0",
            slow_mo_ms=int(env.get("BROWSER_SLOW_MO", "0")),
            approval_timeout_s=float(env.get("APPROVAL_TIMEOUT", cls.approval_timeout_s)),
            approval_port=int(env.get("APPROVAL_PORT", cls.approval_port)),
            approval_web=env.get("APPROVAL_WEB", "1") != "0",
            max_model_calls=int(env.get("MAX_MODEL_CALLS", cls.max_model_calls)),
            llm_mode=mode,  # type: ignore[arg-type]
            llm_provider=provider,
            llm_api_key=key,
            llm_model=env.get("LLM_MODEL") or PROVIDERS[provider]["model"],
            llm_base_url=env.get("LLM_BASE_URL") or PROVIDERS[provider]["base_url"],
            llm_record=env.get("LLM_RECORD", "0") == "1",
        )

    def with_changes(self, **changes: object) -> "Settings":
        return replace(self, **changes)  # type: ignore[arg-type]

    @property
    def replay_dir(self) -> Path:
        return self.data_dir / "replay"

    @property
    def portal_db(self) -> Path:
        return self.out_dir / "portal.db"

    @property
    def agent_db(self) -> Path:
        """Idempotency ledger and approval requests."""
        return self.out_dir / "agent.db"

    @property
    def selector_cache(self) -> Path:
        return self.out_dir / "selector_cache.json"

    @property
    def runs_dir(self) -> Path:
        return self.out_dir / "runs"

    @property
    def downloads_dir(self) -> Path:
        return self.out_dir / "downloads"

    @property
    def mode_label(self) -> str:
        if self.llm_mode == "off":
            return "scripted"
        return f"scripted+model({self.llm_mode}:{self.llm_provider}/{self.llm_model})"
