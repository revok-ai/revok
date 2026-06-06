"use client";

import { Brain } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useRelativeTime } from "@/hooks/useRelativeTime";
import type { ConfidenceStatus, DemoState } from "@/types/state";

interface AgentMemoryProps {
  state: DemoState | null;
}

const GAUGE_SIZE = 120;
const GAUGE_STROKE = 10;
const GAUGE_RADIUS = (GAUGE_SIZE - GAUGE_STROKE) / 2;
const GAUGE_CIRCUMFERENCE = 2 * Math.PI * GAUGE_RADIUS;

function gaugeColor(score: number | null): string {
  if (score === null) return "#475569";
  if (score > 0.7) return "#22c55e";
  if (score >= 0.3) return "#f59e0b";
  return "#ef4444";
}

function statusLabel(status: ConfidenceStatus): string {
  return status.toUpperCase();
}

export function AgentMemory({ state }: AgentMemoryProps) {
  const memory = state?.memory_content ?? null;
  // Score is only meaningful when memory exists — show grey/unknown when empty
  const score = memory ? (state?.confidence_score ?? null) : null;
  const status: ConfidenceStatus = memory ? (state?.confidence_status ?? "unknown") : "unknown";
  const signalCount = state?.signal_count ?? 0;
  const lastSignalRel = useRelativeTime(state?.last_signal_at ?? null);

  const clamped = score === null ? 0 : Math.max(0, Math.min(1, score));
  const dashOffset = GAUGE_CIRCUMFERENCE * (1 - clamped);
  const stroke = gaugeColor(score);

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Brain className="h-4 w-4 text-slate-400" />
          What the Agent Believes
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {memory ? (
          <p className="text-sm text-slate-200 leading-relaxed">{memory}</p>
        ) : (
          <p className="text-sm text-slate-500 italic">No memory stored yet</p>
        )}

        <div className="flex flex-col items-center gap-2 pt-2">
          <div className="relative" style={{ width: GAUGE_SIZE, height: GAUGE_SIZE }}>
            <svg
              width={GAUGE_SIZE}
              height={GAUGE_SIZE}
              viewBox={`0 0 ${GAUGE_SIZE} ${GAUGE_SIZE}`}
              className="-rotate-90"
            >
              <circle
                cx={GAUGE_SIZE / 2}
                cy={GAUGE_SIZE / 2}
                r={GAUGE_RADIUS}
                fill="none"
                stroke="#2a2d3a"
                strokeWidth={GAUGE_STROKE}
              />
              <circle
                cx={GAUGE_SIZE / 2}
                cy={GAUGE_SIZE / 2}
                r={GAUGE_RADIUS}
                fill="none"
                stroke={stroke}
                strokeWidth={GAUGE_STROKE}
                strokeDasharray={GAUGE_CIRCUMFERENCE}
                strokeDashoffset={dashOffset}
                strokeLinecap="round"
                style={{ transition: "stroke-dashoffset 0.5s ease, stroke 0.3s ease" }}
              />
            </svg>
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="text-2xl font-bold tabular-nums text-slate-100">
                {score !== null ? score.toFixed(2) : "—"}
              </span>
            </div>
          </div>
          <div
            className="text-xs font-semibold tracking-wider"
            style={{ color: stroke }}
          >
            {memory ? statusLabel(status) : "NO MEMORY"}
          </div>
        </div>

        <div className="space-y-1 text-xs text-slate-400">
          <div>
            {signalCount} signal{signalCount === 1 ? "" : "s"} received
          </div>
          <div>Last signal: {lastSignalRel}</div>
        </div>
      </CardContent>
    </Card>
  );
}
