export type ConfidenceStatus = "fresh" | "degraded" | "stale" | "unknown";
export type EventKind = "memory" | "database" | "signal" | "agent" | "info";

export interface ProductEntry {
  name: string;
  entity_key: string;
  price: number;
  updated_at: string | null;
  confidence_score: number | null;
  confidence_status: ConfidenceStatus;
  signal_count: number;
}

export interface EventLogEntry {
  timestamp: string;
  message: string;
  type: EventKind;
}

export interface RawEventLogEntry {
  ts: string;
  msg: string;
  kind: string;
}

export interface AgentAnswer {
  answer: string;
  memory_quote?: string;
  re_verified?: boolean;
  live_price?: number | null;
  confidence_score?: number | null;
  confidence_status?: ConfidenceStatus;
  signal_count?: number;
}

export interface DemoState {
  db_price: number | null;
  db_product: string;
  db_updated_at: string | null;
  memory_content: string | null;
  confidence_score: number | null;
  confidence_status: ConfidenceStatus;
  signal_count: number;
  last_signal_at: string | null;
  answer_without_revok: AgentAnswer | null;
  answer_with_revok: AgentAnswer | null;
  event_log: EventLogEntry[];
  memory_loading: boolean;
  products: ProductEntry[];
  services: {
    revok: boolean;
    redis_ams: boolean;
  };
}

const VALID_KINDS: ReadonlySet<EventKind> = new Set<EventKind>([
  "memory",
  "database",
  "signal",
  "agent",
  "info",
]);

function normalizeKind(kind: string): EventKind {
  if (kind === "db") return "database";
  if (kind === "answer") return "agent";
  if (VALID_KINDS.has(kind as EventKind)) return kind as EventKind;
  return "info";
}

export function normalizeState(raw: Record<string, unknown>): DemoState {
  const rawLog = (raw.event_log as RawEventLogEntry[] | undefined) ?? [];
  const event_log: EventLogEntry[] = rawLog.map((e) => ({
    timestamp: e.ts,
    message: e.msg,
    type: normalizeKind(e.kind),
  }));

  const services = (raw.services as { revok?: boolean; redis_ams?: boolean } | undefined) ?? {
    revok: false,
    redis_ams: false,
  };

  return {
    db_price: (raw.db_price as number | null) ?? null,
    db_product: (raw.db_product as string) ?? (raw.product_name as string) ?? "",
    db_updated_at: (raw.db_updated_at as string | null) ?? null,
    memory_content: (raw.memory_content as string | null) ?? null,
    confidence_score: (raw.confidence_score as number | null) ?? null,
    confidence_status: (raw.confidence_status as ConfidenceStatus) ?? "unknown",
    signal_count: (raw.signal_count as number) ?? 0,
    last_signal_at: (raw.last_signal_at as string | null) ?? null,
    answer_without_revok: (raw.answer_without_revok as AgentAnswer | null) ?? null,
    answer_with_revok: (raw.answer_with_revok as AgentAnswer | null) ?? null,
    event_log,
    memory_loading: Boolean(raw.memory_loading),
    products: (raw.products as ProductEntry[] | undefined) ?? [],
    services: {
      revok: Boolean(services.revok),
      redis_ams: Boolean(services.redis_ams),
    },
  };
}
