"""A flow step: one intent, the scripted (v1) selector for it, and the values a fallback may use."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Op = Literal["click", "fill", "select"]
SECRET = "<secret>"


@dataclass(frozen=True)
class Step:
    key: str                         # stable id, e.g. "entry.line2.qty": selector-cache key and trace label
    intent: str                      # what the step does, in words the model sees
    selector: str                    # scripted selector, written against layout v1
    op: Op
    value: str = ""
    secret: bool = False             # the value is never logged or shown to the model
    optional: bool = False           # the element may legitimately be absent (e.g. no next page)
    absent_marker: str = ""          # scripted proof of legitimate absence: visible marker + missing selector
    alternatives: tuple[str, ...] = ()   # other spellings that identify the same value (e.g. the customer name)
    cacheable: bool = True

    def variants(self) -> dict[str, str]:
        """Named values a fallback action may type or pick; dates expand into common formats and components."""
        if self.op == "click":
            return {}
        if self.secret:
            return {"secret": SECRET}
        found: dict[str, str] = {"value": self.value}
        match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", self.value)
        if match:
            y, m, d = match.groups()
            found |= {"slash": f"{y}/{m}/{d}", "dot": f"{y}.{m}.{d}", "compact": f"{y}{m}{d}",
                      "year": y, "month": m, "day": d}
        for i, alt in enumerate(self.alternatives):
            found[f"alt{i}"] = alt
        return found

    def allowed_ops(self) -> tuple[Op, ...]:
        return ("click",) if self.op == "click" else ("fill", "select")

    @property
    def max_actions(self) -> int:
        return 1 if self.op == "click" else 3


def date_spellings(value: str) -> list[str]:
    """All usual spellings of a date string (2026-11-02, 2026/11/02, 2026.11.02, 20261102, unpadded), or []."""
    match = (re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", value.strip())
             or re.fullmatch(r"(\d{4})(\d{2})(\d{2})", value.strip()))
    if not match:
        return []
    y, m, d = (int(g) for g in match.groups())
    spellings = [f"{y}{sep}{m:02d}{sep}{d:02d}" for sep in ("-", "/", ".")] + [f"{y}{m:02d}{d:02d}"]
    spellings += [f"{y}{sep}{m}{sep}{d}" for sep in ("-", "/", ".")] + [f"{y}年{m}月{d}日"]
    return spellings


def _norm(text: str) -> str:
    text = re.sub(r"\s+", "", text).rstrip("年月日")
    if text.isdigit():
        return text.lstrip("0") or "0"
    return text


def option_matches(option_value: str, option_text: str, variants: dict[str, str]) -> bool:
    """Does a <select> option denote one of the step's values? Short tokens must match exactly."""
    value, text = _norm(option_value), _norm(option_text)
    for candidate in variants.values():
        token = _norm(candidate)
        if not token:
            continue
        if token in (value, text):
            return True
        if len(token) >= 4 and (token in value or token in text):
            return True
    return False


def resolve_option(options: list[tuple[str, str]], requested: str, variants: dict[str, str]) -> str | None:
    """The value of the option to pick: ``requested`` may be an option's value or text, or one of the step's own
    values; empty means "any option that denotes the step's value". The option must denote the step's value."""
    requested = requested.strip()
    pool = options
    if requested:
        pool = [o for o in options if requested in (o[0], o[1].strip())]
        if not pool and requested in variants.values():
            pool = [o for o in options if option_matches(o[0], o[1], {"requested": requested})]
    return next((value for value, text in pool if option_matches(value, text, variants)), None)


def variant_name(value: str, variants: dict[str, str]) -> str | None:
    return next((name for name, v in variants.items() if v == value.strip()), None)
