"""Pyldon Agent Runner — runs inside Docker container.

Spawns pi in RPC mode, sends the prompt, collects the response,
and writes it to stdout between sentinel markers for the host to parse.

IPC tools (send_message, schedule_task, etc.) are provided by the
pi extension at /app/pi-extensions/pyldon-ipc.ts.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

OUTPUT_START_MARKER = "---PYLDON_OUTPUT_START---"
OUTPUT_END_MARKER = "---PYLDON_OUTPUT_END---"

PI_BINARY = os.environ.get("PYLDON_PI_BINARY", "pi")
PI_PROVIDER = os.environ.get("PYLDON_PI_PROVIDER", "amazon-bedrock")
PI_MODEL = os.environ.get("PYLDON_PI_MODEL", "")
PI_EXTENSION = os.environ.get(
    "PYLDON_PI_EXTENSION", "/app/pi-extensions/pyldon-ipc.ts"
)


def log(msg: str) -> None:
    print(f"[agent-runner] {msg}", file=sys.stderr, flush=True)


def write_output(output: dict) -> None:
    print(OUTPUT_START_MARKER, flush=True)
    print(json.dumps(output), flush=True)
    print(OUTPUT_END_MARKER, flush=True)


async def run(input_data: dict) -> dict:
    prompt = input_data["prompt"]
    group_folder = input_data["groupFolder"]
    chat_jid = input_data["chatJid"]
    is_main = input_data.get("isMain", False)
    is_scheduled_task = input_data.get("isScheduledTask", False)
    images = input_data.get("images", [])

    # Prepend identity
    identity_path = Path("/workspace/group/IDENTITY.md")
    if identity_path.exists():
        identity = identity_path.read_text(encoding="utf-8")
        prompt = f"{identity}\n\n---\n\n{prompt}"

    # Scheduled task prefix
    if is_scheduled_task:
        prompt = (
            "[SCHEDULED TASK — You are running automatically, not in response to a "
            "user message. Use pyldon_send_message tool to communicate with the user.]\n\n"
            + prompt
        )

    # Build pi command
    cmd = [PI_BINARY, "--mode", "rpc", "--no-session", "-e", PI_EXTENSION]
    if PI_PROVIDER:
        cmd.extend(["--provider", PI_PROVIDER])
    if PI_MODEL:
        cmd.extend(["--model", PI_MODEL])

    # Environment for the extension
    env = {**os.environ}
    env["PYLDON_IPC_DIR"] = "/workspace/ipc"
    env["PYLDON_GROUP_FOLDER"] = group_folder
    env["PYLDON_CHAT_JID"] = chat_jid
    env["PYLDON_IS_MAIN"] = "true" if is_main else "false"

    log(f"Spawning pi: provider={PI_PROVIDER}, model={PI_MODEL or 'default'}, group={group_folder}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd="/workspace/group",
        )
    except FileNotFoundError:
        return {
            "status": "error",
            "result": None,
            "error": f"pi binary not found at {PI_BINARY}",
            "tool_calls": [],
        }

    result_text = ""
    tool_calls: list[dict] = []
    error: str | None = None

    try:
        assert proc.stdin is not None and proc.stdout is not None
        rpc_msg: dict = {"type": "prompt", "message": prompt}
        if images:
            rpc_msg["images"] = [
                {"type": "image", "data": img["data"], "mimeType": img["mimeType"]}
                for img in images
            ]
            log(f"Sending prompt with {len(images)} image(s)")
        proc.stdin.write((json.dumps(rpc_msg) + "\n").encode())
        await proc.stdin.drain()

        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=300)
            if not line:
                break

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            if etype == "message_update":
                ame = event.get("assistantMessageEvent", {})
                if ame.get("type") == "text_delta":
                    result_text += ame.get("delta", "")

            elif etype == "tool_execution_start":
                tool_calls.append({
                    "id": event.get("toolCallId", ""),
                    "tool": event.get("toolName", ""),
                    "args": event.get("args", {}),
                    "status": "running",
                })
                log(f"Tool call: {event.get('toolName', '?')}")

            elif etype == "tool_execution_end":
                tid = event.get("toolCallId", "")
                for tc in tool_calls:
                    if tc["id"] == tid:
                        tc["status"] = "error" if event.get("isError") else "done"
                        # Extract result text from content array
                        result_parts = []
                        for part in (event.get("result") or {}).get("content", []):
                            if part.get("type") == "text":
                                result_parts.append(part["text"])
                        tc["result_preview"] = "".join(result_parts)[:500]
                        break

            elif etype == "response" and event.get("command") == "prompt":
                if not event.get("success"):
                    error = event.get("error", "Prompt failed")
                    break

            elif etype == "agent_end":
                break

    except asyncio.TimeoutError:
        error = "Timeout waiting for pi response"
        proc.kill()
    except Exception as e:
        error = str(e)
    finally:
        if proc.returncode is None:
            try:
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.write(json.dumps({"type": "abort"}).encode() + b"\n")
                    proc.stdin.close()
                await asyncio.wait_for(proc.wait(), timeout=10)
            except Exception:
                proc.kill()
                await proc.wait()

    # Log stderr tail
    stderr_text = ""
    if proc.stderr:
        stderr_bytes = await proc.stderr.read()
        stderr_text = stderr_bytes.decode(errors="replace").strip()
        for ln in stderr_text.splitlines()[-10:]:
            log(f"[pi] {ln}")

    if error:
        return {"status": "error", "result": None, "error": error, "tool_calls": tool_calls, "stderr": stderr_text}

    log(f"Done, result length={len(result_text)}, tool_calls={len(tool_calls)}")
    return {"status": "success", "result": result_text or None, "error": None, "tool_calls": tool_calls, "stderr": stderr_text}


async def main() -> None:
    try:
        input_data = json.loads(sys.stdin.read())
        log(f"Group: {input_data.get('groupFolder')}")
    except Exception as e:
        write_output({"status": "error", "result": None, "error": f"Bad input: {e}"})
        sys.exit(1)

    try:
        output = await run(input_data)
        write_output(output)
        if output["status"] == "error":
            sys.exit(1)
    except Exception as e:
        write_output({"status": "error", "result": None, "error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
