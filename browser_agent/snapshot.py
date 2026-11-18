"""Compact, deterministic description of a page for the model: visible interactive elements tagged with refs.

Hidden inputs (view state, tokens) and password values are never included; element order is DOM order, so the
same page always produces the same snapshot (which keeps recorded model calls replayable).
"""
from __future__ import annotations

from dataclasses import dataclass

from playwright.async_api import Locator, Page

AGENT_JS = r"""
window.__agent = window.__agent || (() => {
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const labelOf = el => {
    if (el.labels && el.labels.length) return clean(el.labels[0].innerText);
    const aria = el.getAttribute('aria-label'); if (aria) return clean(aria);
    const tag = el.tagName.toLowerCase();
    if (tag === 'a' || tag === 'button') return clean(el.innerText);
    if (tag === 'input' && ['button', 'submit', 'image'].includes(el.type)) return clean(el.value || el.alt);
    if (el.placeholder) return clean(el.placeholder);
    if (el.title) return clean(el.title);
    const cell = el.closest('td,th');
    if (cell && cell.previousElementSibling) {
      const t = clean(cell.previousElementSibling.innerText); if (t) return t.slice(0, 30);
    }
    let prev = el.previousSibling;
    while (prev && !clean(prev.textContent)) prev = prev.previousSibling;
    return prev ? clean(prev.textContent).slice(-30) : '';
  };
  const columnOf = el => {
    const cell = el.closest('td,th'), table = el.closest('table');
    if (!cell || !table) return '';
    const head = table.tHead ? table.tHead.rows[0] : table.rows[0];
    if (!head || head === cell.parentElement) return '';
    const h = head.cells[Array.from(cell.parentElement.children).indexOf(cell)];
    return h ? clean(h.innerText).slice(0, 20) : '';
  };
  const rowOf = el => {
    const row = el.closest('tr, .line-row, [role=row], li');
    return row ? clean(row.innerText).slice(0, 40) : '';
  };
  const describe = el => ({tag: el.tagName.toLowerCase(), type: (el.getAttribute('type') || '').toLowerCase(),
                           label: labelOf(el)});
  const snapshot = () => {
    document.querySelectorAll('[data-agent-ref]').forEach(e => e.removeAttribute('data-agent-ref'));
    const query = 'input:not([type=hidden]), select, textarea, button, a[href], [onclick], [role=button]';
    const elements = [];
    let n = 0;
    for (const el of document.querySelectorAll(query)) {
      if (!visible(el)) continue;
      const ref = 'e' + (++n);
      el.setAttribute('data-agent-ref', ref);
      const d = describe(el);
      const item = {ref, tag: d.tag, type: d.type, label: d.label, name: (el.getAttribute('name') || '').slice(0, 48),
                    column: columnOf(el), row: rowOf(el), value: '', options: [], disabled: !!el.disabled};
      if (d.tag === 'select') item.options = Array.from(el.options).slice(0, 40).map(o => [o.value, clean(o.text)]);
      else if ((d.tag === 'input' || d.tag === 'textarea') && d.type !== 'password'
               && !['button', 'submit', 'image'].includes(d.type)) item.value = (el.value || '').slice(0, 40);
      elements.push(item);
    }
    const headings = Array.from(document.querySelectorAll('h1,h2,h3,legend,.tabs .on'))
      .filter(visible).map(e => clean(e.innerText)).filter(Boolean).slice(0, 8);
    return {title: document.title, headings, text: clean(document.body.innerText), elements};
  };
  const selectorOf = el => {
    if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id);
    const name = el.getAttribute('name');
    if (name) {
      const s = el.tagName.toLowerCase() + '[name="' + name.replace(/"/g, '\\"') + '"]';
      if (document.querySelectorAll(s).length === 1) return s;
    }
    const parts = [];
    for (let node = el; node && node.nodeType === 1 && node !== document.body; node = node.parentElement) {
      let i = 1;
      for (let sib = node.previousElementSibling; sib; sib = sib.previousElementSibling) if (sib.tagName === node.tagName) i++;
      parts.unshift(node.tagName.toLowerCase() + ':nth-of-type(' + i + ')');
    }
    return 'body > ' + parts.join(' > ');
  };
  const errors = () => {
    const out = [];
    for (const el of document.querySelectorAll('body *')) {
      if (!visible(el)) continue;
      const own = Array.from(el.childNodes).filter(c => c.nodeType === 3).map(c => c.textContent).join('').trim();
      if (!own) continue;
      const marks = (typeof el.className === 'string' ? el.className : '') + ' ' + el.id;
      const rgb = (getComputedStyle(el).color.match(/\d+/g) || [0, 0, 0]).map(Number);
      const red = rgb[0] > 150 && rgb[1] < 90 && rgb[2] < 90;
      if (red || /(^|[\s_-])(err|error|errors|alert-error|alert-danger|invalid)([\s_-]|$)/i.test(marks)) {
        const t = clean(el.innerText);
        if (t && t.length < 300 && !out.includes(t)) out.push(t);
      }
    }
    return out;
  };
  return {snapshot, describe, selectorOf, errors};
})();
"""


@dataclass(frozen=True)
class Element:
    ref: str
    tag: str
    type: str
    label: str
    name: str
    column: str
    row: str
    value: str
    options: tuple[tuple[str, str], ...]
    disabled: bool

    def render(self) -> str:
        kind = f"{self.tag}[{self.type}]" if self.type else self.tag
        parts = [self.ref, kind]
        for title, value in (("标签", self.label), ("列", self.column), ("所在行", self.row), ("name", self.name)):
            if value:
                parts.append(f"{title}:{value}")
        if self.tag in ("input", "textarea") and self.type not in ("button", "submit", "image", "password"):
            parts.append(f'当前值:"{self.value}"')
        if self.options:
            parts.append("选项:" + " / ".join(text if text == value else f"{text}={value}" for value, text in self.options))
        if self.disabled:
            parts.append("不可用")
        return " | ".join(parts)


@dataclass(frozen=True)
class Snapshot:
    title: str
    headings: tuple[str, ...]
    text: str
    elements: tuple[Element, ...]

    def element(self, ref: str) -> Element | None:
        return next((e for e in self.elements if e.ref == ref), None)

    def render_elements(self) -> str:
        return "\n".join(e.render() for e in self.elements)


async def _agent(page: Page) -> None:
    await page.evaluate(AGENT_JS)


async def take_snapshot(page: Page) -> Snapshot:
    await _agent(page)
    raw = await page.evaluate("window.__agent.snapshot()")
    elements = tuple(Element(e["ref"], e["tag"], e["type"], e["label"], e["name"], e["column"], e["row"], e["value"],
                             tuple((o[0], o[1]) for o in e["options"]), e["disabled"]) for e in raw["elements"])
    return Snapshot(raw["title"], tuple(raw["headings"]), raw["text"], elements)


async def page_text(page: Page) -> str:
    return await page.evaluate("document.body.innerText")


async def page_errors(page: Page) -> list[str]:
    """Validation messages, recognised generically (red text or error-ish class/id), not by layout-specific selectors."""
    await _agent(page)
    return list(await page.evaluate("window.__agent.errors()"))


async def describe(locator: Locator) -> dict[str, str]:
    await _agent(locator.page)
    return await locator.evaluate("el => window.__agent.describe(el)")


async def selector_of(locator: Locator) -> str:
    await _agent(locator.page)
    return await locator.evaluate("el => window.__agent.selectorOf(el)")
