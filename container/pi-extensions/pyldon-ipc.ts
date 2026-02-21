/**
 * Pyldon IPC Tools Extension for pi.dev
 *
 * Registers custom tools that write JSON files to /workspace/ipc/
 * for the Pyldon host process to pick up.
 *
 * Configuration via environment variables:
 *   PYLDON_IPC_DIR       - IPC directory (default: /workspace/ipc)
 *   PYLDON_GROUP_FOLDER  - Current group folder name
 *   PYLDON_CHAT_JID      - Current Matrix room ID
 *   PYLDON_IS_MAIN       - "true" if this is the main admin group
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";
import * as fs from "node:fs";
import * as path from "node:path";
import * as crypto from "node:crypto";

// Read configuration from environment
const IPC_DIR = process.env.PYLDON_IPC_DIR || "/workspace/ipc";
const GROUP_FOLDER = process.env.PYLDON_GROUP_FOLDER || "unknown";
const CHAT_JID = process.env.PYLDON_CHAT_JID || "";
const IS_MAIN = process.env.PYLDON_IS_MAIN === "true";

function writeIpcFile(directory: string, data: Record<string, unknown>): string {
  fs.mkdirSync(directory, { recursive: true });
  const filename = `${Date.now()}-${crypto.randomBytes(3).toString("hex")}.json`;
  const filepath = path.join(directory, filename);
  const tmpPath = filepath + ".tmp";
  fs.writeFileSync(tmpPath, JSON.stringify(data, null, 2), "utf-8");
  fs.renameSync(tmpPath, filepath);
  return filename;
}

export default function (pi: ExtensionAPI) {
  // --- send_message ---
  pi.registerTool({
    name: "pyldon_send_message",
    label: "Send Message",
    description:
      "Send a message to the current Matrix room. Use this to communicate with the user, " +
      "especially during long tasks or scheduled tasks where results are not automatically sent.",
    parameters: Type.Object({
      text: Type.String({ description: "Message text to send" }),
    }),
    async execute(_toolCallId, params) {
      const data = {
        type: "message",
        chatJid: CHAT_JID,
        text: params.text,
        groupFolder: GROUP_FOLDER,
        timestamp: new Date().toISOString(),
      };
      const filename = writeIpcFile(path.join(IPC_DIR, "messages"), data);
      return {
        content: [{ type: "text" as const, text: `Message queued for delivery (${filename})` }],
        details: {},
      };
    },
  });

  // --- schedule_task ---
  pi.registerTool({
    name: "pyldon_schedule_task",
    label: "Schedule Task",
    description:
      'Schedule a recurring or one-time task. schedule_type: "cron" (e.g. "0 9 * * *"), ' +
      '"interval" (milliseconds, e.g. "300000" for 5min), "once" (ISO 8601 timestamp).',
    parameters: Type.Object({
      prompt: Type.String({ description: "Task prompt to execute" }),
      schedule_type: Type.Union([
        Type.Literal("cron"),
        Type.Literal("interval"),
        Type.Literal("once"),
      ]),
      schedule_value: Type.String({ description: "Cron expression, interval in ms, or ISO timestamp" }),
      context_mode: Type.Optional(
        Type.Union([Type.Literal("group"), Type.Literal("isolated")])
      ),
      target_group: Type.Optional(
        Type.String({ description: "Target group folder (main only)" })
      ),
    }),
    async execute(_toolCallId, params) {
      const effectiveTarget =
        IS_MAIN && params.target_group ? params.target_group : GROUP_FOLDER;

      const data = {
        type: "schedule_task",
        prompt: params.prompt,
        schedule_type: params.schedule_type,
        schedule_value: params.schedule_value,
        context_mode: params.context_mode || "group",
        groupFolder: effectiveTarget,
        chatJid: CHAT_JID,
        createdBy: GROUP_FOLDER,
        timestamp: new Date().toISOString(),
      };
      const filename = writeIpcFile(path.join(IPC_DIR, "tasks"), data);
      return {
        content: [
          {
            type: "text" as const,
            text: `Task scheduled (${filename}): ${params.schedule_type} - ${params.schedule_value}`,
          },
        ],
        details: {},
      };
    },
  });

  // --- list_tasks ---
  pi.registerTool({
    name: "pyldon_list_tasks",
    label: "List Tasks",
    description: "List all scheduled tasks visible to this group.",
    parameters: Type.Object({}),
    async execute() {
      const tasksFile = path.join(IPC_DIR, "current_tasks.json");
      if (!fs.existsSync(tasksFile)) {
        return {
          content: [{ type: "text" as const, text: "No scheduled tasks found." }],
          details: {},
        };
      }
      try {
        const allTasks = JSON.parse(fs.readFileSync(tasksFile, "utf-8"));
        const tasks = IS_MAIN
          ? allTasks
          : allTasks.filter((t: any) => t.groupFolder === GROUP_FOLDER);
        if (!tasks.length) {
          return {
            content: [{ type: "text" as const, text: "No scheduled tasks found." }],
            details: {},
          };
        }
        const formatted = tasks
          .map(
            (t: any) =>
              `- [${t.id}] ${(t.prompt || "").slice(0, 50)}... (${t.schedule_type}: ${t.schedule_value}) - ${t.status}, next: ${t.next_run || "N/A"}`
          )
          .join("\n");
        return {
          content: [{ type: "text" as const, text: `Scheduled tasks:\n${formatted}` }],
          details: {},
        };
      } catch (e: any) {
        return {
          content: [{ type: "text" as const, text: `Error reading tasks: ${e.message}` }],
          details: {},
        };
      }
    },
  });

  // --- pause_task ---
  pi.registerTool({
    name: "pyldon_pause_task",
    label: "Pause Task",
    description: "Pause a scheduled task by ID.",
    parameters: Type.Object({
      task_id: Type.String({ description: "Task ID to pause" }),
    }),
    async execute(_toolCallId, params) {
      writeIpcFile(path.join(IPC_DIR, "tasks"), {
        type: "pause_task",
        taskId: params.task_id,
        groupFolder: GROUP_FOLDER,
        isMain: IS_MAIN,
        timestamp: new Date().toISOString(),
      });
      return {
        content: [{ type: "text" as const, text: `Task ${params.task_id} pause requested.` }],
        details: {},
      };
    },
  });

  // --- resume_task ---
  pi.registerTool({
    name: "pyldon_resume_task",
    label: "Resume Task",
    description: "Resume a paused scheduled task by ID.",
    parameters: Type.Object({
      task_id: Type.String({ description: "Task ID to resume" }),
    }),
    async execute(_toolCallId, params) {
      writeIpcFile(path.join(IPC_DIR, "tasks"), {
        type: "resume_task",
        taskId: params.task_id,
        groupFolder: GROUP_FOLDER,
        isMain: IS_MAIN,
        timestamp: new Date().toISOString(),
      });
      return {
        content: [{ type: "text" as const, text: `Task ${params.task_id} resume requested.` }],
        details: {},
      };
    },
  });

  // --- cancel_task ---
  pi.registerTool({
    name: "pyldon_cancel_task",
    label: "Cancel Task",
    description: "Cancel and delete a scheduled task by ID.",
    parameters: Type.Object({
      task_id: Type.String({ description: "Task ID to cancel" }),
    }),
    async execute(_toolCallId, params) {
      writeIpcFile(path.join(IPC_DIR, "tasks"), {
        type: "cancel_task",
        taskId: params.task_id,
        groupFolder: GROUP_FOLDER,
        isMain: IS_MAIN,
        timestamp: new Date().toISOString(),
      });
      return {
        content: [{ type: "text" as const, text: `Task ${params.task_id} cancellation requested.` }],
        details: {},
      };
    },
  });

  // --- register_group (main only) ---
  pi.registerTool({
    name: "pyldon_register_group",
    label: "Register Group",
    description: "Register a new Matrix room as a group (main admin group only).",
    parameters: Type.Object({
      jid: Type.String({ description: "Matrix room ID (e.g. !abc:matrix.org)" }),
      name: Type.String({ description: "Display name for the group" }),
      folder: Type.String({ description: "Folder name under groups/" }),
      trigger: Type.String({ description: "Trigger pattern (e.g. @Assistant)" }),
    }),
    async execute(_toolCallId, params) {
      if (!IS_MAIN) {
        return {
          content: [{ type: "text" as const, text: "Only the main group can register new groups." }],
          details: {},
        };
      }
      writeIpcFile(path.join(IPC_DIR, "tasks"), {
        type: "register_group",
        jid: params.jid,
        name: params.name,
        folder: params.folder,
        trigger: params.trigger,
        timestamp: new Date().toISOString(),
      });
      return {
        content: [
          {
            type: "text" as const,
            text: `Group "${params.name}" registered. It will start receiving messages immediately.`,
          },
        ],
        details: {},
      };
    },
  });

  // --- refresh_groups (main only) ---
  pi.registerTool({
    name: "pyldon_refresh_groups",
    label: "Refresh Groups",
    description: "Request a refresh of available Matrix rooms (main admin group only).",
    parameters: Type.Object({}),
    async execute() {
      if (!IS_MAIN) {
        return {
          content: [{ type: "text" as const, text: "Only the main group can refresh groups." }],
          details: {},
        };
      }
      writeIpcFile(path.join(IPC_DIR, "tasks"), {
        type: "refresh_groups",
        timestamp: new Date().toISOString(),
      });
      return {
        content: [
          {
            type: "text" as const,
            text: "Group refresh requested. Check available_groups.json shortly.",
          },
        ],
        details: {},
      };
    },
  });
}
