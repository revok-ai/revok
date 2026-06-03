"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { EventKind, EventLogEntry } from "@/types/state";

interface EventLogProps {
  entries: EventLogEntry[];
}

const KIND_COLOR: Record<EventKind, string> = {
  memory: "#3b82f6",
  database: "#f59e0b",
  signal: "#ef4444",
  agent: "#22c55e",
  info: "#64748b",
};

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString();
}

export function EventLog({ entries }: EventLogProps) {
  const sorted = [...entries].slice(-50).reverse();

  return (
    <Card>
      <CardHeader>
        <CardTitle>Event Log</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="max-h-[200px] overflow-y-auto space-y-1.5 pr-2">
          {sorted.length === 0 ? (
            <p className="text-sm text-slate-500 italic">No events yet</p>
          ) : (
            sorted.map((e) => {
              const color = KIND_COLOR[e.type] ?? KIND_COLOR.info;
              return (
                <div
                  key={`${e.timestamp}-${e.message}`}
                  className="flex items-start gap-3 pl-3 py-1 border-l-2 animate-fade-in"
                  style={{ borderColor: color }}
                >
                  <span className="text-xs text-slate-500 tabular-nums shrink-0 w-20">
                    {formatTime(e.timestamp)}
                  </span>
                  <span className="text-sm text-slate-200">{e.message}</span>
                </div>
              );
            })
          )}
        </div>
      </CardContent>
    </Card>
  );
}
