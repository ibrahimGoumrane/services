export type JobStatus = "queued" | "running" | "completed" | "failed";

export interface JobResponse {
  job_id: string;
  status: JobStatus;
}

export interface HeadersResponse {
  headers: string[];
}

export interface JobSettings {
  batchSize: number;
  enableWebScraping: boolean;
  skipGoogleSearch: boolean;
}

export interface LogEntry {
  level: "INFO" | "WARN" | "ERROR" | "DEBUG";
  message: string;
  timestamp: string;
}

export interface WSEvent {
  type: "snapshot" | "queued" | "started" | "stream" | "completed" | "failed";
  data: Record<string, unknown>;
}

export interface WSLogStreamData {
  type: "logs";
  message: string;
  level?: LogEntry["level"];
  timestamp?: string;
}

export interface WSProgressStreamData {
  type: "progress";
  payload: JobMetrics;
}

export interface JobMetrics {
  processed: number;
  inserted: number;
  updated: number;
  errors: number;
}

export interface JobResult {
  total_rows?: number;
  processed?: number;
  inserted?: number;
  updated?: number;
  errors?: string[];
}

export interface JobSnapshot {
  job_id: string;
  status: JobStatus;
  payload: Record<string, unknown>;
  result?: JobResult | null;
  error?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}
