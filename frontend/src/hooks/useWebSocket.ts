import { useState, useEffect, useRef, useCallback } from "react";
import {
  JobDisplayStatus,
  LogEntry,
  WSEvent,
  JobMetrics,
  JobResult,
  JobSnapshot,
  WSLogStreamData,
  WSProgressStreamData,
  deriveDisplayStatus,
} from "../lib/types";

const WS_BASE_URL = "ws://127.0.0.1:8000";

function toLogEntry(data: Record<string, unknown>): LogEntry | null {
  const rawLevel =
    typeof data.level === "string" ? data.level.toUpperCase() : "INFO";
  const level =
    rawLevel === "WARNING"
      ? "WARN"
      : rawLevel === "CRITICAL"
        ? "ERROR"
        : rawLevel;
  const message = data.message;
  const timestamp = data.timestamp;

  if (
    (level === "INFO" ||
      level === "WARN" ||
      level === "ERROR" ||
      level === "DEBUG") &&
    typeof message === "string" &&
    typeof timestamp === "string"
  ) {
    return { level, message, timestamp };
  }

  return null;
}

function isLogStreamData(data: unknown): data is WSLogStreamData {
  if (!data || typeof data !== "object") {
    return false;
  }
  const stream = data as { type?: unknown; message?: unknown };
  return stream.type === "logs" && typeof stream.message === "string";
}

function isProgressStreamData(data: unknown): data is WSProgressStreamData {
  if (!data || typeof data !== "object") {
    return false;
  }
  const stream = data as { type?: unknown; payload?: unknown };
  if (stream.type !== "progress") {
    return false;
  }
  const payload = stream.payload;
  if (!payload || typeof payload !== "object") {
    return false;
  }
  const p = payload as Record<string, unknown>;
  return (
    typeof p.processed === "number" &&
    typeof p.inserted === "number" &&
    typeof p.updated === "number" &&
    typeof p.errors === "number" &&
    (typeof p.synthetic_emails_created === "number" ||
      typeof p.synthetic_emails_created === "undefined")
  );
}

function toMetricsFromResult(result: JobResult | null | undefined): JobMetrics {
  return {
    processed: Number(result?.processed ?? 0),
    inserted: Number(result?.inserted ?? 0),
    updated: Number(result?.updated ?? 0),
    errors: Array.isArray(result?.errors) ? result.errors.length : 0,
    synthetic_emails_created: Number(result?.synthetic_emails_created ?? 0),
  };
}

function mergeMetricsFromLog(previous: JobMetrics, log: LogEntry): JobMetrics {
  const next: JobMetrics = { ...previous };
  const message = log.message || "";

  const loadedMatch = message.match(/Loaded\s+(\d+)\s+contacts/i);
  if (loadedMatch) {
    const loaded = Number(loadedMatch[1]);
    if (!Number.isNaN(loaded) && loaded > next.processed) {
      next.processed = loaded;
    }
  }

  const batchMatch =
    message.match(/Batch:\s*(\d+)\s+inserted,\s*(\d+)\s+updated/i) ||
    message.match(/Emails:\s*inserted=(\d+),\s*updated=(\d+)/i);
  if (batchMatch) {
    const inserted = Number(batchMatch[1]);
    const updated = Number(batchMatch[2]);
    if (!Number.isNaN(inserted)) {
      next.inserted += inserted;
    }
    if (!Number.isNaN(updated)) {
      next.updated += updated;
    }
  }

  const progressMatch = message.match(/Progress:\s*(\d+)\s*\/\s*(\d+)/i);
  if (progressMatch) {
    const processed = Number(progressMatch[1]);
    if (!Number.isNaN(processed) && processed > next.processed) {
      next.processed = processed;
    }
  }

  if (log.level === "ERROR") {
    next.errors += 1;
  }

  if (/synthetic fallback email generated/i.test(message)) {
    next.synthetic_emails_created += 1;
  }

  return next;
}

function toSnapshot(data: Record<string, unknown>): JobSnapshot {
  return {
    job_id: String(data.job_id ?? ""),
    status:
      data.status === "running"
        ? "running"
        : data.status === "completed"
          ? "completed"
          : "paused",
    payload: (data.payload as Record<string, unknown>) ?? {},
    result: data.result as JobResult | null | undefined,
    error: data.error as string | null | undefined,
    created_at: String(data.created_at ?? ""),
    started_at: data.started_at as string | null | undefined,
    paused_at: data.paused_at as string | null | undefined,
    completed_at: data.completed_at as string | null | undefined,
    current_row:
      typeof data.current_row === "number" ? data.current_row : undefined,
    total_rows:
      typeof data.total_rows === "number" ? data.total_rows : undefined,
  };
}

export function useWebSocket(jobId: string | null) {
  const [displayStatus, setDisplayStatus] = useState<JobDisplayStatus | null>(
    null,
  );
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [metrics, setMetrics] = useState<JobMetrics>({
    processed: 0,
    inserted: 0,
    updated: 0,
    errors: 0,
    synthetic_emails_created: 0,
  });
  const [error, setError] = useState<string | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );

  const connect = useCallback(() => {
    if (!jobId) return;

    const ws = new WebSocket(`${WS_BASE_URL}/ws/jobs/${jobId}`);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      setError(null);
    };

    ws.onmessage = (event) => {
      try {
        const payload: WSEvent = JSON.parse(event.data);
        const data = payload.data;

        switch (payload.type) {
          case "snapshot":
          case "started":
          case "paused":
          case "completed":
          case "failed": {
            const snapshot = toSnapshot(data);
            setDisplayStatus(deriveDisplayStatus(snapshot));
            if (snapshot.error) {
              setError(snapshot.error);
            }
            if (snapshot.result && typeof snapshot.result === "object") {
              setMetrics(toMetricsFromResult(snapshot.result));
            }
            break;
          }
          case "stream":
            if (isLogStreamData(data)) {
              const logEntry = toLogEntry({
                level: data.level ?? "INFO",
                message: data.message,
                timestamp: data.timestamp ?? new Date().toISOString(),
              });
              if (!logEntry) {
                break;
              }
              setLogs((prev) => [...prev, logEntry]);
              setMetrics((prev) => mergeMetricsFromLog(prev, logEntry));
              break;
            }

            if (isProgressStreamData(data)) {
              setMetrics((previous) => ({
                processed: data.payload.processed,
                inserted: data.payload.inserted,
                updated: data.payload.updated,
                errors: data.payload.errors,
                synthetic_emails_created:
                  typeof data.payload.synthetic_emails_created === "number"
                    ? data.payload.synthetic_emails_created
                    : previous.synthetic_emails_created,
              }));
            }
            break;
        }
      } catch (err) {
        console.error("Failed to parse WS message", err);
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      // Auto-reconnect if not in a terminal state
      if (
        displayStatus !== "completed" &&
        displayStatus !== "failed" &&
        displayStatus !== "paused"
      ) {
        reconnectTimeoutRef.current = setTimeout(connect, 3000);
      }
    };

    ws.onerror = () => {
      // Error handling is mostly managed by onclose reconnects
    };
  }, [jobId, displayStatus]);

  useEffect(() => {
    connect();

    return () => {
      if (reconnectTimeoutRef.current)
        clearTimeout(reconnectTimeoutRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connect]);

  return { status: displayStatus, logs, metrics, error, isConnected };
}
