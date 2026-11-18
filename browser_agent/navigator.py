"""Model-guided fallback: pick the element for a step whose scripted selector broke, or read data off a page.

Constraints enforced in code (not just asked for in the prompt):
  * the model may only reference elements from the snapshot and only use the step's allowed operations;
  * typed / selected values must be one of the step's own values (secrets are filled in by the program);
  * click steps take exactly one action, value steps at most three (e.g. 年/月/日 drop-downs);
  * every value the model reads off a page must literally appear on that page (grounding check);
  * each tool call has a cap on model calls, after which the agent hands over to a human.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Literal, TypeVar

from pydantic import BaseModel, Field

from .errors import NeedsHuman
from .llm import LLMError, Message, ModelGateway
from .snapshot import Snapshot
from .steps import SECRET, Step, date_spellings, resolve_option, variant_name
from .trace import RunRecorder

M = TypeVar("M", bound=BaseModel)

LOCATE_SYSTEM = (
    "你是网页自动化的元素定位助手。程序在一个老旧的内部 Web 系统上执行固定流程，原先写好的选择器失效了（界面改版）。"
    "请根据页面元素列表，选出完成当前步骤要操作的元素。\n"
    "规则：1. 只能使用元素列表里的 ref，不能访问其他网址或执行脚本。2. 只能使用给出的允许操作。"
    "3. 填写的值只能从“允许的值”里原样选取：日期按页面要求的格式选对应写法，或拆成年、月、日分别选择；"
    "下拉框写要选的选项文字或选项值，该选项必须对应允许的值；"
    f"值为 {SECRET} 时原样返回 {SECRET}，由程序代填。4. 点击类步骤只能有 1 个动作，其他步骤最多 3 个动作。"
    "5. 页面上没有能完成该步骤的元素时，actions 返回空数组，并在 reason 里说明。\n"
    '只输出 JSON，例如 {"actions":[{"ref":"e3","op":"fill","value":"..."}],"reason":"..."}'
)

EXTRACT_SYSTEM = (
    "你从网页文字中提取数据。只能使用页面上出现的值，不要推测、计算或补全；页面上没有的数据给空列表或 null。"
    "数字去掉货币符号和千分位逗号；日期写成 YYYY-MM-DD；状态等文字按页面原文。只输出符合 JSON Schema 的 JSON 对象。"
)


class Action(BaseModel):
    ref: str
    op: Literal["click", "fill", "select"]
    value: str = ""


class LocateAnswer(BaseModel):
    actions: list[Action] = Field(default_factory=list)
    reason: str = ""


def check_actions(answer: LocateAnswer, step: Step, snap: Snapshot) -> str | None:
    """Reasons the answer is not allowed, or None. Used as the validation hook of the model call."""
    if len(answer.actions) > step.max_actions:
        return f"该步骤最多 {step.max_actions} 个动作"
    variants = step.variants()
    for action in answer.actions:
        element = snap.element(action.ref)
        if element is None:
            return f"{action.ref} 不在元素列表中"
        if element.disabled:
            return f"{action.ref} 不可用"
        if (action.op == "click") != (step.op == "click"):
            return f"该步骤只允许 {'/'.join(step.allowed_ops())} 操作"
        if action.op == "click":
            continue
        if element.tag == "select":
            if resolve_option(list(element.options), action.value, variants) is None:
                return f"{action.ref} 没有与 {action.value!r} 对应、且符合要填写的值的选项"
        elif element.tag not in ("input", "textarea"):
            return f"{action.ref} 不是输入框"
        elif variant_name(action.value, variants) is None:
            return f"值 {action.value!r} 不在允许的值中"
    return None


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def grounding_problems(data: BaseModel, text: str) -> list[str]:
    """Values in ``data`` that do not appear on the page (strings literally, numbers numerically, dates in any spelling)."""
    compact = re.sub(r"\s+", "", text)
    numbers = {float(n.replace(",", "")) for n in _NUMBER.findall(text)}
    problems: list[str] = []

    def visit(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                visit(item, f"{path}[{i}]")
        elif isinstance(value, bool) or value is None:
            return
        elif isinstance(value, (int, float)):
            if not any(abs(value - n) < 0.005 for n in numbers):
                problems.append(f"{path}={value} 不在页面上")
        elif isinstance(value, str) and value.strip():
            spellings = date_spellings(value) or [value]
            if not any(re.sub(r"\s+", "", s) in compact for s in spellings):
                problems.append(f"{path}={value!r} 不在页面上")

    visit(data.model_dump(mode="json"), "")
    return problems


class Navigator:
    def __init__(self, gateway: ModelGateway, recorder: RunRecorder, max_calls: int):
        self.gateway = gateway
        self.recorder = recorder
        self.max_calls = max_calls

    async def _call(self, messages: list[Message], schema: type[M], check: Callable[[M], str | None]) -> M:
        if self.recorder.model_calls >= self.max_calls:
            raise NeedsHuman(f"本次调用的模型兜底次数已达上限 {self.max_calls}，转人工")
        before = self.gateway.usage.calls
        try:
            return await asyncio.to_thread(self.gateway.structured, messages, schema, check)
        except LLMError as exc:
            raise NeedsHuman(f"模型兜底失败：{exc}") from exc
        finally:
            self.recorder.model_calls += self.gateway.usage.calls - before

    async def locate(self, step: Step, snap: Snapshot) -> LocateAnswer:
        values = list(dict.fromkeys(step.variants().values()))
        user = (f"步骤：{step.intent}\n允许的操作：{'/'.join(step.allowed_ops())}\n"
                f"允许的值：{json.dumps(values, ensure_ascii=False) if values else '（点击类步骤，无）'}\n"
                f"页面标题：{snap.title}\n页面分组标题：{' / '.join(snap.headings) or '（无）'}\n"
                f"页面文字（节选）：{snap.text[:600]}\n可操作元素：\n{snap.render_elements()}")
        messages: list[Message] = [{"role": "system", "content": LOCATE_SYSTEM}, {"role": "user", "content": user}]
        answer = await self._call(messages, LocateAnswer, lambda a: check_actions(a, step, snap))
        self.recorder.event("model_locate", step=step.key, actions=[a.model_dump() for a in answer.actions],
                            reason=answer.reason)
        return answer

    async def extract(self, schema: type[M], what: str, text: str) -> M:
        user = (f"要提取：{what}\nJSON Schema：{json.dumps(schema.model_json_schema(), ensure_ascii=False)}\n"
                f"页面文字：\n{text[:6000]}")
        messages: list[Message] = [{"role": "system", "content": EXTRACT_SYSTEM}, {"role": "user", "content": user}]

        def check(data: M) -> str | None:
            problems = grounding_problems(data, text)
            return "；".join(problems[:10]) if problems else None

        data = await self._call(messages, schema, check)
        self.recorder.event("model_extract", what=what, data=data.model_dump(mode="json"))
        return data
