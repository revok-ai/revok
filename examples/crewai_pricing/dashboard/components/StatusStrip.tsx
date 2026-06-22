"use client";

import { Database, Brain, Shield } from "lucide-react";
import type { ConfidenceStatus, DemoState } from "@/types/state";

interface StatusStripProps {
  state: DemoState | null;
}

const CONFIDENCE: Record<
  ConfidenceStatus,
  { icon: string; text: string; border: string; bg: string }
> = {
  fresh: {
    icon: "text-emerald-400",
    text: "text-emerald-300",
    border: "border-emerald-500/30",
    bg: "bg-emerald-500/10",
  },
  degraded: {
    icon: "text-amber-400",
    text: "text-amber-300",
    border: "border-amber-500/30",
    bg: "bg-amber-500/10",
  },
  stale: {
    icon: "text-red-400",
    text: "text-red-300",
    border: "border-red-500/30",
    bg: "bg-red-500/10",
  },
  unknown: {
    icon: "text-slate-400",
    text: "text-slate-400",
    border: "border-slate-700",
    bg: "bg-[#1a1d27]",
  },
};

function extractMemoryPrice(memory: string | null): number | null {
  if (!memory) return null;
  const match = memory.match(/\$([0-9]+(?:\.[0-9]+)?)/);
  return match ? Number.parseFloat(match[1]) : null;
}

export function StatusStrip({ state }: StatusStripProps) {
  const dbPrice = state?.db_price ?? null;
  const memoryPrice = extractMemoryPrice(state?.memory_content ?? null);
  const score = state?.confidence_score ?? null;
  const status: ConfidenceStatus = state?.confidence_status ?? "unknown";
  const signalCount = state?.signal_count ?? 0;
  const c = CONFIDENCE[status];
  const drift =
    dbPrice !== null && memoryPrice !== null && dbPrice !== memoryPrice;

  return (
    <div className="flex flex-wrap gap-3">
      {/* ── DB price ─────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 rounded-lg border border-[#2a2d3a] bg-[#1a1d27] px-3 py-2 min-w-[140px]">
        <Database className="h-3.5 w-3.5 text-slate-400 shrink-0" />
        <span className="text-xs text-slate-500">DB price</span>
        <span className="ml-auto text-sm font-semibold text-slate-100 tabular-nums">
          {dbPrice !== null ? `$${dbPrice.toFixed(0)}/mo` : "—"}
        </span>
      </div>

      {/* ── Memory price ─────────────────────────────────────────── */}
      <div
        className={`flex items-center gap-2 rounded-lg border px-3 py-2 min-w-[160px] ${
          drift
            ? "border-amber-500/40 bg-amber-500/10"
            : "border-[#2a2d3a] bg-[#1a1d27]"
        }`}
      >
        <Brain
          className={`h-3.5 w-3.5 shrink-0 ${
            drift ? "text-amber-400" : "text-slate-400"
          }`}
        />
        <span
          className={`text-xs ${drift ? "text-amber-400" : "text-slate-500"}`}
        >
          Memory says
        </span>
        <span
          className={`ml-auto text-sm font-semibold tabular-nums ${
            drift ? "text-amber-200" : "text-slate-100"
          }`}
        >
          {memoryPrice !== null ? `$${memoryPrice.toFixed(0)}/mo` : "—"}
        </span>
        {drift && (
          <span className="ml-1 rounded border border-amber-500/40 bg-amber-500/15 px-1 py-0.5 text-[9px] font-bold tracking-wider text-amber-300 uppercase">
            drift
          </span>
        )}
      </div>

      {/* ── Revok confidence ─────────────────────────────────────── */}
      <div
        className={`flex items-center gap-2 rounded-lg border px-3 py-2 min-w-[160px] ${c.border} ${c.bg}`}
      >
        <Shield className={`h-3.5 w-3.5 shrink-0 ${c.icon}`} />
        <span className={`text-xs ${c.text} opacity-70`}>Revok</span>
        <span
          className={`ml-auto text-sm font-semibold uppercase tracking-wide ${c.text}`}
        >
          {status}
        </span>
        {score !== null && (
          <span className={`text-xs tabular-nums ${c.text} opacity-60`}>
            {score.toFixed(2)}
          </span>
        )}
        {signalCount > 0 && (
          <span
            className={`rounded border ${c.border} px-1 py-0.5 text-[9px] font-medium ${c.text} opacity-70`}
          >
            {signalCount}×
          </span>
        )}
      </div>
    </div>
  );
}
