"""
On-demand, AI-generated plain-language explanations of alert rules, via the
Google Gemini API (not Anthropic/Claude -- a different provider, different SDK).

Reads GEMINI_API_KEY or GOOGLE_API_KEY from the environment (see app.py,
which checks this once at startup). Generation only ever happens when the
user explicitly confirms it from the describe view -- nothing here is called
automatically.
"""

from __future__ import annotations

import os

from seer.models import AlertRule

EXPLAIN_MODEL = "gemini-2.5-pro"

EXPLAIN_SYSTEM_PROMPT = (
    "You explain Prometheus alerting rules to an on-call SRE. Given an alert's "
    "name and PromQL expression, respond in Markdown: a bullet list ('-') of "
    "4-6 short items covering what the expression measures in plain language, "
    "the condition that triggers it, likely real-world causes, and a suggested "
    "first response step. Use **bold** for the key metric or threshold where "
    "it helps, and backticks for metric/function names. No preamble, no "
    "headings, no code fence around the whole answer, no restating the raw "
    "expression verbatim. Keep the whole answer under ~120 words."
)


def gemini_api_key_from_env() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def build_explain_prompt(rule: AlertRule) -> str:
    return f"Alert: {rule.name}\nExpression: {rule.expr}"


async def stream_explanation(rule: AlertRule, api_key: str):
    """Async-yield explanation text chunks as they arrive from Gemini."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    stream = await client.aio.models.generate_content_stream(
        model=EXPLAIN_MODEL,
        contents=build_explain_prompt(rule),
        config=types.GenerateContentConfig(
            system_instruction=EXPLAIN_SYSTEM_PROMPT,
            # Gemini 2.5 models "think" before answering, and thinking tokens
            # count against max_output_tokens. This cap therefore needs room
            # for thinking + answer; the answer length itself is constrained
            # by the prompt (~120 words). 400 was too tight -- the model spent
            # most of it thinking and the visible answer was truncated.
            max_output_tokens=4096,
        ),
    )
    async for chunk in stream:
        if chunk.text:
            yield chunk.text
