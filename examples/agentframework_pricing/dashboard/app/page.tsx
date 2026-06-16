"use client";

import { ExternalLink } from "lucide-react";
import { AgentComparison } from "@/components/AgentComparison";
import { ConnectionStatus } from "@/components/ConnectionStatus";
import { ControlPanel } from "@/components/ControlPanel";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { EventLog } from "@/components/EventLog";
import { RevokInspector } from "@/components/RevokInspector";
import { StatusStrip } from "@/components/StatusStrip";
import { useAgUiRun } from "@/hooks/useAgUiRun";
import { useSSE } from "@/hooks/useSSE";

const DEVUI_URL = process.env.NEXT_PUBLIC_DEVUI_URL ?? "http://localhost:8082";

export default function Page() {
  const { state, status } = useSSE();
  const { state: agUiState, run: agUiRun, reset: agUiReset } = useAgUiRun();

  return (
    <main className="min-h-screen bg-[#0f1117] px-4 py-6 md:px-8 md:py-8">
      <div className="mx-auto max-w-[1200px] space-y-6">

        {/* ── Header ──────────────────────────────────────────────── */}
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl font-bold text-slate-50">
              Revok Demo
            </h1>
            <p className="text-sm text-slate-400 mt-0.5">
              Stale-memory detection · Redis AMS · Revok proxy · Azure OpenAI
            </p>
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            {/* AF DevUI link — token is DEVUI_AUTH_TOKEN env var (default: revok-demo) */}
            <a
              href={DEVUI_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 rounded-md border border-sky-500/40 bg-sky-500/10 px-3 py-1.5 text-xs font-medium text-sky-300 hover:bg-sky-500/20 transition-colors"
              title={`AF DevUI — auth token: ${process.env.NEXT_PUBLIC_DEVUI_AUTH_HINT ?? "revok-demo"}`}
            >
              <ExternalLink className="h-3.5 w-3.5" />
              AF DevUI
            </a>
            <ConnectionStatus status={status} state={state} />
          </div>
        </div>

        {/* ── Live status strip ───────────────────────────────────── */}
        <ErrorBoundary label="Status">
          <StatusStrip state={state} />
        </ErrorBoundary>

        {/* ── Demo controls ───────────────────────────────────────── */}
        <ErrorBoundary label="Controls">
          <ControlPanel
            state={state}
            onAskAgent={agUiRun}
            onReset={agUiReset}
            agUiRunning={agUiState.running}
          />
        </ErrorBoundary>

        {/* ── Hero: agent comparison ──────────────────────────────── */}
        <ErrorBoundary label="Agent Comparison">
          <AgentComparison state={state} agUiState={agUiState} />
        </ErrorBoundary>

        {/* ── Revok Inspector ─────────────────────────────────────── */}
        <ErrorBoundary label="Revok Inspector">
          <RevokInspector state={state} />
        </ErrorBoundary>

        {/* ── Event log ───────────────────────────────────────────── */}
        <ErrorBoundary label="Event Log">
          <EventLog entries={state?.event_log ?? []} />
        </ErrorBoundary>

      </div>
    </main>
  );
}
