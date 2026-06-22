"use client";

import { AgentAnswer } from "@/components/AgentAnswer";
import { ConnectionStatus } from "@/components/ConnectionStatus";
import { ControlPanel } from "@/components/ControlPanel";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { EventLog } from "@/components/EventLog";
import { RevokInspector } from "@/components/RevokInspector";
import { StatusStrip } from "@/components/StatusStrip";
import { useSSE } from "@/hooks/useSSE";

export default function Page() {
  const { state, status } = useSSE();

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
              Stale-memory detection · Mem0 · Revok proxy · CrewAI agents
            </p>
          </div>
          <ConnectionStatus status={status} state={state} />
        </div>

        {/* ── Live status strip ───────────────────────────────────── */}
        <ErrorBoundary label="Status">
          <StatusStrip state={state} />
        </ErrorBoundary>

        {/* ── Demo controls ───────────────────────────────────────── */}
        <ErrorBoundary label="Control Panel">
          <ControlPanel state={state} />
        </ErrorBoundary>

        {/* ── Agent answers ──────────────────────────────────────── */}
        <ErrorBoundary label="Agent Answer">
          <AgentAnswer state={state} />
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
