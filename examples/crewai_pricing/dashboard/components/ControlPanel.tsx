"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Loader2, Play, RotateCcw, DollarSign, Zap, MessageSquare } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { API_URL } from "@/lib/api";

type ActionId = "load-memory" | "change-price" | "trigger-signal" | "ask-agent" | "reset";

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
    successMsg: "Memory loaded",
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
    step: "3",
    label: "Fire Signal",
    variant: "destructive",
    endpoint: "/actions/trigger-signal",
    successMsg: "Signal fired",
    tooltip: "Simulates Azure Function trigger",
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

export function ControlPanel() {
  const [running, setRunning] = useState<ActionId | null>(null);

  async function run(action: ActionDef): Promise<void> {
    setRunning(action.id);
    try {
      const res = await fetch(`${API_URL}${action.endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: action.body ? JSON.stringify(action.body) : undefined,
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
      toast.success(action.successMsg);
    } catch (exc) {
      toast.error(`${action.label} failed: ${String(exc)}`);
    } finally {
      setRunning(null);
    }
  }

  const anyRunning = running !== null;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-1">
          <CardTitle>Demo Controls</CardTitle>
          <p className="text-xs text-slate-400">Follow steps 1-4 in order</p>
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-3">
          {ACTIONS.map((action) => {
            const isRunning = running === action.id;
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
      </CardContent>
    </Card>
  );
}
