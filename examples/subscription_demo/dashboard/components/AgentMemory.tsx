"use client";

import { Brain, GitBranch } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ConfidenceStatus, DemoState } from "@/types/state";

interface AgentMemoryProps {
  state: DemoState | null;
}

// ── helpers ──────────────────────────────────────────────────────────────────

function scoreColor(score: number | null): string {
  if (score === null) return "#475569";
  if (score > 0.7) return "#22c55e";
  if (score >= 0.3) return "#f59e0b";
  return "#ef4444";
}

function statusBadgeClass(status: ConfidenceStatus): string {
  switch (status) {
    case "fresh":    return "bg-emerald-500/15 text-emerald-400 border-emerald-500/30";
    case "degraded": return "bg-amber-400/15 text-amber-300 border-amber-400/30";
    case "stale":    return "bg-rose-500/15 text-rose-400 border-rose-500/30";
    default:         return "bg-slate-500/15 text-slate-400 border-slate-500/30";
  }
}

const DEP_ENTITIES: [string, string][] = [
  ["seat-limit",            "Seat Limit"],
  ["feature-entitlements",  "Feature Entitlements"],
  ["api-rate-limit",        "API Rate Limit"],
  ["billing-terms",         "Billing Terms"],
];

function ScoreBar({
  score,
  color,
  className = "",
}: {
  score: number | null;
  color: string;
  className?: string;
}) {
  const pct = score !== null ? Math.round(Math.max(0, Math.min(1, score)) * 100) : 0;
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div className="flex-1 h-1.5 bg-slate-700/60 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-[11px] tabular-nums text-slate-400 w-8 text-right shrink-0">
        {score !== null ? score.toFixed(2) : "—"}
      </span>
    </div>
  );
}

// ── component ─────────────────────────────────────────────────────────────────

export function AgentMemory({ state }: AgentMemoryProps) {
  const memory = state?.memory_content ?? null;
  const score = memory ? (state?.confidence_score ?? null) : null;
  const status: ConfidenceStatus = memory
    ? (state?.confidence_status ?? "unknown")
    : "unknown";
  const signalCount = state?.signal_count ?? 0;
  const rootColor = scoreColor(score);

  return (
    <Card className="h-full">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Brain className="h-4 w-4 text-slate-400" />
          What the Agent Believes
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 pt-0">
        {memory ? (
          <p className="text-sm text-slate-200 leading-relaxed line-clamp-3">{memory}</p>
        ) : (
          <p className="text-sm text-slate-500 italic">No memory stored yet</p>
        )}

        {/* Causal cascade panel */}
        <div className="rounded-md border border-slate-700/50 bg-slate-800/30 p-2.5 space-y-2">
          <div className="flex items-center gap-1.5 mb-1">
            <GitBranch className="h-3 w-3 text-slate-500" />
            <span className="text-[10px] text-slate-500 uppercase tracking-wider">
              Revok confidence · causal cascade
            </span>
          </div>

          {/* Root entity row */}
          <div className="space-y-1">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold text-slate-300">Subscription Tier</span>
              <div className="flex items-center gap-1.5">
                {memory && (
                  <Badge
                    variant="outline"
                    className={`text-[9px] px-1 py-0 font-mono border ${statusBadgeClass(status)}`}
                  >
                    {status.toUpperCase()}
                  </Badge>
                )}
                <span className="text-[10px] text-slate-500">
                  {signalCount} sig{signalCount !== 1 ? "s" : ""}
                </span>
              </div>
            </div>
            <ScoreBar score={score} color={rootColor} />
          </div>

          {/* Tree connector + dependent rows */}
          <div className="ml-2 pl-2.5 border-l border-slate-600/40 space-y-1.5 pt-0.5">
            {DEP_ENTITIES.map(([key, label], idx) => {
              // Gate on `memory` the same way the root score is gated above —
              // otherwise a leftover Revok entity from a prior session (the
              // store persists across redeploys, unlike in-memory demo state)
              // can render dependents as colored while root still shows
              // "not loaded".
              const depScore = memory ? (state?.dependent_scores?.[key] ?? null) : null;
              const depColor = scoreColor(depScore);
              const isLast = idx === DEP_ENTITIES.length - 1;
              return (
                <div key={key} className="flex items-center gap-2 relative">
                  {/* tree connector */}
                  <span className="text-slate-600 text-[10px] shrink-0 select-none leading-none">
                    {isLast ? "└─" : "├─"}
                  </span>
                  <div className="flex-1 space-y-0.5">
                    <span className="text-[11px] text-slate-400">{label}</span>
                    <ScoreBar score={depScore} color={depColor} />
                  </div>
                </div>
              );
            })}
          </div>

          {signalCount === 0 ? (
            <p className="text-[10px] text-slate-500 pt-0.5 italic">
              Fire a billing signal to see confidence degrade and propagate
            </p>
          ) : (
            <p className="text-[10px] text-slate-600 pt-0.5">
              Scores propagate root → dependents via causal graph
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
