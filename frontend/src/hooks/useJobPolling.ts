import { useEffect, useRef } from "react";
import { fetchJobStatus } from "../lib/api";
import { JobMetrics, JobStatus, JobSnapshot } from "../lib/types";

function toMetrics(snapshot: JobSnapshot): JobMetrics {
  const result = snapshot.result;
  return {
    processed: Number(result?.processed ?? 0),
    inserted: Number(result?.inserted ?? 0),
    updated: Number(result?.updated ?? 0),
    errors: Array.isArray(result?.errors) ? result.errors.length : 0,
  };
}

export function useJobPolling(
  jobId: string | null,
  currentStatus: JobStatus | null,
  isWsConnected: boolean,
  onStatusUpdate: (
    status: JobStatus,
    metrics?: JobMetrics,
    error?: string,
  ) => void,
) {
  const pollingIntervalRef = useRef<NodeJS.Timeout>();

  useEffect(() => {
    // Only poll if we have a job, WS is disconnected, and we aren't in a terminal state
    const shouldPoll =
      jobId &&
      !isWsConnected &&
      currentStatus !== "completed" &&
      currentStatus !== "failed";

    if (shouldPoll) {
      pollingIntervalRef.current = setInterval(async () => {
        try {
          const data = await fetchJobStatus(jobId);
          onStatusUpdate(data.status, toMetrics(data), data.error ?? undefined);
        } catch (err) {
          console.error("Polling failed", err);
        }
      }, 3000);
    }

    return () => {
      if (pollingIntervalRef.current) {
        clearInterval(pollingIntervalRef.current);
      }
    };
  }, [jobId, currentStatus, isWsConnected, onStatusUpdate]);
}
