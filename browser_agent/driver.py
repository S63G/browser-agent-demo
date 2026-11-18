"""Executes flow steps: scripted selector first, then a verified cached selector, then the model fallback.

A click that was performed is never repeated through a fallback tier: only "element not found" falls through.
A navigation timeout after a click is raised to the caller, because the click may already have taken effect.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar

from playwright.async_api import Download, Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from .errors import NavigationTimeout, NeedsHuman
from .navigator import Navigator
from .snapshot import describe, page_errors, page_text, selector_of, take_snapshot
from .steps import Op, Step, resolve_option, variant_name
from .trace import RunRecorder

Expect = Literal["none", "navigation", "download"]
T = TypeVar("T")


class ScriptedMiss(Exception):
    """A scripted selector did not match: the layout probably changed."""


@dataclass(frozen=True)
class StepOutcome:
    performed: bool
    download: Download | None = None


class SelectorCache:
    """Selectors the model found, keyed by step, reused only if the element still looks the same (tag/type/label)."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, dict] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def put(self, key: str, selector: str, signature: dict[str, str], variant: str) -> None:
        self._data[key] = {"selector": selector, "signature": signature, "variant": variant}
        self._save()

    def drop(self, key: str) -> None:
        if self._data.pop(key, None) is not None:
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")


PlannedAction = tuple[Locator, Op, str]


