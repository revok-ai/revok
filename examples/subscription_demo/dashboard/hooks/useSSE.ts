"use client";

import { useEffect, useRef, useState } from "react";

import { API_URL } from "@/lib/api";
import { normalizeState, type DemoState } from "@/types/state";

export type SSEStatus = "connecting" | "connected" | "reconnecting" | "disconnected";

export interface UseSSEResult {
  state: DemoState | null;
  connected: boolean;
  status: SSEStatus;
  error: string | null;
}

const BACKOFF_STEPS_MS: readonly number[] = [1000, 2000, 4000, 8000, 16000, 30000];

export function useSSE(): UseSSEResult {
  const [state, setState] = useState<DemoState | null>(null);
  const [status, setStatus] = useState<SSEStatus>("connecting");
  const [error, setError] = useState<string | null>(null);

  const sourceRef = useRef<EventSource | null>(null);
  const retryRef = useRef<number>(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelledRef = useRef<boolean>(false);

  useEffect(() => {
    cancelledRef.current = false;

    const connect = (): void => {
      if (cancelledRef.current) return;
      try {
        const es = new EventSource(`${API_URL}/stream`);
        sourceRef.current = es;
        setStatus(retryRef.current === 0 ? "connecting" : "reconnecting");

        es.onopen = () => {
          retryRef.current = 0;
          setStatus("connected");
          setError(null);
        };

        es.onmessage = (event: MessageEvent<string>) => {
          try {
            const parsed = JSON.parse(event.data) as Record<string, unknown>;
            setState(normalizeState(parsed));
          } catch (parseExc) {
            setError(`Parse error: ${String(parseExc)}`);
          }
        };

        es.onerror = () => {
          es.close();
          sourceRef.current = null;
          if (cancelledRef.current) return;
          const idx = Math.min(retryRef.current, BACKOFF_STEPS_MS.length - 1);
          const delay = BACKOFF_STEPS_MS[idx];
          retryRef.current += 1;
          setStatus("reconnecting");
          setError(`Connection lost (retry in ${Math.round(delay / 1000)}s)`);
          reconnectTimerRef.current = setTimeout(connect, delay);
        };
      } catch (exc) {
        setError(`Could not connect: ${String(exc)}`);
        setStatus("disconnected");
      }
    };

    connect();

    return () => {
      cancelledRef.current = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (sourceRef.current) {
        sourceRef.current.close();
        sourceRef.current = null;
      }
    };
  }, []);

  return {
    state,
    connected: status === "connected",
    status,
    error,
  };
}
