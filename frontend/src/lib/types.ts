export type JobStatus = "running" | "paused" | "completed";

export type JobDisplayStatus = "queued" | "running" | "paused" | "completed" | "failed";

export function deriveDisplayStatus(snapshot: JobSnapshot): JobDisplayStatus {
  if (snapshot.status === "running") return "running";
  if (snapshot.status === "completed") return "completed";
  if (snapshot.status === "paused") return "paused";
  if (snapshot.error) return "failed";
  const total = snapshot.total_rows ?? 0;
  const current = snapshot.current_row ?? 0;
  if (total > 0 && current >= total) return "completed";
  if (total > 0 && current < total) return "paused";
  return "queued";
}

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
  enablePersonSearch: boolean;
  enableCompanySearch: boolean;
  enableDebugging: boolean;
}

export interface UrlScrapeRequest {
  urls: string[];
  enable_web_scraping: boolean;
  skip_google_search: boolean;
  enable_debugging: boolean;
}

export interface UrlScrapeResult {
  email?: string | null;
  website?: string | null;
  phone?: string | null;
  city?: string | null;
  country?: string | null;
  contact_form_url?: string | null;
  whatsapp?: string | null;
  facebook?: string | null;
  instagram?: string | null;
  tiktok?: string | null;
  youtube?: string | null;
  telegram?: string | null;
  calendly?: string | null;
  status?: "inserted" | "updated";
}

/**
 * Mapping value type for field mapping:
 * - "": Field is skipped (not mapped)
 * - "__default__:{value}": Use a constant default value for all rows
 * - "{columnName}": Map from a CSV column
 */
export type MappingValue = string;

export interface FieldMapping {
  [fieldId: string]: MappingValue;
}

export interface FieldMappingOption {
  column?: string;
  defaultValue?: string;
}

export interface LogEntry {
  level: "INFO" | "WARN" | "ERROR" | "DEBUG";
  message: string;
  timestamp: string;
}

export interface WSEvent {
  type:
    | "snapshot"
    | "started"
    | "stream"
    | "paused"
    | "completed"
    | "failed";
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
  synthetic_emails_created: number;
}

export interface JobResult {
  total_rows?: number;
  processed?: number;
  inserted?: number;
  updated?: number;
  synthetic_emails_created?: number;
  errors?: string[];
  url_result?: UrlScrapeResult | null;
}

export interface JobSnapshot {
  job_id: string;
  status: JobStatus;
  payload: Record<string, unknown>;
  result?: JobResult | null;
  error?: string | null;
  created_at: string;
  started_at?: string | null;
  paused_at?: string | null;
  completed_at?: string | null;
  current_row?: number;
  total_rows?: number;
}
