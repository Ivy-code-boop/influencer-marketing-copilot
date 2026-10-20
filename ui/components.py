"""可复用的展示片段（纯字符串，不含任何业务逻辑）。

所有用户输入都经过 html.escape，避免把产品名等内容直接拼进 HTML。
"""

from __future__ import annotations

import html

# 抽象"增长曲线"标记：不用 emoji，纯内联 SVG
LOGO_SVG = (
    '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">'
    '<path d="M2 11.8L6 7.8L9 10.4L14 4.6" stroke="white" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round"/>'
    '<circle cx="13" cy="12.2" r="1.5" fill="white" fill-opacity="0.9"/>'
    "</svg>"
)


def chip(text: str, tone: str = "neutral", title: str | None = None) -> str:
    """状态胶囊。tone: neutral / success / warning / danger / ai。"""
    title_attr = f' title="{html.escape(title, quote=True)}"' if title else ""
    return (
        f'<span class="imc-chip imc-chip-{tone}"{title_attr}>'
        f"{html.escape(text)}</span>"
    )


def product_bar(
    product_name: str, tagline: str, chips: list[tuple[str, str]]
) -> str:
    """顶部产品栏：渐变 logo + 产品名 + 一句话定位 + 右侧版本胶囊。"""
    chips_html = "".join(chip(text, tone) for text, tone in chips)
    return (
        '<div class="imc-product-bar">'
        '<div class="imc-brand">'
        f'<div class="imc-logo">{LOGO_SVG}</div>'
        '<div class="imc-brand-text">'
        f'<div class="imc-product-name">{html.escape(product_name)}</div>'
        f'<div class="imc-product-tagline">{html.escape(tagline)}</div>'
        "</div></div>"
        f'<div class="imc-bar-right">{chips_html}</div>'
        "</div>"
    )


def run_status_bar(
    chips: list[tuple[str, str, str | None]],
    status_text: str,
    status_tone: str,
    stale_reasons: list[str] | None = None,
) -> str:
    """运行状态条：数据/任务/规则/Prompt/模型 + 最新或已过期。"""
    reasons = stale_reasons or []
    chips_html = "".join(chip(text, tone, title) for text, tone, title in chips)
    stale_class = " imc-runbar-stale" if status_tone == "warning" else ""
    reason_note = ""
    if reasons:
        reason_text = html.escape("；".join(reasons))
        reason_note = f'<span class="imc-runbar-reason">{reason_text}</span>'
    return (
        f'<div class="imc-runbar{stale_class}">'
        '<span class="imc-runbar-label">运行状态</span>'
        f"{chips_html}"
        f'{chip(status_text, status_tone, "；".join(reasons) if reasons else None)}'
        f"{reason_note}"
        "</div>"
    )


def side_progress(current: int, total: int, completed: int) -> str:
    """侧边栏顶部的流程进度（纯展示，交互仍由 st.radio 负责）。"""
    pct = max(0, min(100, round(completed / total * 100))) if total else 0
    return (
        '<div class="imc-side-progress">'
        '<div class="imc-side-progress-row">'
        f"<span>流程 {current} / {total}</span><span>已完成 {completed}</span>"
        "</div>"
        '<div class="imc-side-progress-track">'
        f'<div class="imc-side-progress-fill" style="width:{pct}%"></div>'
        "</div></div>"
    )
