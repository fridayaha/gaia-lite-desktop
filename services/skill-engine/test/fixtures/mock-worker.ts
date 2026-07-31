/**
 * Mock engine worker for testing EngineInstanceManager.
 *
 * Simulates a child process that communicates via stdin/stdout JSONL,
 * without requiring the real pi SDK.
 *
 * Usage: spawn("node", [pathToThisFile, "--role", "dev", "--cwd", "/tmp/test"])
 */

import { createInterface } from "node:readline";
import { parseArgs } from "node:util";

const { values } = parseArgs({
  options: {
    cwd: { type: "string" },
    role: { type: "string", default: "dev" },
  },
  strict: false, // Accept extra args without error
});

const role = values.role ?? "dev";

function send(obj: Record<string, unknown>): void {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

// Announce ready
send({ type: "status", status: "initializing", role });
setTimeout(() => {
  send({ type: "status", status: "ready", role });
}, 10); // Small delay to simulate SDK init

// Heartbeat
const heartbeat = setInterval(() => {
  send({ type: "heartbeat", ts: Date.now() });
}, 30_000);

// Command handling
const rl = createInterface({ input: process.stdin });

rl.on("line", (line) => {
  if (!line.trim()) return;
  try {
    const cmd = JSON.parse(line);
    const { type, id, message } = cmd;

    switch (type) {
      case "prompt":
        // Simulate a realistic turn: text message, then a tool call, then
        // turn_end. Uses the real pi SDK event shapes so persistence logic
        // (which reads data.message.content[] / tool_execution_*) is tested
        // against the same shapes production emits.
        send({ type: "event", eventType: "turn_start", data: {} });
        send({
          type: "event",
          eventType: "message_start",
          data: { message: { content: [] } },
        });
        send({
          type: "event",
          eventType: "message_update",
          data: {
            message: {
              content: [{ type: "text", text: `Echo: ${message}` }],
            },
          },
        });
        // Second update carries the accumulated text (real SDK semantics) —
        // persistence must replace the text part, not write a second row.
        send({
          type: "event",
          eventType: "message_update",
          data: {
            message: {
              content: [{ type: "text", text: `Echo: ${message} done` }],
            },
          },
        });
        send({ type: "event", eventType: "message_end", data: {} });
        send({
          type: "event",
          eventType: "tool_execution_start",
          data: {
            toolCallId: "tc-mock-1",
            toolName: "ls",
            args: { path: "." },
          },
        });
        send({
          type: "event",
          eventType: "tool_execution_end",
          data: {
            toolCallId: "tc-mock-1",
            toolName: "ls",
            result: { content: [{ type: "text", text: "file.txt" }] },
            isError: false,
          },
        });
        send({ type: "event", eventType: "turn_end", data: {} });
        send({ type: "response", id, command: "prompt", success: true });
        break;

      case "steer":
        send({ type: "response", id, command: "steer", success: true });
        break;

      case "follow_up":
        send({ type: "response", id, command: "follow_up", success: true });
        break;

      case "abort":
        send({ type: "response", id, command: "abort", success: true });
        break;

      case "reload":
        send({ type: "response", id, command: "reload", success: true });
        break;

      case "tool_response":
        // Mock has no real pending clarify; just ack the round-trip so the
        // manager-level plumbing (stdin write + response matching) is tested.
        send({ type: "response", id, command: "tool_response", success: true });
        break;

      case "get_state":
        send({
          type: "response",
          id,
          command: "get_state",
          success: true,
          data: {
            isStreaming: false,
            model: { provider: "mock", modelId: "mock-v1" },
            messageCount: 1,
          },
        });
        break;

      default:
        send({
          type: "response",
          id,
          command: type,
          success: false,
          error: `Unknown command: ${type}`,
        });
    }
  } catch (err) {
    send({
      type: "error",
      error: `Parse error: ${(err as Error).message}`,
    });
  }
});

rl.on("close", () => {
  clearInterval(heartbeat);
  process.exit(0);
});

process.on("SIGTERM", () => {
  clearInterval(heartbeat);
  send({ type: "status", status: "shutting_down" });
  process.exit(0);
});
