"use client";

import { useEffect, useState } from "react";
import { Database } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useRelativeTime } from "@/hooks/useRelativeTime";
import type { DemoState } from "@/types/state";

interface DataLayerProps {
  state: DemoState | null;
}

function extractMemoryTier(memory: string | null): string | null {
  if (!memory) return null;
  const match = memory.match(/[Ss]ubscription tier[:\s]+(\w+)/);
  return match ? match[1] : null;
}

export function DataLayer({ state }: DataLayerProps) {
  const dbTier = state?.db_subscription_tier ?? null;
  const dbSeats = state?.db_seat_limit ?? null;
  const dbFeatures = state?.db_feature_entitlements ?? null;
  const dbApiRate = state?.db_api_rate_limit ?? null;
  const dbBilling = state?.db_billing_terms ?? null;
  const updatedRel = useRelativeTime(state?.db_updated_at ?? null);
  const memoryTier = extractMemoryTier(state?.memory_content ?? null);

  const inSync = memoryTier !== null && dbTier !== null && memoryTier.toLowerCase() === dbTier.toLowerCase();
  const drift = memoryTier !== null && dbTier !== null && memoryTier.toLowerCase() !== dbTier.toLowerCase();

  // Pulse on tier change
  const [pulseKey, setPulseKey] = useState<number>(0);
  const [lastTier, setLastTier] = useState<string | null>(dbTier);
  useEffect(() => {
    if (dbTier !== null && lastTier !== null && dbTier !== lastTier) {
      setPulseKey((k) => k + 1);
    }
    setLastTier(dbTier);
  }, [dbTier, lastTier]);

  return (
    <Card className="h-full">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Database className="h-4 w-4 text-slate-400" />
            SQL Database
          </CardTitle>
          {!state?.memory_content && (
            <Badge variant="secondary" className="bg-slate-700/50 text-slate-400">
              Memory Empty
            </Badge>
          )}
          {drift && (
            <Badge variant="warning" className="bg-amber-500/20 text-amber-300">
              Plan Changed
            </Badge>
          )}
          {inSync && (
            <Badge variant="success" className="bg-emerald-500/20 text-emerald-300">
              In Sync
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-2 pt-0">
        <div
          key={pulseKey}
          className="text-base font-bold text-slate-50 animate-soft-pulse"
        >
          {dbTier ?? "—"}
        </div>
        <div className="space-y-0.5 text-xs text-slate-300">
          {dbSeats !== null && (
            <div><span className="text-slate-500">Seats:</span> {dbSeats}</div>
          )}
          {dbFeatures && (
            <div><span className="text-slate-500">Features:</span> {dbFeatures}</div>
          )}
          {dbApiRate && (
            <div><span className="text-slate-500">API rate:</span> {dbApiRate}</div>
          )}
          {dbBilling && (
            <div><span className="text-slate-500">Billing:</span> {dbBilling}</div>
          )}
        </div>
        <div className="text-[11px] text-slate-500 pt-0.5">
          Current subscription record · updated {updatedRel}
        </div>
      </CardContent>
    </Card>
  );
}
