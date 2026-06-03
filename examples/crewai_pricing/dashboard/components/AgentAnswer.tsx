"use client";

import { MessageSquare, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { useTypewriter } from "@/hooks/useTypewriter";
import type { ConfidenceStatus, DemoState } from "@/types/state";

interface AgentAnswerProps {
  state: DemoState | null;
}

function withRevokBanner(status: ConfidenceStatus): {
  text: string;
  className: string;
  borderColor: string;
} {
  switch (status) {
    case "fresh":
      return {
        text: "Memory verified ✅",
        className: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
        borderColor: "#22c55e",
      };
    case "degraded":
      return {
        text: "Re-verifying recommended ⚠️",
        className: "bg-amber-500/15 text-amber-300 border-amber-500/40",
        borderColor: "#f59e0b",
      };
    case "stale":
      return {
        text: "Memory stale — re-verified with live data 🔄",
        className: "bg-red-500/15 text-red-300 border-red-500/40",
        borderColor: "#ef4444",
      };
    default:
      return {
        text: "Confidence unknown",
        className: "bg-slate-500/15 text-slate-300 border-slate-500/40",
        borderColor: "#64748b",
      };
  }
}

function TypewriterText({
  text,
  placeholder,
}: {
  text: string | null | undefined;
  placeholder: string;
}) {
  const displayed = useTypewriter(text ?? "");
  const isTyping = !!text && displayed.length < text.length;

  if (!text) {
    return <p className="text-sm text-slate-500 italic">{placeholder}</p>;
  }

  return (
    <p className="text-sm text-slate-200 leading-relaxed">
      {displayed}
      {isTyping && (
        <span className="inline-block w-[2px] h-[1em] bg-slate-300 ml-0.5 align-middle animate-pulse" />
      )}
    </p>
  );
}

export function AgentAnswer({ state }: AgentAnswerProps) {
  const without = state?.answer_without_revok ?? null;
  const withRevok = state?.answer_with_revok ?? null;
  const status: ConfidenceStatus = state?.confidence_status ?? "unknown";
  const banner = withRevokBanner(status);
  const showWarningIcon = status === "degraded" || status === "stale";

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessageSquare className="h-4 w-4 text-slate-400" />
          Sales Agent Response
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div
          className="space-y-2 pl-3 border-l-4"
          style={{ borderColor: "#ef4444" }}
        >
          <Badge variant="destructive" className="bg-red-500/20 text-red-300">
            Without Revok
          </Badge>
          <TypewriterText text={without?.answer} placeholder="No answer yet" />
          <div className="flex items-center gap-1.5 text-xs rounded border border-red-500/40 bg-red-500/10 text-red-300 px-2 py-1">
            {showWarningIcon && <XCircle className="h-3 w-3" />}
            <span>Acting on unverified memory</span>
          </div>
        </div>

        <Separator />

        <div
          className="space-y-2 pl-3 border-l-4"
          style={{ borderColor: banner.borderColor }}
        >
          <Badge
            className={
              status === "fresh"
                ? "bg-emerald-500/20 text-emerald-300"
                : status === "degraded"
                  ? "bg-amber-500/20 text-amber-300"
                  : status === "stale"
                    ? "bg-red-500/20 text-red-300"
                    : "bg-slate-500/20 text-slate-300"
            }
          >
            With Revok
          </Badge>
          <TypewriterText text={withRevok?.answer} placeholder="No answer yet" />
          <div
            className={`text-xs rounded border px-2 py-1 ${banner.className}`}
          >
            {banner.text}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
