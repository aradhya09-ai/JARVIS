"""PowerPoint generation with python-pptx (honest about content sources)."""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt
from pydantic import BaseModel, ConfigDict, Field

from security.permissions import Risk
from tools.application_launcher import find_shortcut, resolve_spec
from tools.base import ToolContext, ToolDefinition, ToolError, ToolResult

NAVY = RGBColor(0x06, 0x0C, 0x14)
PANEL = RGBColor(0x0B, 0x18, 0x24)
ORANGE = RGBColor(0xFF, 0x8A, 0x1E)
AMBER = RGBColor(0xFF, 0xC2, 0x6B)
CYAN = RGBColor(0x4F, 0xD8, 0xE8)
WHITE = RGBColor(0xF2, 0xF7, 0xFA)
MUTED = RGBColor(0x8F, 0xA6, 0xB4)

SLIDE_W, SLIDE_H = 13.333, 7.5

TEMPLATE_SECTIONS: list[tuple[str, list[str]]] = [
    ("Why this matters", [
        "The problem this topic addresses",
        "Who is affected and why now",
        "What good looks like in practice",
        "What this presentation will cover",
    ]),
    ("Background and context", [
        "How the field reached this point",
        "Key turning points and enablers",
        "The current landscape at a glance",
        "Common misunderstandings to clear up",
    ]),
    ("Core concepts", [
        "The foundational ideas",
        "How the pieces fit together",
        "Essential vocabulary",
        "A simple mental model",
    ]),
    ("How it works", [
        "Inputs and constraints",
        "The main workflow, step by step",
        "Tools and techniques involved",
        "Where human judgement stays essential",
    ]),
    ("Benefits and opportunities", [
        "Efficiency and quality gains",
        "New capabilities that become possible",
        "Scalability considerations",
        "Realistic expectations and trade-offs",
    ]),
    ("Challenges and risks", [
        "Technical limitations",
        "Adoption and organisational barriers",
        "Security, privacy and safety concerns",
        "Mitigation strategies",
    ]),
    ("Practical applications", [
        "Near-term use cases",
        "Illustrative industry scenarios",
        "What to pilot first",
        "How to measure success",
    ]),
    ("Best practices", [
        "Start small and measure",
        "Build feedback loops",
        "Document decisions and assumptions",
        "Review and iterate regularly",
    ]),
    ("Getting started", [
        "The first three steps",
        "Recommended learning resources",
        "A pragmatic 30-day plan",
        "Who needs to be involved",
    ]),
    ("Future outlook", [
        "Emerging trends to watch",
        "Open questions",
        "Signals that indicate real progress",
        "How to prepare today",
    ]),
]


class PresentationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str = Field(min_length=2, max_length=180)
    slides: int = Field(default=8, ge=4, le=20)
    audience: str = Field(default="general audience", max_length=120)
    style: str = Field(default="professional", max_length=60)
    open_after: bool = True
    filename: str | None = Field(default=None, max_length=120)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-").lower()
    return cleaned[:60] or "presentation"


def template_outline(topic: str, count: int, audience: str) -> list[dict[str, Any]]:
    content_count = max(1, count - 2)
    chosen = TEMPLATE_SECTIONS[:content_count]
    slides: list[dict[str, Any]] = [{"title": topic, "subtitle": f"Prepared for {audience}",
                                     "kind": "title"}]
    slides.append({
        "title": "Agenda",
        "bullets": [title for title, _ in chosen],
        "kind": "content",
    })
    for index, (title, bullets) in enumerate(chosen, start=1):
        slides.append({"title": title, "bullets": bullets, "kind": "content", "index": index})
    slides.append({"title": "Key takeaways",
                   "bullets": ["Clarify the goal before choosing tools",
                               "Pilot with a measurable use case",
                               "Keep humans accountable for decisions",
                               "Iterate based on observed outcomes"],
                   "kind": "closing"})
    return slides[:count]


