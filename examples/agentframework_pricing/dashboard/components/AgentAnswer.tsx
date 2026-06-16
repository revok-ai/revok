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

/** Returns the bottom notification for the With Revok panel.
 *  Only shows memory health after an answer exists; before that, neutral waiting state.
 *  When the agent re-verified against the live DB, prefer the "fetched live data"
 *  message regardless of the original confidence status. */
function memoryNotification(
  status: ConfidenceStatus,
  hasAnswer: boolean,
  reVerified: boolean,
): { text: string; className: string } {
  if (!hasAnswer) {
    return {
      text: "Ask a question to see memory-verified answers",
      className: "bg-slate-500/10 text-slate-500 border-slate-500/30",
    };
  }
  if (reVerified) {
    return {
      text: "Memory was stale — Revok fetched live data before answering ✅",
      className: "bg-sky-500/15 text-sky-300 border-sky-500/40",
    };
  }
  switch (status) {
    case "fresh":
      return {
        text: "Answered using verified memory ✓",
        className: "bg-emerald-500/10 text-emerald-400/80 border-emerald-500/25",
      };
    case "degraded":
      return {
        text: "Signals detected — answer may lag the market ⚠️",
        className: "bg-amber-500/15 text-amber-300 border-amber-500/40",
      };
    case "stale":
      return {
        text: "Memory was stale — Revok fetched live data before answering ✅",
        className: "bg-sky-500/15 text-sky-300 border-sky-500/40",
      };
    default:
      return {
        text: "Memory state unknown",
        className: "bg-slate-500/10 text-slate-500 border-slate-500/30",
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
  // The notification reflects the memory health AT THE TIME OF THE ANSWER,
  // not the current live status — otherwise the message changes spontaneously
  // as the score decays in the background, even though the displayed text
  // hasn't been re-fetched.  Fall back to live status only as a last resort
  // (e.g. older snapshots that didn't carry confidence_status).
  const status: ConfidenceStatus =
    withRevok?.confidence_status ?? state?.confidence_status ?? "unknown";
  const reVerified = Boolean(withRevok?.re_verified);
  const notification = memoryNotification(status, withRevok !== null, reVerified);
  // Without-Revok side never re-verifies, so the warning icon reflects raw status.
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

        {/* With Revok — brand colour is always green; only the notification reflects memory health */}
        <div
          className="space-y-2 pl-3 border-l-4"
          style={{ borderColor: "#22c55e" }}
        >
          <Badge className="bg-emerald-500/20 text-emerald-300">
            With Revok
          </Badge>
          <TypewriterText text={withRevok?.answer} placeholder="No answer yet" />
          <div
            className={`text-xs rounded border px-2 py-1 ${notification.className}`}
          >
            {notification.text}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
