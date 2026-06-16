"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import type { DemoState } from "@/types/state";
import { Loader2, Play, RotateCcw, DollarSign, Zap, MessageSquare, Activity } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { API_URL } from "@/lib/api";

const DEFAULT_QUESTION = "What is the current price for Orion Cache per month?";

type ActionId = "load-memory" | "change-price" | "trigger-signal" | "signal-pressure" | "ask-agent" | "reset";

interface ActionDef {
  id: ActionId;
  step: string;
  label: string;
  variant:
    | "blue"
    | "orangeOutline"
    | "destructive"
    | "greenOutline"
    | "ghost";
  endpoint: string;
  body?: Record<string, unknown>;
  successMsg: string;
  tooltip?: string;
  icon: React.ComponentType<{ className?: string }>;
}

const ACTIONS: readonly ActionDef[] = [
  {
    id: "load-memory",
    step: "1",
    label: "Load Memory",
    variant: "blue",
    endpoint: "/actions/load-memory",
    successMsg: "Loading memory… watch the event log",
    icon: Play,
  },
  {
    id: "change-price",
    step: "2",
    label: "Change Price to $800",
    variant: "orangeOutline",
    endpoint: "/actions/change-price",
    body: { new_price: 800 },
    successMsg: "Price updated",
    icon: DollarSign,
  },
  {
    id: "trigger-signal",
    step: "3a",
    label: "Fire Signal",
    variant: "destructive",
    endpoint: "/actions/trigger-signal",
    successMsg: "Signal fired",
    tooltip: "Simulates Azure Function trigger",
    icon: Zap,
  },
  {
    id: "signal-pressure",
    step: "3b",
    label: "Simulate Pressure",
    variant: "destructive",
    endpoint: "/actions/signal-pressure",
    successMsg: "Signal burst fired — watch confidence collapse",
    tooltip: "Fires 3 rapid signals: fresh → degraded → stale",
    icon: Activity,
  },
  {
    id: "ask-agent",
    step: "4",
    label: "Ask Agent",
    variant: "greenOutline",
    endpoint: "/actions/ask-agent",
    successMsg: "Agent answered",
    icon: MessageSquare,
  },
  {
    id: "reset",
    step: "↺",
    label: "Reset Demo",
    variant: "ghost",
    endpoint: "/actions/reset",
    successMsg: "Demo reset",
    icon: RotateCcw,
  },
] as const;

interface ControlPanelProps {
  state: DemoState | null;
  /** When provided, Ask Agent calls this instead of POSTing to /actions/ask-agent. */
  onAskAgent?: (question: string) => void;
  /** Called after a successful reset so callers can clear local AG-UI state. */
  onReset?: () => void;
  /** External running flag from AG-UI hook — drives the Ask Agent spinner. */
  agUiRunning?: boolean;
}

