"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Loader2, Layers, Zap } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { API_URL } from "@/lib/api";
import type { ProductEntry, ConfidenceStatus } from "@/types/state";

interface ProductCatalogProps {
  products: ProductEntry[];
}

function statusColor(status: ConfidenceStatus): string {
  switch (status) {
    case "fresh":    return "bg-emerald-500/20 text-emerald-300 border-emerald-500/30";
    case "degraded": return "bg-amber-500/20  text-amber-300  border-amber-500/30";
    case "stale":    return "bg-red-500/20    text-red-300    border-red-500/30";
    default:         return "bg-slate-700/50  text-slate-400  border-slate-600/30";
  }
}

const BULK_SIZES = [50, 100, 500] as const;

export function ProductCatalog({ products }: ProductCatalogProps) {
  const [seeding, setSeeding] = useState(false);

  async function seed(count: number) {
    setSeeding(true);
    try {
      const res = await fetch(`${API_URL}/actions/bulk-seed`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count }),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = (await res.json()) as { seeded: number; errors: number };
      toast.success(
        `Seeded ${data.seeded} products (${data.errors} errors). The table will refresh shortly.`,
      );
    } catch (exc) {
      toast.error(`Bulk seed failed: ${String(exc)}`);
    } finally {
      setSeeding(false);
    }
  }

  const tracked = products.filter((p) => p.confidence_score !== null).length;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2">
            <Layers className="h-4 w-4 text-slate-400" />
            <CardTitle>Product Catalog</CardTitle>
            <Badge variant="secondary" className="bg-slate-700/50 text-slate-400 tabular-nums">
              {products.length} products · {tracked} tracked by Revok
            </Badge>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-slate-500 mr-1">Bulk seed:</span>
            {BULK_SIZES.map((n) => (
              <Button
                key={n}
                size="sm"
                variant="ghost"
                disabled={seeding}
                onClick={() => seed(n)}
                className="h-7 px-3 text-xs border border-slate-600/50 text-slate-300 hover:text-slate-50"
              >
                {seeding ? <Loader2 className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3 mr-1" />}
                +{n}
              </Button>
            ))}
          </div>
        </div>
        <p className="text-xs text-slate-500">
          Each product is an independent Revok entity — no per-entity config needed, just the{" "}
          <code className="text-slate-400">X-Revok-Entity</code> header. Bulk-seed to prove scale.
        </p>
      </CardHeader>

      <CardContent>
        <div className="overflow-auto max-h-72 rounded border border-slate-700/50">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-slate-800/90 backdrop-blur-sm">
              <tr className="text-left text-xs text-slate-400 border-b border-slate-700/50">
                <th className="px-3 py-2 font-medium">Product</th>
                <th className="px-3 py-2 font-medium">Entity key</th>
                <th className="px-3 py-2 font-medium text-right">Price / mo</th>
                <th className="px-3 py-2 font-medium text-center">Confidence</th>
                <th className="px-3 py-2 font-medium text-right">Signals</th>
              </tr>
            </thead>
            <tbody>
              {products.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-3 py-6 text-center text-slate-500 text-xs">
                    No products yet — run "Load Memory" first.
                  </td>
                </tr>
              ) : (
                products.map((p, i) => (
                  <tr
                    key={p.entity_key || p.name}
                    className={`border-b border-slate-700/30 transition-colors hover:bg-slate-800/40 ${
                      i % 2 === 0 ? "" : "bg-slate-800/20"
                    }`}
                  >
                    <td className="px-3 py-2 text-slate-200 font-medium whitespace-nowrap">{p.name}</td>
                    <td className="px-3 py-2 text-slate-500 font-mono text-xs whitespace-nowrap">{p.entity_key}</td>
                    <td className="px-3 py-2 text-slate-200 text-right tabular-nums whitespace-nowrap">
                      ${p.price.toFixed(0)}
                    </td>
                    <td className="px-3 py-2 text-center">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${statusColor(p.confidence_status)}`}
                      >
                        {p.confidence_score !== null
                          ? `${(p.confidence_score * 100).toFixed(0)}% · ${p.confidence_status}`
                          : "untracked"}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-slate-400 text-right tabular-nums">{p.signal_count}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