def _clean_outline(raw: list[Any], topic: str, count: int) -> list[dict[str, Any]] | None:
    slides = [{"title": topic, "subtitle": "Prepared by JARVIS", "kind": "title"}]
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        bullets = [str(b).strip() for b in item.get("bullets", []) if str(b).strip()]
        if title and 2 <= len(bullets) <= 6:
            slides.append({"title": title[:90], "bullets": bullets[:6], "kind": "content"})
    if len(slides) < 3:
        return None
    slides.append({"title": "Key takeaways",
                   "bullets": ["Clarify the goal before choosing tools",
                               "Pilot with a measurable use case",
                               "Keep humans accountable for decisions",
                               "Iterate based on observed outcomes"],
                   "kind": "closing"})
    return slides[:count]


async def _outline_via_model(ctx: ToolContext, args: PresentationArgs) -> tuple[list[dict[str, Any]], str]:
    if ctx.router is None:
        return template_outline(args.topic, args.slides, args.audience), "structural template (no AI model available)"
    prompt = (
        f"Draft a slide outline for a {args.style} presentation titled '{args.topic}' "
        f"for {args.audience}. Produce exactly {args.slides - 2} content slides. "
        "Return JSON only: an array of objects with keys 'title' (string) and "
        "'bullets' (array of 3-5 short strings). Do not invent statistics, dates, "
        "quotes or sources. Each bullet must be a concise, defensible statement."
    )
    try:
        text = await ctx.router.complete(
            [{"role": "system", "content": "You are a precise presentation outline writer. Reply with JSON only."},
             {"role": "user", "content": prompt}],
            json_mode=True,
        )
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("slides", data.get("outline", []))
        cleaned = _clean_outline(data, args.topic, args.slides)
        if cleaned:
            return cleaned, f"AI outline ({ctx.router.active_name()})"
    except Exception:  # noqa: BLE001 - fall back to a reliable template
        pass
    return template_outline(args.topic, args.slides, args.audience), "structural template (model outline rejected or unavailable)"


def _blank_slide(prs: Presentation):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _background(slide) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = NAVY
    top = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(SLIDE_W), Inches(0.06))
    top.fill.solid()
    top.fill.fore_color.rgb = ORANGE
    top.line.fill.background()


def _footer(slide, page: int) -> None:
    box = slide.shapes.add_textbox(Inches(0.6), Inches(SLIDE_H - 0.55), Inches(6), Inches(0.3))
    frame = box.text_frame
    frame.text = "J.A.R.V.I.S.  ·  generated locally"
    for paragraph in frame.paragraphs:
        for run in paragraph.runs:
            run.font.size = Pt(9)
            run.font.color.rgb = MUTED
    number = slide.shapes.add_textbox(Inches(SLIDE_W - 1.2), Inches(SLIDE_H - 0.55),
                                      Inches(0.6), Inches(0.3))
    number.text_frame.text = f"{page:02d}"
    for paragraph in number.text_frame.paragraphs:
        paragraph.alignment = PP_ALIGN.RIGHT
        for run in paragraph.runs:
            run.font.size = Pt(9)
            run.font.color.rgb = ORANGE


def _ring(slide, left, top, size, color: RGBColor, width: float = 1.0) -> None:
    shape = slide.shapes.add_shape(
        MSO_SHAPE.OVAL, Inches(left), Inches(top), Inches(size), Inches(size)
    )
    shape.fill.background()
    shape.line.color.rgb = color
    shape.line.width = Pt(width)
    shape.rotation = 0