export function ControlPanel({ state, onAskAgent, onReset, agUiRunning }: ControlPanelProps) {
  const [running, setRunning] = useState<ActionId | null>(null);
  const [question, setQuestion] = useState(DEFAULT_QUESTION);
  // Which product Change Price and Fire Signal target.  Defaults to the
  // first product in the catalog (Orion Cache) and is kept in sync with
  // whatever the server reports as products[] once state arrives.
  const [selectedProduct, setSelectedProduct] = useState<string>("Orion Cache");

  // When the product list first arrives, default the selection to the first one
  // if the current value isn't in the list (e.g. on first load).
  useEffect(() => {
    const names = (state?.products ?? []).map((p) => p.name);
    if (names.length > 0 && !names.includes(selectedProduct)) {
      setSelectedProduct(names[0]);
    }
  }, [state?.products, selectedProduct]);

  // Prevent double-toasting when both the useEffect and the fallback timer fire.
  const completionToastFiredRef = useRef(false);

  // Normal case: server transitions memory_loading true → false.
  // The prevMemoryLoading ref lets us detect the transition across re-renders.
  const prevMemoryLoading = useRef<boolean | null>(null);
  useEffect(() => {
    const current = state?.memory_loading ?? false;
    if (prevMemoryLoading.current === true && current === false) {
      if (!completionToastFiredRef.current) {
        toast.success("Memory loaded for all products");
        completionToastFiredRef.current = true;
      }
      setRunning((prev) => (prev === "load-memory" ? null : prev));
    }
    prevMemoryLoading.current = current;
  }, [state?.memory_loading]);

  // Keep the load-memory button spinning while the background task runs.
  const memoryLoading = state?.memory_loading ?? false;

  async function run(action: ActionDef): Promise<void> {
    setRunning(action.id);
    let success = false;
    completionToastFiredRef.current = false;
    try {
      // Inject the selected product into the body for actions that target a
      // specific product.  ask-agent uses the typed question; load-memory
      // and reset are global.
      let bodyObj: Record<string, unknown> | undefined = action.body
        ? { ...action.body }
        : undefined;
      if (action.id === "change-price" || action.id === "trigger-signal" || action.id === "signal-pressure") {
        bodyObj = { ...(bodyObj ?? {}), product_name: selectedProduct };
      }

      const body =
        action.id === "ask-agent"
          ? JSON.stringify({ question: question.trim() || DEFAULT_QUESTION })
          : bodyObj
            ? JSON.stringify(bodyObj)
            : undefined;

      const res = await fetch(`${API_URL}${action.endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      if (!res.ok) {
        let detail = `${res.status} ${res.statusText}`;
        try {
          const data: unknown = await res.json();
          if (
            data &&
            typeof data === "object" &&
            "error" in data &&
            typeof (data as { error: unknown }).error === "string"
          ) {
            detail = (data as { error: string }).error;
          }
        } catch {
          /* ignore */
        }
        toast.error(`${action.label} failed: ${detail}`);
        return;
      }
      success = true;
      if (action.id !== "load-memory") {
        toast.success(action.successMsg);
        if (action.id === "reset") {
          onReset?.();
        }
      } else {
        // Robust completion detection: poll /state every 2 s up to 5 minutes
        // until memory_loading is false.  This is the single authoritative path
        // — it works regardless of SSE delivery, regardless of how fast or slow
        // the background task runs, and regardless of whether the useEffect
        // catches the transition first (the completionToastFiredRef guards against
        // double-toasting).
        const startedAt = Date.now();
        const MAX_WAIT_MS = 5 * 60 * 1000;
        const pollInterval = setInterval(async () => {
          if (Date.now() - startedAt > MAX_WAIT_MS) {
            clearInterval(pollInterval);
            setRunning((prev) => (prev === "load-memory" ? null : prev));
            if (!completionToastFiredRef.current) {
              toast.error("Load Memory timed out after 5 minutes");
              completionToastFiredRef.current = true;
            }
            return;
          }
          try {
            const r = await fetch(`${API_URL}/state`);
            if (!r.ok) return;
            const s = (await r.json()) as { memory_loading?: boolean };
            if (s.memory_loading === false) {
              clearInterval(pollInterval);
              setRunning((prev) => (prev === "load-memory" ? null : prev));
              if (!completionToastFiredRef.current) {
                toast.success("Memory loaded for all products");
                completionToastFiredRef.current = true;
              }
            }
          } catch {
            /* network blip — will retry on next interval */
          }
        }, 2000);
      }
    } catch (exc) {
      toast.error(`${action.label} failed: ${String(exc)}`);
    } finally {
      // For load-memory on success: keep running set so the spinner bridges
      // the gap between the 202 response and the first poll with memory_loading=true.
      // On failure (success=false): always clear so the button resets.
      if (!(action.id === "load-memory" && success)) {
        setRunning(null);
      }
    }
  }

  const anyRunning = running !== null || memoryLoading || (agUiRunning ?? false);

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-1">
          <CardTitle>Demo Controls</CardTitle>
          <p className="text-xs text-slate-400">Follow steps 1-4 in order</p>
        </div>
      </CardHeader>
      <CardContent>
        {/* Target-product selector — Change Price and Fire Signal target this product */}
        <div className="mb-3 flex items-center gap-2">
          <label htmlFor="target-product" className="text-xs text-slate-400 shrink-0">
            Target product:
          </label>
          <select
            id="target-product"
            value={selectedProduct}
            onChange={(e) => setSelectedProduct(e.target.value)}
            disabled={anyRunning}
            className="rounded-md border border-slate-700 bg-slate-800/60 px-2 py-1 text-sm text-slate-200 focus:outline-none focus:ring-1 focus:ring-slate-500 disabled:opacity-50"
          >
            {(state?.products ?? []).map((p) => (
              <option key={p.entity_key} value={p.name}>
                {p.name} — ${p.price.toFixed(0)}/mo
              </option>
            ))}
          </select>
          <span className="text-xs text-slate-500">(applies to steps 2 & 3)</span>
        </div>

        <div className="flex flex-wrap gap-3">
          {ACTIONS.filter((a) => a.id !== "ask-agent" && a.id !== "reset").map((action) => {
            const isRunning = running === action.id || (action.id === "load-memory" && memoryLoading);
            const Icon = action.icon;
            // Show the target product on the actions that take it.
            const label =
              action.id === "change-price"
                ? `Change ${selectedProduct} → $800`
                : action.id === "trigger-signal"
                  ? `Fire Signal: ${selectedProduct}`
                  : action.id === "signal-pressure"
                    ? `Pressure: ${selectedProduct}`
                    : action.label;
            const tooltip =
              action.id === "trigger-signal"
                ? `Simulates a CDC event for ${selectedProduct}`
                : action.id === "signal-pressure"
                  ? `Fires 3 rapid signals for ${selectedProduct}: fresh → degraded → stale`
                  : action.tooltip;
            return (
              <Button
                key={action.id}
                variant={action.variant}
                disabled={anyRunning}
                onClick={() => run(action)}
                title={tooltip}
                className="min-w-[180px]"
              >
                {isRunning ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Icon className="h-4 w-4" />
                )}
                <span className="font-mono text-xs opacity-70">{action.step}</span>
                <span>{label}</span>
              </Button>
            );
          })}
        </div>

        {/* Step 4: question input + ask button on the same row */}
        <div className="mt-3 flex gap-2 items-center">
          <span className="font-mono text-xs text-slate-400 shrink-0">4</span>
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !anyRunning) {
                const askAction = ACTIONS.find((a) => a.id === "ask-agent")!;
                run(askAction);
              }
            }}
            disabled={anyRunning}
            placeholder={DEFAULT_QUESTION}
            className="flex-1 rounded-md border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 disabled:opacity-50"
          />
          {(() => {
            const askAction = ACTIONS.find((a) => a.id === "ask-agent")!;
            const isRunning = (agUiRunning ?? false) || running === "ask-agent";
            return (
              <Button
                variant={askAction.variant}
                disabled={anyRunning}
                onClick={() => {
                  if (onAskAgent) {
                    onAskAgent(question.trim() || DEFAULT_QUESTION);
                  } else {
                    run(askAction);
                  }
                }}
                className="shrink-0"
              >
                {isRunning ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <MessageSquare className="h-4 w-4" />
                )}
                <span>Ask Agent</span>
              </Button>
            );
          })()}
        </div>

        {/* Reset — always last, separated */}
        <div className="mt-3">
          {(() => {
            const resetAction = ACTIONS.find((a) => a.id === "reset")!;
            const isRunning = running === "reset";
            return (
              <Button
                variant={resetAction.variant}
                disabled={anyRunning}
                onClick={() => run(resetAction)}
                className="min-w-[180px]"
              >
                {isRunning ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RotateCcw className="h-4 w-4" />
                )}
                <span className="font-mono text-xs opacity-70">{resetAction.step}</span>
                <span>{resetAction.label}</span>
              </Button>
            );
          })()}
        </div>
      </CardContent>
    </Card>
  );
}
