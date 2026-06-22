"use client";

import { useEffect, useRef, useState } from "react";

const CHARS_PER_TICK = 3; // characters revealed per interval tick
const TICK_MS = 18;       // ~55 chars/sec — feels like a fast LLM stream

/**
 * Animates `target` text character-by-character whenever the value changes.
 * Returns the currently-visible slice of the string.
 */
export function useTypewriter(target: string | null | undefined): string {
  const [displayed, setDisplayed] = useState<string>("");
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const posRef = useRef<number>(0);
  const targetRef = useRef<string>("");

  useEffect(() => {
    const next = target ?? "";

    // Same text — nothing to do
    if (next === targetRef.current && displayed === next) return;

    // New text arrived: reset and start animating from zero
    targetRef.current = next;
    posRef.current = 0;
    setDisplayed("");

    if (intervalRef.current) {
      clearInterval(intervalRef.current);
    }

    if (!next) return;

    intervalRef.current = setInterval(() => {
      posRef.current = Math.min(posRef.current + CHARS_PER_TICK, targetRef.current.length);
      setDisplayed(targetRef.current.slice(0, posRef.current));

      if (posRef.current >= targetRef.current.length) {
        clearInterval(intervalRef.current!);
        intervalRef.current = null;
      }
    }, TICK_MS);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
    // Only re-run when the target string itself changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  return displayed;
}
