"""Pyldon Agent Runner - runs inside Docker container.

Migrated from NanoClaw container/agent-runner/src/index.ts.
Receives config via stdin, runs Claude Agent SDK query(), outputs result to stdout.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Sentinel markers for output parsing (must match host container_runner.py)
OUTPUT_START_MARKER = "---PYLDON_OUTPUT_START---"
OUTPUT_END_MARKER = "---PYLDON_OUTPUT_END---"


def log(message: str) -> None:
    """Log to stderr (stdout is reserved for output)."""
    print(f"[agent-runner] {message}", file=sys.stderr, flush=True)


def write_output(output: dict) -> None:
    """Write structured output to stdout between sentinel markers."""
    print(OUTPUT_START_MARKER, flush=True)
    print(json.dumps(output), flush=True)
    print(OUTPUT_END_MARKER, flush=True)


def sanitize_filename(summary: str) -> str:
    """Sanitize a string for use as a filename."""
    import re
    return re.sub(r"[^a-z0-9]+", "-", summary.lower()).strip("-")[:50]


def generate_fallback_name() -> str:
    """Generate a fallback conversation filename."""
    now = datetime.now(timezone.utc)
    return f"conversation-{now.hour:02d}{now.minute:02d}"


def get_session_summary(session_id: str, transcript_path: str) -> str | None:
    """Get the session summary from sessions-index.json."""
    project_dir = Path(transcript_path).parent
    index_path = project_dir / "sessions-index.json"

    if not index_path.exists():
        log(f"Sessions index not found at {index_path}")
        return None

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for entry in index.get("entries", []):
            if entry.get("sessionId") == session_id:
                return entry.get("summary")
    except Exception as e:
        log(f"Failed to read sessions index: {e}")

    return None


def parse_transcript(content: str) -> list[dict]:
    """Parse JSONL transcript into messages."""
    messages = []
    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if entry.get("type") == "user" and entry.get("message", {}).get("content"):
                msg_content = entry["message"]["content"]
                if isinstance(msg_content, str):
                    text = msg_content
                else:
                    text = "".join(c.get("text", "") for c in msg_content)
                if text:
                    messages.append({"role": "user", "content": text})
            elif entry.get("type") == "assistant" and entry.get("message", {}).get("content"):
                text_parts = [
                    c.get("text", "")
                    for c in entry["message"]["content"]
                    if c.get("type") == "text"
                ]
                text = "".join(text_parts)
                if text:
                    messages.append({"role": "assistant", "content": text})
        except (json.JSONDecodeError, TypeError, KeyError):
            continue

    return messages


def format_transcript_markdown(messages: list[dict], title: str | None = None) -> str:
    """Format parsed messages as markdown."""
    now = datetime.now(timezone.utc)
    lines = [
        f"# {title or 'Conversation'}",
        "",
        f"Archived: {now.strftime('%b %d, %I:%M %p')}",
        "",
        "---",
        "",
    ]

    for msg in messages:
        sender = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"][:2000] + "..." if len(msg["content"]) > 2000 else msg["content"]
        lines.append(f"**{sender}**: {content}")
        lines.append("")

    return "\n".join(lines)


async def main() -> None:
    """Main agent runner entry point."""
    from claude_code_sdk import query, ClaudeCodeOptions

    from agent_runner.ipc_mcp import create_ipc_mcp

    # Read input from stdin
    try:
        stdin_data = sys.stdin.read()
        input_data = json.loads(stdin_data)
        log(f"Received input for group: {input_data.get('groupFolder')}")
    except Exception as e:
        write_output({
            "status": "error",
            "result": None,
            "error": f"Failed to parse input: {e}",
        })
        sys.exit(1)

    prompt = input_data["prompt"]
    session_id = input_data.get("sessionId")
    group_folder = input_data["groupFolder"]
    chat_jid = input_data["chatJid"]
    is_main = input_data.get("isMain", False)
    is_scheduled_task = input_data.get("isScheduledTask", False)

    # Create IPC MCP server
    ipc_mcp = create_ipc_mcp(
        chat_jid=chat_jid,
        group_folder=group_folder,
        is_main=is_main,
    )

    # Add context for scheduled tasks
    if is_scheduled_task:
        prompt = (
            "[SCHEDULED TASK - You are running automatically, not in response to a "
            "user message. Use mcp__pyldon__send_message if needed to communicate "
            "with the user.]\n\n" + prompt
        )

    # Prepend identity if exists
    identity_path = Path("/workspace/group/IDENTITY.md")
    if identity_path.exists():
        identity = identity_path.read_text(encoding="utf-8")
        prompt = f"{identity}\n\n---\n\n{prompt}"

    result: str | None = None
    new_session_id: str | None = None

    try:
        log("Starting agent...")

        options = ClaudeCodeOptions(
            cwd="/workspace/group",
            allowed_tools=[
                "Bash",
                "Read", "Write", "Edit", "Glob", "Grep",
                "WebSearch", "WebFetch",
                "mcp__pyldon__*",
            ],
            permission_mode="bypassPermissions",
        )

        if session_id:
            options.resume = session_id

        async for message in query(
            prompt=prompt,
            options=options,
        ):
            # Debug: log all messages
            msg_type = getattr(message, "type", "unknown")
            msg_subtype = getattr(message, "subtype", "N/A")
            log(f"Message: type={msg_type}, subtype={msg_subtype}")

            if msg_type == "system" and msg_subtype == "init":
                new_session_id = getattr(message, "session_id", None)
                log(f"Session initialized: {new_session_id}")

            if hasattr(message, "result") and message.result:
                result = message.result
                log(f"Got result: {result[:100]}...")

            # Also check for assistant messages with text content
            if msg_type == "assistant" and hasattr(message, "message"):
                assistant_msg = message.message
                if hasattr(assistant_msg, "content"):
                    text_parts = [
                        c.text
                        for c in assistant_msg.content
                        if hasattr(c, "type") and c.type == "text" and hasattr(c, "text")
                    ]
                    text_content = "".join(text_parts)
                    if text_content and not result:
                        result = text_content
                        log(f"Got assistant text: {text_content[:100]}...")

        log("Agent completed successfully")
        write_output({
            "status": "success",
            "result": result,
            "newSessionId": new_session_id,
        })

    except Exception as e:
        error_message = str(e)
        log(f"Agent error: {error_message}")
        write_output({
            "status": "error",
            "result": None,
            "newSessionId": new_session_id,
            "error": error_message,
        })
        sys.exit(1)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
