"use client";

import { Activity, Cpu, Radio, Clock, AlertTriangle, CheckCircle2, AlertCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ConfidenceStatus, DemoState, EventLogEntry } from "@/types/state";

interface RevokInspectorProps {
  state: DemoState | null;
}

// ─── helpers ────────────────────────────────────────────────────────────────

function relativeTime(unixSec: number | null): string {
  if (unixSec == null) return "—";
  const diffSec = Math.max(0, Math.floor(Date.now() / 1000 - unixSec));
  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  return `${Math.floor(diffSec / 3600)}h ago`;
}

function formatLogTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour12: false });
}

type StatusMeta = {
  dot: string;
  bar: string;
  badge: string;
  label: string;
  Icon: React.FC<{ className?: string }>;
};

const STATUS_META: Record<ConfidenceStatus, StatusMeta> = {
  fresh: {
    dot: "bg-emerald-500",
    bar: "bg-emerald-500",
    badge: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
    label: "FRESH",
    Icon: CheckCircle2,
  },
  degraded: {
    dot: "bg-amber-400",
    bar: "bg-amber-400",
    badge: "bg-amber-400/15 text-amber-300 border-amber-400/30",
    label: "DEGRADED",
    Icon: AlertTriangle,
  },
  stale: {
    dot: "bg-rose-500",
    bar: "bg-rose-500",
    badge: "bg-rose-500/15 text-rose-400 border-rose-500/30",
    label: "STALE",
    Icon: AlertCircle,
  },
  unknown: {
    dot: "bg-slate-500",
    bar: "bg-slate-600",
    badge: "bg-slate-500/15 text-slate-400 border-slate-500/30",
    label: "UNKNOWN",
    Icon: AlertCircle,
  },
};

// ─── sub-components ──────────────────────────────────────────────────────────

function ScoreBar({ score, status }: { score: number | null; status: ConfidenceStatus }) {
  const meta = STATUS_META[status];
  const pct = score != null ? Math.round(Math.max(0, Math.min(1, score)) * 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full bg-slate-700/60">
        <div
          className={`h-full rounded-full transition-all duration-700 ${meta.bar}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs tabular-nums text-slate-400 w-8 text-right">
        {score != null ? score.toFixed(2) : "—"}
      </span>
    </div>
  );
}

function EntityCard({
  name,
  entityKey,
  score,
  status,
  signalCount,
  lastSignalAt,
}: {
  name: string;
  entityKey: string;
  score: number | null;
  status: ConfidenceStatus;
  signalCount: number;
  lastSignalAt: number | null;
}) {
  const meta = STATUS_META[status];
  const hasSignals = signalCount > 0;

  return (
    <div className="rounded-lg border border-slate-700/50 bg-slate-800/40 p-3 space-y-2 hover:bg-slate-800/60 transition-colors">
      {/* header row */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${meta.dot}`} />
          <span className="text-sm font-medium text-slate-200 truncate">{name}</span>
        </div>
        <Badge
          variant="outline"
          className={`text-[10px] px-1.5 py-0 shrink-0 font-mono border ${meta.badge}`}
        >
          {meta.label}
        </Badge>
      </div>

      {/* score bar */}
      <ScoreBar score={score} status={status} />

      {/* footer row */}
      <div className="flex items-center gap-3 text-xs text-slate-500">
        <span className="flex items-center gap-1">
          <Radio className="w-3 h-3" />
          {signalCount} {signalCount === 1 ? "signal" : "signals"}
        </span>
        {hasSignals && (
          <span className="flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {relativeTime(lastSignalAt)}
          </span>
        )}
        <span className="ml-auto font-mono text-[10px] text-slate-600">{entityKey}</span>
      </div>
    </div>
  );
}

function SignalFeed({ entries }: { entries: EventLogEntry[] }) {
  const signals = [...entries]
    .filter((e) => e.type === "signal")
    .slice(-15)
    .reverse();

  if (signals.length === 0) {
    return (
      <p className="text-xs text-slate-500 italic py-2">
        No signals yet &mdash; trigger one with &ldquo;Fire Billing Signal&rdquo;
      </p>
    );
  }

  return (
    <div className="space-y-1 max-h-[180px] overflow-y-auto pr-1">
      {signals.map((e, i) => (
        <div
          key={`${e.timestamp}-${i}`}
          className="flex items-start gap-2 py-1 px-2 rounded border-l-2 border-rose-500/50 bg-rose-500/5 text-xs"
        >
          <span className="shrink-0 tabular-nums text-slate-500 w-[68px]">
            {formatLogTime(e.timestamp)}
          </span>
          <span className="text-slate-300 leading-relaxed">{e.message}</span>
        </div>
      ))}
    </div>
  );
}

