"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import type { DemoState } from "@/types/state";
import { Loader2, Play, RotateCcw, CreditCard, Zap, MessageSquare } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { API_URL } from "@/lib/api";

const DEFAULT_QUESTION = "What does this customer's current plan include?";

type ActionId = "load-memory" | "downgrade-plan" | "fire-signal" | "ask-agent" | "reset";

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
    label: "Load Customer Profile",
    variant: "blue",
    endpoint: "/actions/load-memory",
    successMsg: "Loading customer profile… watch the event log",
    icon: Play,
  },
  {
    id: "downgrade-plan",
    step: "2",
    label: "Simulate Plan Downgrade",
    variant: "orangeOutline",
    endpoint: "/actions/downgrade-plan",
    successMsg: "Plan downgraded to Starter",
    tooltip: "Billing system downgrades Enterprise → Starter without notifying the agent",
    icon: CreditCard,
  },
  {
    id: "fire-signal",
    step: "3",
    label: "Fire Billing Signal",
    variant: "destructive",
    endpoint: "/actions/fire-signal",
    successMsg: "Billing CDC signal fired",
    tooltip: "Simulates a CDC event: billing system signals that subscription changed",
    icon: Zap,
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
  /** External running flag from AG-UI hook -- drives the Ask Agent spinner. */
  agUiRunning?: boolean;
}

export function ControlPanel({ state, onAskAgent, onReset, agUiRunning }: ControlPanelProps) {
  const [running, setRunning] = useState<ActionId | null>(null);
  const [question, setQuestion] = useState(DEFAULT_QUESTION);

  // Prevent double-toasting when both the useEffect and the fallback timer fire.
  const completionToastFiredRef = useRef(false);

  // Normal case: server transitions memory_loading true -> false.
  const prevMemoryLoading = useRef<boolean | null>(null);
  useEffect(() => {
    const current = state?.memory_loading ?? false;
    if (prevMemoryLoading.current === true && current === false) {
      if (!completionToastFiredRef.current) {
        toast.success("Customer profile loaded");
        completionToastFiredRef.current = true;
      }
      setRunning((prev) => (prev === "load-memory" ? null : prev));
    }
    prevMemoryLoading.current = current;
  }, [state?.memory_loading]);

  const memoryLoading = state?.memory_loading ?? false;

  async function run(action: ActionDef): Promise<void> {
    setRunning(action.id);
    let success = false;
    completionToastFiredRef.current = false;
    try {
      const bodyObj: Record<string, unknown> | undefined = action.body
        ? { ...action.body }
        : undefined;

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
        // Robust completion detection: poll /state every 2 s up to 5 minutes.
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
                toast.success("Customer profile loaded");
                completionToastFiredRef.current = true;
              }
            }
          } catch {
            /* network blip -- will retry on next interval */
          }
        }, 2000);
      }
    } catch (exc) {
      toast.error(`${action.label} failed: ${String(exc)}`);
    } finally {
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
          <p className="text-xs text-slate-400">Follow steps 1–4 in order</p>
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-3">
          {ACTIONS.filter((a) => a.id !== "ask-agent" && a.id !== "reset").map((action) => {
            const isRunning = running === action.id || (action.id === "load-memory" && memoryLoading);
            const Icon = action.icon;
            return (
              <Button
                key={action.id}
                variant={action.variant}
                disabled={anyRunning}
                onClick={() => run(action)}
                title={action.tooltip}
                className="min-w-[180px]"
              >
                {isRunning ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Icon className="h-4 w-4" />
                )}
                <span className="font-mono text-xs opacity-70">{action.step}</span>
                <span>{action.label}</span>
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
                if (onAskAgent) {
                  onAskAgent(question.trim() || DEFAULT_QUESTION);
                } else {
                  const askAction = ACTIONS.find((a) => a.id === "ask-agent")!;
                  run(askAction);
                }
              }
            }}
            disabled={anyRunning}
            placeholder={DEFAULT_QUESTION}
            className="flex-1 rounded-md border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 disabled:opacity-50"
          />
          {(() => {
            const isAskRunning = (agUiRunning ?? false) || running === "ask-agent";
            return (
              <Button
                variant="greenOutline"
                disabled={anyRunning}
                onClick={() => {
                  if (onAskAgent) {
                    onAskAgent(question.trim() || DEFAULT_QUESTION);
                  } else {
                    const askAction = ACTIONS.find((a) => a.id === "ask-agent")!;
                    run(askAction);
                  }
                }}
                className="shrink-0"
              >
                {isAskRunning ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <MessageSquare className="h-4 w-4" />
                )}
                <span>Ask Agent</span>
              </Button>
            );
          })()}
        </div>

        {/* Reset -- always last, separated */}
        <div className="mt-3">
          {(() => {
            const isResetRunning = running === "reset";
            const resetAction = ACTIONS.find((a) => a.id === "reset")!;
            return (
              <Button
                variant={resetAction.variant}
                disabled={anyRunning}
                onClick={() => run(resetAction)}
                className="min-w-[180px]"
              >
                {isResetRunning ? (
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