def _render_title(prs: Presentation, slide_data: dict[str, Any]) -> None:
    slide = _blank_slide(prs)
    _background(slide)
    _ring(slide, 4.4, 1.2, 4.5, ORANGE, 1.0)
    _ring(slide, 5.0, 1.8, 3.3, CYAN, 0.75)
    _ring(slide, 5.55, 2.35, 2.2, AMBER, 0.5)
    _ring(slide, 6.05, 2.85, 1.2, ORANGE, 1.5)

    title_box = slide.shapes.add_textbox(Inches(0.9), Inches(4.3), Inches(11.5), Inches(1.3))
    frame = title_box.text_frame
    frame.word_wrap = True
    frame.text = slide_data["title"]
    paragraph = frame.paragraphs[0]
    paragraph.alignment = PP_ALIGN.CENTER
    for run in paragraph.runs:
        run.font.size = Pt(40)
        run.font.bold = True
        run.font.name = "Segoe UI Semibold"
        run.font.color.rgb = WHITE

    subtitle = slide.shapes.add_textbox(Inches(0.9), Inches(5.55), Inches(11.5), Inches(0.6))
    subtitle_frame = subtitle.text_frame
    subtitle_frame.text = slide_data.get("subtitle", "")
    for paragraph in subtitle_frame.paragraphs:
        paragraph.alignment = PP_ALIGN.CENTER
        for run in paragraph.runs:
            run.font.size = Pt(15)
            run.font.color.rgb = AMBER
    _footer(slide, 1)


def _render_content(prs: Presentation, data: dict[str, Any], page: int) -> None:
    slide = _blank_slide(prs)
    _background(slide)
    accent = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.7), Inches(0.75),
                                    Inches(0.09), Inches(0.7))
    accent.fill.solid()
    accent.fill.fore_color.rgb = ORANGE
    accent.line.fill.background()

    title_box = slide.shapes.add_textbox(Inches(0.95), Inches(0.65), Inches(11.4), Inches(0.9))
    title_frame = title_box.text_frame
    title_frame.word_wrap = True
    title_frame.text = data["title"]
    for run in title_frame.paragraphs[0].runs:
        run.font.size = Pt(28)
        run.font.bold = True
        run.font.name = "Segoe UI Semibold"
        run.font.color.rgb = WHITE

    panel = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.95), Inches(1.75),
                                   Inches(7.2), Inches(4.6))
    panel.fill.solid()
    panel.fill.fore_color.rgb = PANEL
    panel.line.color.rgb = RGBColor(0x14, 0x2A, 0x38)
    panel.line.width = Pt(0.75)
    panel.adjustments[0] = 0.04

    body = slide.shapes.add_textbox(Inches(1.3), Inches(2.05), Inches(6.5), Inches(4.0))
    frame = body.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.TOP
    for index, bullet in enumerate(data.get("bullets", [])):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.space_after = Pt(14)
        marker = paragraph.add_run()
        marker.text = "▸  "
        marker.font.size = Pt(17)
        marker.font.color.rgb = ORANGE
        run = paragraph.add_run()
        run.text = bullet
        run.font.size = Pt(17)
        run.font.color.rgb = WHITE

    badge = slide.shapes.add_shape(MSO_SHAPE.HEXAGON, Inches(9.2), Inches(2.0),
                                   Inches(2.9), Inches(1.3))
    badge.fill.solid()
    badge.fill.fore_color.rgb = PANEL
    badge.line.color.rgb = CYAN
    badge.line.width = Pt(1.0)
    badge_text = badge.text_frame
    badge_text.text = f"SECTION\n{page - 2:02d}"
    for paragraph in badge_text.paragraphs:
        paragraph.alignment = PP_ALIGN.CENTER
        for run in paragraph.runs:
            run.font.size = Pt(13)
            run.font.color.rgb = AMBER

    label = slide.shapes.add_textbox(Inches(9.2), Inches(3.7), Inches(2.9), Inches(2.2))
    label_frame = label.text_frame
    label_frame.word_wrap = True
    label_frame.text = "Focus on decisions, not decoration."
    for run in label_frame.paragraphs[0].runs:
        run.font.size = Pt(12)
        run.font.color.rgb = MUTED
    _footer(slide, page)