class Driver:
    def __init__(self, page: Page, recorder: RunRecorder, cache: SelectorCache, navigator: Navigator | None,
                 selector_timeout_ms: int, navigation_timeout_ms: int):
        self.page = page
        self.recorder = recorder
        self.cache = cache
        self.navigator = navigator
        self.selector_timeout_ms = selector_timeout_ms
        self.navigation_timeout_ms = navigation_timeout_ms
        self._layout_changed = False  # after the first scripted miss, stop waiting the full timeout on every step

    @property
    def _scripted_wait_ms(self) -> int:
        return min(200, self.selector_timeout_ms) if self._layout_changed else self.selector_timeout_ms

    def _miss(self, step_key: str, selector: str, **extra: object) -> None:
        self._layout_changed = True
        self.recorder.event("scripted_miss", step=step_key, selector=selector, **extra)

    # ------------------------------------------------------------------ navigation and reading

    async def goto(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded", timeout=self.navigation_timeout_ms)
        self.recorder.event("goto", url=url)

    async def errors(self) -> list[str]:
        return await page_errors(self.page)

    async def need(self, selector: str) -> Locator:
        """Scripted readers call this: the element or :class:`ScriptedMiss`."""
        locator = self.page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=self._scripted_wait_ms)
        except PlaywrightTimeout as exc:
            raise ScriptedMiss(selector) from exc
        return locator

    async def read(self, key: str, scripted: Callable[[], Awaitable[T]],
                   fallback: Callable[[Navigator, str], Awaitable[T]]) -> T:
        try:
            result = await scripted()
        except ScriptedMiss as exc:
            self._miss(key, str(exc))
        else:
            self.recorder.scripted_steps += 1
            self.recorder.event("read", step=key, via="scripted")
            return result
        if self.navigator is None:
            raise NeedsHuman(f"读取页面失败（{key}）：脚本选择器失效，且未启用模型兜底")
        await self.recorder.screenshot(self.page, f"fallback-{key}")
        result = await fallback(self.navigator, await page_text(self.page))
        self.recorder.model_steps += 1
        self.recorder.event("read", step=key, via="model")
        return result

    # ------------------------------------------------------------------ actions

    async def perform(self, step: Step, expect: Expect = "none", timeout_ms: int | None = None) -> StepOutcome:
        for tier in (self._scripted, self._cached, self._model):
            outcome = await tier(step, expect, timeout_ms)
            if outcome is not None:
                return outcome
        raise AssertionError("the model tier always returns or raises")

    async def _scripted(self, step: Step, expect: Expect, timeout_ms: int | None) -> StepOutcome | None:
        locator = self.page.locator(step.selector)
        if step.optional and step.absent_marker and await self.page.locator(step.absent_marker).count() \
                and not await locator.count():
            self._log(step, "scripted", performed=False)
            return StepOutcome(False)
        try:
            await locator.first.wait_for(state="visible", timeout=self._scripted_wait_ms)
        except PlaywrightTimeout:
            self._miss(step.key, step.selector)
            return None
        planned = await self._plan(locator.first, step, step.value)
        if planned is None:
            self._miss(step.key, step.selector, detail="no matching option")
            return None
        download = await self._run([planned], expect, timeout_ms)
        self.recorder.scripted_steps += 1
        self._log(step, "scripted")
        return StepOutcome(True, download)

    async def _cached(self, step: Step, expect: Expect, timeout_ms: int | None) -> StepOutcome | None:
        entry = self.cache.get(step.key) if step.cacheable else None
        if entry is None:
            return None
        locator = self.page.locator(entry["selector"])
        if await locator.count() != 1 or not await locator.is_visible():
            return None
        if await describe(locator) != entry["signature"]:
            self.cache.drop(step.key)
            self.recorder.event("cache_stale", step=step.key, selector=entry["selector"])
            return None
        value = step.value if step.secret else step.variants().get(entry["variant"], step.value)
        planned = await self._plan(locator, step, value)
        if planned is None:
            return None
        download = await self._run([planned], expect, timeout_ms)
        self.recorder.cache_steps += 1
        self._log(step, "cache", selector=entry["selector"])
        return StepOutcome(True, download)

    async def _model(self, step: Step, expect: Expect, timeout_ms: int | None) -> StepOutcome:
        if self.navigator is None:
            raise NeedsHuman(f"脚本选择器失效（{step.selector}），且未启用模型兜底：{step.intent}")
        snap = await take_snapshot(self.page)
        await self.recorder.screenshot(self.page, f"fallback-{step.key}")
        answer = await self.navigator.locate(step, snap)
        if not answer.actions:
            if step.optional:
                self._log(step, "model", performed=False)
                return StepOutcome(False)
            raise NeedsHuman(f"页面上找不到可完成此步骤的元素：{step.intent}（{answer.reason}）")
        planned: list[PlannedAction] = []
        for action in answer.actions:
            locator = self.page.locator(f'[data-agent-ref="{action.ref}"]')
            value = step.value if step.secret else action.value
            plan = await self._plan(locator, step, value)
            if plan is None:
                raise NeedsHuman(f"模型选择的元素无法执行该操作：{step.intent}")
            planned.append(plan)
        cache_entry = None
        if step.cacheable and len(planned) == 1:
            variant = "value" if step.op == "click" else variant_name(answer.actions[0].value, step.variants())
            cache_entry = (await selector_of(planned[0][0]), await describe(planned[0][0]), variant or "value")
        download = await self._run(planned, expect, timeout_ms)
        if cache_entry:
            self.cache.put(step.key, *cache_entry)
        self.recorder.model_steps += 1
        self._log(step, "model", refs=[a.ref for a in answer.actions])
        return StepOutcome(True, download)

    async def _plan(self, locator: Locator, step: Step, value: str) -> PlannedAction | None:
        """Turn a step into a concrete action on ``locator``: type ``value``, or for a drop-down pick the option that
        ``value`` names (option value/text or one of the step's values) and that denotes the step's value."""
        if step.op == "click":
            return locator, "click", ""
        if await locator.evaluate("el => el.tagName.toLowerCase()") != "select":
            return locator, "fill", value
        options: list[list[str]] = await locator.evaluate("el => Array.from(el.options).map(o => [o.value, o.text])")
        chosen = resolve_option([(v, t) for v, t in options], value, step.variants())
        return (locator, "select", chosen) if chosen is not None else None

    async def _run(self, planned: list[PlannedAction], expect: Expect, timeout_ms: int | None) -> Download | None:
        timeout = timeout_ms or self.navigation_timeout_ms

        async def act() -> None:
            for locator, op, value in planned:
                if op == "click":
                    await locator.click(timeout=self.navigation_timeout_ms)
                elif op == "select":
                    await locator.select_option(value=value)
                else:
                    await locator.fill(value)

        if expect == "navigation":
            try:
                async with self.page.expect_navigation(wait_until="domcontentloaded", timeout=timeout):
                    await act()
            except PlaywrightTimeout as exc:
                raise NavigationTimeout(f"点击后 {timeout} ms 内页面未完成跳转") from exc
            return None
        if expect == "download":
            async with self.page.expect_download(timeout=timeout) as info:
                await act()
            return await info.value
        await act()
        return None

    def _log(self, step: Step, via: str, performed: bool = True, **extra: object) -> None:
        value = "***" if step.secret else step.value
        self.recorder.event("step", step=step.key, via=via, performed=performed, op=step.op, value=value, **extra)
