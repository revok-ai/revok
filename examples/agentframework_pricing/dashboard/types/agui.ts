/**
 * AG-UI Protocol event type definitions.
 * Matches the open spec at https://docs.ag-ui.com/concepts/events
 *
 * No external package needed — we define the types inline so the
 * dashboard stays dependency-light and the spec stays explicit.
 */

// ---------------------------------------------------------------------------
// Event type literal union
// ---------------------------------------------------------------------------

export type AgUiEventType =
  | "RUN_STARTED"
  | "RUN_FINISHED"
  | "RUN_ERROR"
  | "TEXT_MESSAGE_START"
  | "TEXT_MESSAGE_CONTENT"
  | "TEXT_MESSAGE_END"
  | "TOOL_CALL_START"
  | "TOOL_CALL_ARGS"
  | "TOOL_CALL_END"
  | "STATE_SNAPSHOT"
  | "STATE_DELTA"
  | "CUSTOM";

// ---------------------------------------------------------------------------
// Per-event interfaces
// ---------------------------------------------------------------------------

export interface RunStartedEvent {
  type: "RUN_STARTED";
  threadId: string;
  runId: string;
}

export interface RunFinishedEvent {
  type: "RUN_FINISHED";
  threadId: string;
  runId: string;
}

export interface RunErrorEvent {
  type: "RUN_ERROR";
  message: string;
}

export interface TextMessageStartEvent {
  type: "TEXT_MESSAGE_START";
  messageId: string;
  role: string;
}

export interface TextMessageContentEvent {
  type: "TEXT_MESSAGE_CONTENT";
  messageId: string;
  delta: string;
}

export interface TextMessageEndEvent {
  type: "TEXT_MESSAGE_END";
  messageId: string;
}

export interface ToolCallStartEvent {
  type: "TOOL_CALL_START";
  toolCallId: string;
  toolCallName: string;
  parentMessageId: string | null;
}

export interface ToolCallArgsEvent {
  type: "TOOL_CALL_ARGS";
  toolCallId: string;
  /** Streamed JSON fragment of the tool arguments. */
  delta: string;
}

export interface ToolCallEndEvent {
  type: "TOOL_CALL_END";
  toolCallId: string;
}

export interface StateSnapshotEvent {
  type: "STATE_SNAPSHOT";
  snapshot: Record<string, unknown>;
}

export interface StateDeltaEvent {
  type: "STATE_DELTA";
  delta: unknown;
}

export interface CustomEvent {
  type: "CUSTOM";
  name: string;
  value: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Discriminated union
// ---------------------------------------------------------------------------

export type AgUiEvent =
  | RunStartedEvent
  | RunFinishedEvent
  | RunErrorEvent
  | TextMessageStartEvent
  | TextMessageContentEvent
  | TextMessageEndEvent
  | ToolCallStartEvent
  | ToolCallArgsEvent
  | ToolCallEndEvent
  | StateSnapshotEvent
  | StateDeltaEvent
  | CustomEvent;