def _render_closing(prs: Presentation, data: dict[str, Any], page: int) -> None:
    slide = _blank_slide(prs)
    _background(slide)
    _ring(slide, 5.0, 1.0, 3.3, ORANGE, 1.0)
    _ring(slide, 5.55, 1.55, 2.2, CYAN, 0.5)
    heading = slide.shapes.add_textbox(Inches(0.9), Inches(4.2), Inches(11.5), Inches(0.9))
    heading.text_frame.text = data["title"]
    for paragraph in heading.text_frame.paragraphs:
        paragraph.alignment = PP_ALIGN.CENTER
        for run in paragraph.runs:
            run.font.size = Pt(34)
            run.font.bold = True
            run.font.color.rgb = WHITE
    body = slide.shapes.add_textbox(Inches(1.8), Inches(5.1), Inches(9.7), Inches(1.0))
    body.text_frame.word_wrap = True
    body.text_frame.text = "  ·  ".join(data.get("bullets", [])[:3])
    for paragraph in body.text_frame.paragraphs:
        paragraph.alignment = PP_ALIGN.CENTER
        for run in paragraph.runs:
            run.font.size = Pt(12)
            run.font.color.rgb = AMBER
    _footer(slide, page)


def build_presentation(outline: list[dict[str, Any]], destination: Path, topic: str,
                       source_note: str) -> int:
    prs = Presentation()
    prs.slide_width = Inches(SLIDE_W)
    prs.slide_height = Inches(SLIDE_H)
    page = 1
    for slide_data in outline:
        kind = slide_data.get("kind")
        if kind == "title":
            _render_title(prs, slide_data)
        elif kind == "closing":
            _render_closing(prs, slide_data, page)
        else:
            _render_content(prs, slide_data, page)
        page += 1
    notes = prs.slides[0].notes_slide.notes_text_frame
    notes.text = (
        f"Topic: {topic}\nOutline source: {source_note}\n"
        "No live web research was performed for this deck."
    )
    prs.save(str(destination))
    check = Presentation(str(destination))
    return len(check.slides)


def _open_in_powerpoint(ctx: ToolContext, path: Path) -> dict[str, Any]:
    spec = find_shortcut(ctx.repo, "PowerPoint")
    executable = resolve_spec(spec) if spec else None
    if executable is None:
        return {"opened": False, "reason": "PowerPoint is not installed."}
    process = subprocess.Popen(
        [str(executable), str(path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return {"opened": process.poll() is None, "pid": process.pid}


async def presentation_handler(args: PresentationArgs, ctx: ToolContext) -> ToolResult:
    outline, source_note = await _outline_via_model(ctx, args)
    target_dir = ctx.settings.output_dir / "presentations"
    target_dir.mkdir(parents=True, exist_ok=True)
    base = _slug(args.filename or args.topic)
    destination = target_dir / f"{base}-{datetime.now().strftime('%Y%m%d-%H%M')}.pptx"
    if destination.exists():
        destination = target_dir / f"{base}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pptx"
    ctx.note(f"Rendering {len(outline)} slides")
    count = await asyncio.to_thread(
        build_presentation, outline, destination, args.topic, source_note
    )
    if not destination.exists() or destination.stat().st_size < 8000:
        raise ToolError("The presentation file was not written correctly; nothing was opened.")
    ctx.audit.record(ctx.actor, "docs.presentation", Risk.LOW.value,
                     {"topic": args.topic, "path": str(destination), "slides": count,
                      "outline_source": source_note}, "ok")
    opened: dict[str, Any] = {"opened": False, "reason": "Open in PowerPoint was not requested."}
    if args.open_after:
        opened = await asyncio.to_thread(_open_in_powerpoint, ctx, destination)
    # No folder is read out: the deck opening on screen is the confirmation. The
    # outline's origin stays in the reply because a template deck is not a drafted one.
    summary = f"Executed. {count} slides on {args.topic}"
    summary += (
        " from a plain template — no model outline was available."
        if source_note.startswith("structural template")
        else "."
    )
    if args.open_after and not opened.get("opened"):
        summary += " PowerPoint did not open it."
    return ToolResult.success(summary, path=str(destination), slides=count,
                              outline_source=source_note, opened=opened)


def build() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="docs.create_presentation",
            description="Generate a themed .pptx deck on a topic and optionally open it.",
            args_model=PresentationArgs,
            risk=Risk.MODERATE,
            handler=presentation_handler,
            category="documents",
        )
    ]
