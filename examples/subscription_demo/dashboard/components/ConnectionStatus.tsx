"use client";

import { Wifi, WifiOff, Loader2, AlertTriangle } from "lucide-react";

import type { SSEStatus } from "@/hooks/useSSE";
import type { DemoState } from "@/types/state";

interface ConnectionStatusProps {
  status: SSEStatus;
  state: DemoState | null;
}

export function ConnectionStatus({ status, state }: ConnectionStatusProps) {
  let dotClass = "bg-emerald-500";
  let label = "Connected";
  let Icon = Wifi;
  if (status === "disconnected") {
    dotClass = "bg-red-500";
    label = "Disconnected";
    Icon = WifiOff;
  } else if (status === "reconnecting" || status === "connecting") {
    dotClass = "bg-amber-500";
    label = status === "connecting" ? "Connecting…" : "Reconnecting…";
    Icon = Loader2;
  }

  const services = state?.services;
  const offlineMessages: string[] = [];
  if (services && !services.revok) offlineMessages.push("Revok offline");
  if (services && !services.redis_ams) offlineMessages.push("Redis AMS offline");

  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col items-end gap-2">
      <div className="flex items-center gap-2 rounded-md border border-[#2a2d3a] bg-[#1a1d27] px-3 py-1.5 text-sm shadow">
        <span className="relative flex h-2.5 w-2.5">
          <span
            className={`absolute inline-flex h-full w-full rounded-full opacity-60 ${
              status === "connected" ? "animate-ping" : ""
            } ${dotClass}`}
          />
          <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${dotClass}`} />
        </span>
        <Icon
          className={`h-3.5 w-3.5 text-slate-300 ${
            status === "reconnecting" || status === "connecting" ? "animate-spin" : ""
          }`}
        />
        <span className="text-slate-200">{label}</span>
      </div>
      {offlineMessages.map((msg) => (
        <div
          key={msg}
          className="flex items-center gap-1.5 rounded-md border border-red-500/40 bg-red-500/10 px-2.5 py-1 text-xs text-red-300"
        >
          <AlertTriangle className="h-3 w-3" />
          {msg}
        </div>
      ))}
    </div>
  );
}
