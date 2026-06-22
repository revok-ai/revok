"use client";

import { useEffect, useRef } from "react";
import { Brain, CheckCircle2, Loader2, Database, Shield, Search, Zap } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AgUiRunState, ToolCallRecord } from "@/hooks/useAgUiRun";

// ---------------------------------------------------------------------------
// Tool metadata
// ---------------------------------------------------------------------------

interface ToolMeta {
  label: string;
  description: string;
  Icon: React.ComponentType<{ className?: string }>;
}

const TOOL_META: Record<string, ToolMeta> = {
  check_memory: {
    label: "Search Memory",
    description: "Redis AMS",
    Icon: Search,
  },
  check_memory_without_revok: {
    label: "Search Memory",
    description: "Redis AMS",
    Icon: Search,
  },
  check_memory_with_revok: {
    label: "Search Memory",
    description: "Redis AMS",
    Icon: Search,
  },
  get_revok_confidence: {
    label: "Revok Confidence",
    description: "Revok proxy",
    Icon: Shield,
  },
  get_current_entitlements: {
    label: "Fetch Live Entitlements",
    description: "SQLite",
    Icon: Database,
  },
};

const CONFIDENCE_PILL: Record<string, string> = {
  fresh:
    "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
  degraded:
    "border-amber-500/30 bg-amber-500/10 text-amber-400",
  stale:
    "border-red-500/30 bg-red-500/10 text-red-400",
  unknown:
    "border-slate-500/30 bg-slate-500/10 text-slate-400",
};

// ---------------------------------------------------------------------------
// ToolCallCard
// ---------------------------------------------------------------------------

