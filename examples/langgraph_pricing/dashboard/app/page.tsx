"use client";

import { AgentAnswer } from "@/components/AgentAnswer";
import { AgentMemory } from "@/components/AgentMemory";
import { ConnectionStatus } from "@/components/ConnectionStatus";
import { ControlPanel } from "@/components/ControlPanel";
import { DataLayer } from "@/components/DataLayer";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { EventLog } from "@/components/EventLog";
import { ProductCatalog } from "@/components/ProductCatalog";
import { useSSE } from "@/hooks/useSSE";

export default function Page() {
  const { state, status } = useSSE();

  return (
    <main className="min-h-screen bg-[#0f1117] px-4 py-6 md:px-8 md:py-8">
      <ConnectionStatus status={status} state={state} />

      <div className="mx-auto max-w-7xl space-y-6">
        <header className="space-y-1">
          <h1 className="text-2xl md:text-3xl font-bold text-slate-50">
            Revok Pricing Demo
          </h1>
          <p className="text-sm text-slate-400">
            Stale-memory detection in real time — Mem0 + Revok proxy + SQLite
          </p>
        </header>

        <div className="grid gap-4 grid-cols-1 md:grid-cols-3">
          <ErrorBoundary label="Data Layer">
            <DataLayer state={state} />
          </ErrorBoundary>
          <ErrorBoundary label="Agent Memory">
            <AgentMemory state={state} />
          </ErrorBoundary>
          <ErrorBoundary label="Agent Answer">
            <AgentAnswer state={state} />
          </ErrorBoundary>
        </div>

        <ErrorBoundary label="Control Panel">
          <ControlPanel state={state} />
        </ErrorBoundary>

        <ErrorBoundary label="Product Catalog">
          <ProductCatalog products={state?.products ?? []} />
        </ErrorBoundary>

        <ErrorBoundary label="Event Log">
          <EventLog entries={state?.event_log ?? []} />
        </ErrorBoundary>
      </div>
    </main>
  );
}
