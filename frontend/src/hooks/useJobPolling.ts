import { useEffect, useRef } from "react";
import { fetchJobStatus } from "../lib/api";
import { JobSnapshot } from "../lib/types";

export function useJobPolling(
  jobId: string | null,
  currentStatus: string | null,
  isWsConnected: boolean,
  onSnapshotUpdate: (snapshot: JobSnapshot) => void,
) {
  const pollingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(
    null,
  );

  useEffect(() => {
    // Only poll while the job is running and WS is disconnected.
    const shouldPoll =
      jobId && !isWsConnected && currentStatus !== "paused";

    if (shouldPoll) {
      pollingIntervalRef.current = setInterval(async () => {
        try {
          const data = await fetchJobStatus(jobId);
          onSnapshotUpdate(data);
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
  }, [jobId, currentStatus, isWsConnected, onSnapshotUpdate]);
}
