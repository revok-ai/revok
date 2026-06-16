"use client";

import { useState, useRef, useCallback } from "react";
import { API_URL } from "@/lib/api";
import type { AgUiEvent } from "@/types/agui";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface ToolCallRecord {
  id: string;
  name: string;
  /** Accumulated TOOL_CALL_ARGS delta strings (forms a valid JSON string when done). */
  argsJson: string;
  status: "running" | "done";
  /** Populated from the matching CUSTOM event once the tool call is done. */
  customData?: Record<string, unknown>;
}

export interface AgUiRunState {
  running: boolean;
  runId: string | null;
  toolCalls: ToolCallRecord[];
  /** Text accumulated from TEXT_MESSAGE_CONTENT deltas (clears between runs). */
  streamingText: string;
  /** Full answer text once TEXT_MESSAGE_END is received. */
  finalAnswer: string;
  /** STATE_SNAPSHOT payload from the last run. */
  snapshot: Record<string, unknown> | null;
  /** Without-revok answer delivered via CUSTOM without_revok_answer. */
  withoutRevokAnswer: Record<string, unknown> | null;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

const INITIAL_STATE: AgUiRunState = {
  running: false,
  runId: null,
  toolCalls: [],
  streamingText: "",
  finalAnswer: "",
  snapshot: null,
  withoutRevokAnswer: null,
  error: null,
};

/**
 * Consumes AG-UI protocol events from `POST /v1/runs` and exposes reactive
 * state for the AgentTrace component.
 *
 * The server streams newline-delimited SSE (`data: {...}\n\n`) where every
 * event conforms to the AG-UI open spec.
 */
export function useAgUiRun() {
  const [state, setState] = useState<AgUiRunState>(INITIAL_STATE);
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);

  const reset = useCallback((): void => {
    if (readerRef.current) {
      try { readerRef.current.cancel(); } catch { /* ignore */ }
      readerRef.current = null;
    }
    setState(INITIAL_STATE);
  }, []);

  const run = useCallback(async (question: string): Promise<void> => {
    // Cancel any in-flight run
    if (readerRef.current) {
      try { readerRef.current.cancel(); } catch { /* ignore */ }
      readerRef.current = null;
    }

    // Reset state for new run
    setState({
      ...INITIAL_STATE,
      running: true,
    });

    try {
      const res = await fetch(`${API_URL}/v1/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: [{ role: "user", content: question }],
          threadId: "demo-with-revok",
          runId: crypto.randomUUID(),
        }),
      });

      if (!res.ok || !res.body) {
        setState((s) => ({
          ...s,
          running: false,
          error: `HTTP ${res.status} ${res.statusText}`,
        }));
        return;
      }

      const reader = res.body.getReader();
      readerRef.current = reader;
      const decoder = new TextDecoder();
      let buf = "";

      // Inline reducer so it captures no stale state via closure
      const applyEvent = (event: AgUiEvent): void => {
        setState((s) => {
          switch (event.type) {
            case "RUN_STARTED":
              return { ...s, runId: event.runId, running: true };

            case "TOOL_CALL_START":
              return {
                ...s,
                toolCalls: [
                  ...s.toolCalls,
                  {
                    id: event.toolCallId,
                    name: event.toolCallName,
                    argsJson: "",
                    status: "running",
                  },
                ],
              };

            case "TOOL_CALL_ARGS":
              return {
                ...s,
                toolCalls: s.toolCalls.map((tc) =>
                  tc.id === event.toolCallId
                    ? { ...tc, argsJson: tc.argsJson + event.delta }
                    : tc
                ),
              };

            case "TOOL_CALL_END":
              return {
                ...s,
                toolCalls: s.toolCalls.map((tc) =>
                  tc.id === event.toolCallId ? { ...tc, status: "done" } : tc
                ),
              };

            case "CUSTOM": {
              // Map custom event name → tool name so we can attach result data
              const toolNameByEvent: Record<string, string> = {
                memory_loaded: "check_memory",
                revok_confidence: "get_revok_confidence",
                db_reverified: "get_current_price",
              };
              const targetTool = toolNameByEvent[event.name];
              if (targetTool) {
                // Attach to the most-recent matching tool call that is done
                const idx = [...s.toolCalls]
                  .reverse()
                  .findIndex((tc) => tc.name === targetTool && tc.status === "done");
                if (idx !== -1) {
                  const realIdx = s.toolCalls.length - 1 - idx;
                  const updated = s.toolCalls.map((tc, i) =>
                    i === realIdx
                      ? { ...tc, customData: event.value as Record<string, unknown> }
                      : tc
                  );
                  return { ...s, toolCalls: updated };
                }
              }
              if (event.name === "without_revok_answer") {
                return { ...s, withoutRevokAnswer: event.value as Record<string, unknown> };
              }
              return s;
            }

            case "TEXT_MESSAGE_CONTENT":
              return { ...s, streamingText: s.streamingText + event.delta };

            case "TEXT_MESSAGE_END":
              return { ...s, finalAnswer: s.streamingText };

            case "STATE_SNAPSHOT":
              return { ...s, snapshot: event.snapshot };

            case "RUN_FINISHED":
              return { ...s, running: false };

            case "RUN_ERROR":
              return { ...s, running: false, error: event.message };

            default:
              return s;
          }
        });
      }

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;
          try {
            applyEvent(JSON.parse(raw) as AgUiEvent);
          } catch {
            /* skip malformed events */
          }
        }
      }
      // Flush any remaining buffer content (edge case)
      if (buf.startsWith("data: ")) {
        try {
          applyEvent(JSON.parse(buf.slice(6).trim()) as AgUiEvent);
        } catch { /* ignore */ }
      }
    } catch (exc) {
      setState((s) => ({ ...s, running: false, error: String(exc) }));
    } finally {
      readerRef.current = null;
      // Ensure running is cleared even if RUN_FINISHED wasn't received
      setState((s) => (s.running ? { ...s, running: false } : s));
    }
  }, []);

  return { state, run, reset };
}