function ToolCallCard({ tc }: { tc: ToolCallRecord }) {
  const meta = TOOL_META[tc.name] ?? { label: tc.name, description: "", Icon: Zap };
  const { label, description, Icon } = meta;
  const running = tc.status === "running";

  let argsDisplay = "";
  try {
    const parsed = JSON.parse(tc.argsJson);
    // Show the first value only (e.g. product_name or entity_key)
    const vals = Object.values(parsed as Record<string, unknown>);
    argsDisplay = vals.length ? String(vals[0]) : tc.argsJson;
  } catch {
    argsDisplay = tc.argsJson;
  }

  let resultNode: React.ReactNode = null;
  if (tc.customData && tc.status === "done") {
    if (tc.name === "check_memory") {
      const count = (tc.customData.memory_count as number) ?? 0;
      const quote = (tc.customData.memory_quote as string) ?? "";
      resultNode = (
        <div className="mt-1 space-y-0.5">
          <span className="text-[11px] text-emerald-400">
            {count} memor{count === 1 ? "y" : "ies"} found
          </span>
          {quote ? (
            <p className="text-[11px] text-slate-400 italic line-clamp-2">
              &ldquo;{quote.slice(0, 120)}{quote.length > 120 ? "…" : ""}&rdquo;
            </p>
          ) : (
            <p className="text-[11px] text-slate-500 italic">No memory — load memory first.</p>
          )}
        </div>
      );
    } else if (tc.name === "get_revok_confidence") {
      const score = tc.customData.score as number | null;
      const status = (tc.customData.status as string) ?? "unknown";
      const signals = (tc.customData.signal_count as number) ?? 0;
      const pillClass = CONFIDENCE_PILL[status] ?? CONFIDENCE_PILL.unknown;
      resultNode = (
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <span className={`rounded-full border px-1.5 py-0.5 text-[10px] font-semibold ${pillClass}`}>
            {status.toUpperCase()}
          </span>
          {score !== null && score !== undefined && (
            <span className="text-[11px] text-slate-400">score: {score.toFixed(2)}</span>
          )}
          <span className="text-[11px] text-slate-400">signals: {signals}</span>
        </div>
      );
    } else if (tc.name === "get_current_entitlements") {
      const tier = tc.customData.subscription_tier as string | null;
      const seats = tc.customData.seat_limit as number | null;
      resultNode = (
        <div className="mt-1 space-y-0.5">
          <p className="text-[11px] text-sky-300 font-medium">
            Tier: {tier ?? "unknown"}{seats !== null && seats !== undefined ? ` · ${seats} seats` : ""}
          </p>
          <p className="text-[10px] text-slate-500 italic">re-verified from database</p>
        </div>
      );
    }
  }

  return (
    <div className="flex gap-2.5 py-2.5 border-b border-[#2a2d3a] last:border-0">
      <div className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center">
        {running ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-400" />
        ) : (
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 flex-wrap">
          <Icon className="h-3 w-3 shrink-0 text-slate-400" />
          <span className="text-[11px] font-semibold text-slate-200">{label}</span>
          <span className="text-[10px] text-slate-500">{description}</span>
        </div>
        {argsDisplay && (
          <p className="mt-0.5 font-mono text-[10px] text-slate-600 truncate">{argsDisplay}</p>
        )}
        {resultNode}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AgentTrace — sticky sidebar panel
// ---------------------------------------------------------------------------

interface AgentTraceProps {
  runState: AgUiRunState;
}

export function AgentTrace({ runState }: AgentTraceProps) {
  const {
    running,
    runId,
    toolCalls,
    streamingText,
    finalAnswer,
    withoutRevokAnswer,
    snapshot,
    error,
  } = runState;

  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom as new events arrive
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [toolCalls.length, streamingText, finalAnswer]);

  const displayText = finalAnswer || streamingText;
  const isStreaming = running && !finalAnswer;
  const confidenceStatus = (snapshot?.confidence_status as string | undefined) ?? null;
  const reVerified = Boolean(snapshot?.re_verified);
  const pillClass = confidenceStatus
    ? CONFIDENCE_PILL[confidenceStatus] ?? CONFIDENCE_PILL.unknown
    : null;

  return (
    <Card className="flex flex-col" style={{ maxHeight: "calc(100vh - 3rem)" }}>
      {/* Header */}
      <CardHeader className="shrink-0 pb-2 border-b border-[#2a2d3a]">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Brain className="h-4 w-4 text-slate-400" />
            Agent Reasoning
            <span className="rounded border border-violet-500/30 bg-violet-500/10 px-1.5 py-0.5 text-[10px] text-violet-300 font-medium">
              AG-UI
            </span>
          </CardTitle>

          <div className="flex items-center gap-1.5">
            {confidenceStatus && pillClass && (
              <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${pillClass}`}>
                {confidenceStatus}{reVerified ? " ✓" : ""}
              </span>
            )}
            {running ? (
              <span className="flex items-center gap-1 rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] text-amber-400">
                <Loader2 className="h-2.5 w-2.5 animate-spin" /> live
              </span>
            ) : runId ? (
              <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] text-emerald-400">
                ✓ done
              </span>
            ) : null}
          </div>
        </div>
      </CardHeader>

      {/* Scrollable body */}
      <CardContent ref={scrollRef} className="flex-1 overflow-y-auto pt-3 pb-3 space-y-3 min-h-0">

        {/* Empty state */}
        {!running && toolCalls.length === 0 && !finalAnswer && !error && (
          <div className="space-y-3">
            <p className="text-xs text-slate-500 italic">
              Click <span className="not-italic text-slate-300 font-medium">Ask Agent</span> to
              stream the live tool-call trace here via the{" "}
              <span className="not-italic text-violet-400">AG-UI</span> open protocol.
            </p>
            <div className="space-y-1.5">
              {Object.entries(TOOL_META).map(([key, meta]) => (
                <div
                  key={key}
                  className="flex items-center gap-2 rounded-md border border-[#2a2d3a] bg-[#1a1d27] px-2.5 py-2"
                >
                  <meta.Icon className="h-3.5 w-3.5 shrink-0 text-slate-500" />
                  <div>
                    <p className="text-[11px] font-medium text-slate-300">{meta.label}</p>
                    <p className="text-[10px] text-slate-500">{meta.description}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Tool call timeline — WITH REVOK */}
        {toolCalls.length > 0 && (
          <div>
            <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-slate-500">
              WITH REVOK · tool calls
            </p>
            <div className="rounded-md border border-[#2a2d3a] bg-[#1a1d27]/50 px-2.5">
              {toolCalls.map((tc) => (
                <ToolCallCard key={tc.id} tc={tc} />
              ))}
            </div>
          </div>
        )}

        {/* LLM answer — WITH REVOK */}
        {(displayText || isStreaming) && (
          <div>
            <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-slate-500">
              WITH REVOK · answer
            </p>
            <div className="rounded-md border border-[#2a2d3a] bg-[#1a1d27] p-3">
              <p className="text-xs text-slate-200 leading-relaxed">
                {displayText}
                {isStreaming && (
                  <span className="ml-0.5 inline-block h-[0.9em] w-[2px] animate-pulse bg-slate-300 align-middle" />
                )}
              </p>
            </div>
          </div>
        )}

        {/* WITHOUT REVOK answer (delivered via CUSTOM event) */}
        {withoutRevokAnswer && (
          <div>
            <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-slate-500">
              WITHOUT REVOK · answer
            </p>
            <div className="rounded-md border border-[#2a2d3a] bg-[#1a1d27] p-3">
              <p className="text-xs text-slate-400 leading-relaxed">
                {(withoutRevokAnswer.answer as string) ?? ""}
              </p>
            </div>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="rounded-md border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-300">
            Error: {error}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
