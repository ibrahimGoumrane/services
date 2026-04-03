import { useState, useEffect, useRef, useCallback } from "react";
import {
  JobStatus,
  LogEntry,
  WSEvent,
  JobMetrics,
  JobResult,
  WSLogStreamData,
  WSProgressStreamData,
} from "../lib/types";

const WS_BASE_URL = "ws://127.0.0.1:8000";

function isJobStatus(value: unknown): value is JobStatus {
  return (
    value === "queued" ||
    value === "running" ||
    value === "paused" ||
    value === "completed" ||
    value === "failed"
  );
}

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
    typeof p.errors === "number"
  );
}

function toMetricsFromResult(result: JobResult | null | undefined): JobMetrics {
  return {
    processed: Number(result?.processed ?? 0),
    inserted: Number(result?.inserted ?? 0),
    updated: Number(result?.updated ?? 0),
    errors: Array.isArray(result?.errors) ? result.errors.length : 0,
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

  return next;
}

export function useWebSocket(jobId: string | null) {
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [metrics, setMetrics] = useState<JobMetrics>({
    processed: 0,
    inserted: 0,
    updated: 0,
    errors: 0,
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
            if (isJobStatus(data.status)) {
              setStatus(data.status);
            }
            if (data.result && typeof data.result === "object") {
              setMetrics(toMetricsFromResult(data.result as JobResult));
            }
            break;
          case "queued":
            setStatus("queued");
            break;
          case "started":
            setStatus("running");
            break;
          case "paused":
            setStatus("paused");
            break;
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
              setMetrics({
                processed: data.payload.processed,
                inserted: data.payload.inserted,
                updated: data.payload.updated,
                errors: data.payload.errors,
              });
            }
            break;
          case "completed":
            setStatus("completed");
            if (data.result && typeof data.result === "object") {
              setMetrics(toMetricsFromResult(data.result as JobResult));
            }
            break;
          case "failed":
            setStatus("failed");
            setError(
              typeof data.error === "string" && data.error
                ? data.error
                : "Job failed unexpectedly",
            );
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
        status !== "completed" &&
        status !== "failed" &&
        status !== "paused"
      ) {
        reconnectTimeoutRef.current = setTimeout(connect, 3000);
      }
    };

    ws.onerror = () => {
      // Error handling is mostly managed by onclose reconnects
    };
  }, [jobId, status]);

  useEffect(() => {
    connect();

    return () => {
      if (reconnectTimeoutRef.current)
        clearTimeout(reconnectTimeoutRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connect]);

  return { status, logs, metrics, error, isConnected };
}