// ─── main component ──────────────────────────────────────────────────────────

export function RevokInspector({ state }: RevokInspectorProps) {
  const depScores = state?.dependent_scores ?? {};
  const allEntries = state?.event_log ?? [];

  // Root entity (subscription-tier) is tracked when memory_content is set
  const rootScore = state?.confidence_score ?? null;
  const rootStatus = state?.confidence_status ?? "unknown";
  const rootSignalCount = state?.signal_count ?? 0;

  // Highlight: root or any dependent that is degraded/stale
  const DEP_DISPLAY: [string, string][] = [
    ["seat-limit", "Seat Limit"],
    ["feature-entitlements", "Feature Entitlements"],
    ["api-rate-limit", "API Rate Limit"],
    ["billing-terms", "Billing Terms"],
  ];

  const alertCount =
    (rootStatus === "stale" || rootStatus === "degraded" ? 1 : 0) +
    DEP_DISPLAY.filter(([key]) => {
      const s = depScores[key];
      if (s === null || s === undefined) return false;
      return s < 0.7; // degraded or stale
    }).length;

  const trackedCount =
    (rootScore !== null ? 1 : 0) +
    DEP_DISPLAY.filter(([key]) => depScores[key] !== undefined && depScores[key] !== null).length;

  return (
    <Card className="border-slate-700/50 bg-slate-900/60">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2 text-base font-semibold text-slate-100">
            <Cpu className="w-4 h-4 text-violet-400 shrink-0" />
            Revok Inspector
          </CardTitle>
          <div className="flex items-center gap-2">
            {alertCount > 0 && (
              <Badge
                variant="outline"
                className="text-[10px] px-1.5 py-0 border-rose-500/40 bg-rose-500/10 text-rose-400"
              >
                {alertCount} alert{alertCount > 1 ? "s" : ""}
              </Badge>
            )}
            <Badge
              variant="outline"
              className="text-[10px] px-1.5 py-0 border-slate-600 text-slate-400"
            >
              {trackedCount} tracked
            </Badge>
          </div>
        </div>
        <p className="text-xs text-slate-500 mt-1 leading-relaxed">
          Live view of entity confidence, signal count, and causal state.
        </p>
      </CardHeader>

      <CardContent className="space-y-5 pt-0">
        {/* ── Tracked Entities ────────────────────────────────── */}
        <section>
          <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-widest text-slate-400 mb-2">
            <Activity className="w-3.5 h-3.5" />
            Tracked Entities
          </h3>

          {trackedCount === 0 ? (
            <p className="text-xs text-slate-500 italic py-2">
              No entities tracked yet — load customer profile to start tracking
            </p>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              {/* Root entity */}
              {rootScore !== null && (
                <EntityCard
                  name="Subscription Tier"
                  entityKey="subscription-tier"
                  score={rootScore}
                  status={rootStatus}
                  signalCount={rootSignalCount}
                  lastSignalAt={null}
                />
              )}
              {/* Dependent entities from BFS propagation */}
              {DEP_DISPLAY.map(([key, label]) => {
                const depScore = depScores[key] ?? null;
                if (depScore === null) return null;
                const depStatus =
                  depScore >= 0.7 ? "fresh" : depScore >= 0.3 ? "degraded" : "stale";
                return (
                  <EntityCard
                    key={key}
                    name={label}
                    entityKey={key}
                    score={depScore}
                    status={depStatus as import("@/types/state").ConfidenceStatus}
                    signalCount={0}
                    lastSignalAt={null}
                  />
                );
              })}
            </div>
          )}
        </section>

        {/* divider */}
        <div className="border-t border-slate-700/40" />

        {/* ── Signal History ───────────────────────────────────── */}
        <section>
          <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-widest text-slate-400 mb-2">
            <Radio className="w-3.5 h-3.5" />
            Signal History
          </h3>
          <SignalFeed entries={allEntries} />
        </section>
      </CardContent>
    </Card>
  );
}
