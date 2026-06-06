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

function extractMemoryPrice(memory: string | null): number | null {
  if (!memory) return null;
  const match = memory.match(/\$([0-9]+(?:\.[0-9]+)?)/);
  return match ? Number.parseFloat(match[1]) : null;
}

export function DataLayer({ state }: DataLayerProps) {
  const dbPrice = state?.db_price ?? null;
  const product = state?.db_product ?? "—";
  const updatedRel = useRelativeTime(state?.db_updated_at ?? null);
  const memoryPrice = extractMemoryPrice(state?.memory_content ?? null);

  const inSync = memoryPrice !== null && dbPrice !== null && memoryPrice === dbPrice;
  const drift = memoryPrice !== null && dbPrice !== null && memoryPrice !== dbPrice;

  // Pulse on price change
  const [pulseKey, setPulseKey] = useState<number>(0);
  const [lastPrice, setLastPrice] = useState<number | null>(dbPrice);
  useEffect(() => {
    if (dbPrice !== null && lastPrice !== null && dbPrice !== lastPrice) {
      setPulseKey((k) => k + 1);
    }
    setLastPrice(dbPrice);
  }, [dbPrice, lastPrice]);

  return (
    <Card className="h-full">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2">
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
              Price Changed
            </Badge>
          )}
          {inSync && (
            <Badge variant="success" className="bg-emerald-500/20 text-emerald-300">
              In Sync
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div
          key={pulseKey}
          className="text-4xl font-bold text-slate-50 tabular-nums animate-soft-pulse"
        >
          {dbPrice !== null ? `$${dbPrice.toFixed(0)}` : "—"}
          <span className="text-base font-normal text-slate-400 ml-1">/ month</span>
        </div>
        <div className="text-sm text-slate-300 font-medium">{product}</div>
        <div className="text-xs text-slate-500">
          Current DB price · updated {updatedRel}
        </div>
        <div className="pt-2 text-xs text-slate-500 italic">
          Tracks the product in the active question. See the catalog below for all products.
        </div>
      </CardContent>
    </Card>
  );
}
