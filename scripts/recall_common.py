#!/usr/bin/env python3
"""Shared helpers for parsing Claude/Codex session message blocks."""

TEXT_BLOCK_TYPES = {"text", "input_text", "output_text"}

SKIP_MARKERS = (
    "<user_instructions>",
    "<environment_context>",
    "<permissions instructions>",
    "# AGENTS.md instructions",
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<command-name>",
    "<command-message>",
    "<system-reminder>",
    "<task-notification>",
    "<task-id>",
    "<tool-use-id>",
    "<bash-stdout>",
    "<bash-input>",
)


def extract_text(content):
    """Extract plain text from message content (string or array format)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type", "") in TEXT_BLOCK_TYPES
        ]
        return "\n".join(filter(None, parts))
    return ""


def extract_claude_content(entry):
    """Extract Claude message content from wrapped or top-level entry shapes."""
    message = entry.get("message")
    if isinstance(message, dict):
        if "content" in message:
            return message.get("content", "")
        return entry.get("content", "")
    if isinstance(message, (str, list)):
        return message
    return entry.get("content", "")


def is_noise(text):
    """Return True if text is system noise that should not be indexed or shown as summary."""
    if not text:
        return True
    stripped = text.lstrip()
    if not stripped:
        return True
    return any(stripped.startswith(marker) for marker in SKIP_MARKERS)
