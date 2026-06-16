"use client";

import { CheckCircle2, Loader2, XCircle, Database, Shield, RefreshCw } from "lucide-react";
import { useTypewriter } from "@/hooks/useTypewriter";
import type { AgUiRunState } from "@/hooks/useAgUiRun";
import type { ConfidenceStatus, DemoState } from "@/types/state";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const CONFIDENCE_COLORS: Record<
  ConfidenceStatus,
  { pill: string; dot: string }
> = {
  fresh:    { pill: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300", dot: "bg-emerald-400" },
  degraded: { pill: "border-amber-500/30 bg-amber-500/10 text-amber-300",     dot: "bg-amber-400" },
  stale:    { pill: "border-red-500/30 bg-red-500/10 text-red-300",           dot: "bg-red-400" },
  unknown:  { pill: "border-slate-600 bg-slate-700/30 text-slate-400",        dot: "bg-slate-500" },
};

function AnswerText({
  text,
  streaming,
  placeholder,
}: {
  text: string | null | undefined;
  streaming?: boolean;
  placeholder: string;
}) {
  const displayed = useTypewriter(text ?? "");
  const isTyping = !!text && displayed.length < text.length;

  if (!text && !streaming) {
    return <p className="text-sm text-slate-500 italic">{placeholder}</p>;
  }
  return (
    <p className="text-sm text-slate-200 leading-relaxed min-h-[3.5rem]">
      {displayed || ""}
      {(isTyping || streaming) && (
        <span className="ml-0.5 inline-block h-[0.9em] w-[2px] animate-pulse bg-slate-300 align-middle" />
      )}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Revok Verdict — what Revok did (shown in the WITH REVOK panel)
// ---------------------------------------------------------------------------

function RevokVerdict({
  status,
  score,
  signalCount,
  reVerified,
  livePrice,
  running,
}: {
  status: ConfidenceStatus | null;
  score: number | null;
  signalCount: number;
  reVerified: boolean;
  livePrice: number | null;
  running: boolean;
}) {
  if (running) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-[#2a2d3a] bg-[#1a1d27] px-3 py-2">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-violet-400 shrink-0" />
        <span className="text-xs text-slate-400">Revok checking signals…</span>
      </div>
    );
  }

  if (!status || status === "unknown") {
    return (
      <div className="flex items-center gap-2 rounded-md border border-[#2a2d3a] bg-[#1a1d27] px-3 py-2">
        <Shield className="h-3.5 w-3.5 text-slate-500 shrink-0" />
        <span className="text-xs text-slate-500">
          Ask a question to see Revok&apos;s verdict
        </span>
      </div>
    );
  }

  const c = CONFIDENCE_COLORS[status];

  return (
    <div className={`rounded-md border px-3 py-2.5 space-y-2 ${c.pill}`}>
      {/* Status line */}
      <div className="flex items-center gap-2">
        <span className={`inline-block h-2 w-2 rounded-full shrink-0 ${c.dot}`} />
        <span className="text-xs font-semibold uppercase tracking-wide">
          Memory was {status}
        </span>
        {score !== null && (
          <span className="ml-auto text-xs tabular-nums opacity-70">
            score {score.toFixed(2)}
          </span>
        )}
        {signalCount > 0 && (
          <span className="text-[10px] opacity-70">
            · {signalCount} signal{signalCount !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {/* Verdict */}
      {status === "fresh" && (
        <div className="flex items-start gap-1.5 text-xs opacity-80">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 mt-px" />
          <span>No external signals — answered confidently from memory</span>
        </div>
      )}

      {status === "degraded" && (
        <div className="flex items-start gap-1.5 text-xs opacity-80">
          <Shield className="h-3.5 w-3.5 shrink-0 mt-px" />
          <span>
            Signals detected — answered from memory with a staleness caveat
          </span>
        </div>
      )}

      {status === "stale" && reVerified && livePrice !== null && (
        <div className="flex items-start gap-1.5 text-xs opacity-90">
          <Database className="h-3.5 w-3.5 shrink-0 mt-px" />
          <span>
            Memory stale — queried live database.{" "}
            <span className="font-semibold">${livePrice.toFixed(0)}/mo</span> confirmed.
          </span>
        </div>
      )}

      {status === "stale" && !reVerified && (
        <div className="flex items-start gap-1.5 text-xs opacity-80">
          <RefreshCw className="h-3.5 w-3.5 shrink-0 mt-px" />
          <span>Memory stale — attempted re-verification</span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export interface AgentComparisonProps {
  state: DemoState | null;
  agUiState: AgUiRunState;
}

export function AgentComparison({ state, agUiState }: AgentComparisonProps) {
  const { running, streamingText, finalAnswer, withoutRevokAnswer } = agUiState;

  // WITHOUT REVOK — prefer CUSTOM event (arrives faster) then SSE state
  const withoutAnswer: string | null | undefined =
    (withoutRevokAnswer?.answer as string | undefined) ??
    state?.answer_without_revok?.answer;

  // WITH REVOK — streaming first, then final from AG-UI, then SSE state
  const withAnswer: string | null | undefined =
    finalAnswer || streamingText || state?.answer_with_revok?.answer;

  // Revok verdict — from SSE answer (populated after run) or current live state
  const withRevokData = state?.answer_with_revok;
  const confidenceStatus: ConfidenceStatus =
    (withRevokData?.confidence_status as ConfidenceStatus | undefined) ??
    state?.confidence_status ??
    "unknown";
  const confidenceScore =
    withRevokData?.confidence_score ?? state?.confidence_score ?? null;
  const signalCount =
    withRevokData?.signal_count ?? state?.signal_count ?? 0;
  const reVerified = Boolean(withRevokData?.re_verified);
  const livePrice = withRevokData?.live_price ?? null;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {/* ── WITHOUT REVOK ──────────────────────────────────────── */}
      <div className="rounded-xl border border-[#2a2d3a] bg-[#13161e] p-5 space-y-4 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <XCircle className="h-4 w-4 text-red-400 shrink-0" />
            <span className="text-sm font-semibold text-slate-200">
              Without Revok
            </span>
          </div>
          <span className="rounded border border-red-500/30 bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-300 uppercase tracking-wide">
            No signal check
          </span>
        </div>

        <div className="h-px bg-[#2a2d3a]" />

        {/* Answer */}
        <div className="flex-1 space-y-2">
          <p className="text-[10px] font-medium uppercase tracking-wider text-slate-500">
            Agent answer
          </p>
          <AnswerText
            text={withoutAnswer}
            placeholder="Ask a question to see what the agent says without signal checks"
          />
        </div>

        {/* Warning footer */}
        <div className="flex items-start gap-2 rounded-md border border-red-500/20 bg-red-500/5 px-3 py-2">
          <XCircle className="h-3.5 w-3.5 text-red-400 shrink-0 mt-px" />
          <p className="text-xs text-red-300/80 leading-snug">
            Trusts memory blindly — no check for stale data. Will give the
            wrong price if the database changed.
          </p>
        </div>
      </div>

      {/* ── WITH REVOK ─────────────────────────────────────────── */}
      <div className="rounded-xl border border-[#2a2d3a] bg-[#13161e] p-5 space-y-4 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
            <span className="text-sm font-semibold text-slate-200">
              With Revok
            </span>
          </div>
          {running ? (
            <span className="flex items-center gap-1 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] text-amber-400">
              <Loader2 className="h-2.5 w-2.5 animate-spin" /> thinking…
            </span>
          ) : (
            <span className="rounded border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300 uppercase tracking-wide">
              Signal-aware
            </span>
          )}
        </div>

        <div className="h-px bg-[#2a2d3a]" />

        {/* Revok verdict */}
        <div className="space-y-1.5">
          <p className="text-[10px] font-medium uppercase tracking-wider text-slate-500">
            Revok verdict
          </p>
          <RevokVerdict
            status={confidenceStatus}
            score={confidenceScore}
            signalCount={signalCount}
            reVerified={reVerified}
            livePrice={livePrice}
            running={running}
          />
        </div>

        {/* Answer */}
        <div className="flex-1 space-y-2">
          <p className="text-[10px] font-medium uppercase tracking-wider text-slate-500">
            Agent answer
          </p>
          <AnswerText
            text={withAnswer}
            streaming={running && !!streamingText}
            placeholder="Ask a question to see Revok verify memory before answering"
          />
        </div>

        {/* Outcome footer */}
        {reVerified ? (
          <div className="flex items-start gap-2 rounded-md border border-sky-500/30 bg-sky-500/10 px-3 py-2">
            <CheckCircle2 className="h-3.5 w-3.5 text-sky-400 shrink-0 mt-px" />
            <p className="text-xs text-sky-300/90 leading-snug">
              Memory was stale — Revok triggered a live database lookup before
              answering. ✓
            </p>
          </div>
        ) : confidenceStatus === "degraded" ? (
          <div className="flex items-start gap-2 rounded-md border border-amber-500/20 bg-amber-500/5 px-3 py-2">
            <Shield className="h-3.5 w-3.5 text-amber-400 shrink-0 mt-px" />
            <p className="text-xs text-amber-300/80 leading-snug">
              Signals detected — Revok flagged the answer with a staleness caveat.
            </p>
          </div>
        ) : confidenceStatus === "fresh" ? (
          <div className="flex items-start gap-2 rounded-md border border-emerald-500/20 bg-emerald-500/5 px-3 py-2">
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0 mt-px" />
            <p className="text-xs text-emerald-300/80 leading-snug">
              Memory verified — no signals detected, answered confidently. ✓
            </p>
          </div>
        ) : (
          <div className="flex items-start gap-2 rounded-md border border-emerald-500/20 bg-emerald-500/5 px-3 py-2">
            <Shield className="h-3.5 w-3.5 text-emerald-400 shrink-0 mt-px" />
            <p className="text-xs text-emerald-300/80 leading-snug">
              Checks signals before answering — never trusts stale
              data.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
