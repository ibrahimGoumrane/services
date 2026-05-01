import {
  HeadersResponse,
  JobResponse,
  JobSnapshot,
  UrlScrapeRequest,
} from "./types";

const BASE_URL = "http://127.0.0.1:8000";

export async function fetchCsvHeaders(
  input: File | string,
  separator = ",",
): Promise<string[]> {
  const formData = new FormData();

  if (input instanceof File) {
    formData.append("csv_file", input);
  } else {
    formData.append("csv_text", input);
  }
  formData.append("csv_separator", separator);

  const response = await fetch(`${BASE_URL}/jobs/csv/headers`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    throw new Error("Failed to parse CSV headers");
  }

  const data: HeadersResponse = await response.json();
  return data.headers;
}

export async function createJob(
  input: File | string,
  mapping: Record<string, string>,
  separator: string,
  batchSize: number,
  enableWebScraping: boolean,
  skipGoogleSearch: boolean,
): Promise<string> {
  const formData = new FormData();

  if (input instanceof File) {
    formData.append("csv_file", input);
  } else {
    formData.append("csv_text", input);
  }

  // Extract explicit default values from __default__: mapping entries so the
  // seeding service receives them both through the mapping (data_transformers
  // handles __default__: in-place) and through the dedicated default_values
  // dict (used as a fallback when a mapped CSV column is empty).
  const defaultValues: Record<string, string> = {};
  for (const [field, value] of Object.entries(mapping)) {
    if (value.startsWith("__default__:")) {
      defaultValues[field] = value.slice("__default__:".length);
    }
  }

  formData.append("csv_mapping", JSON.stringify(mapping));
  formData.append("csv_separator", separator);
  formData.append("batch_size", batchSize.toString());
  formData.append("enable_web_scraping", enableWebScraping.toString());
  formData.append("skip_google_search", skipGoogleSearch.toString());
  if (Object.keys(defaultValues).length > 0) {
    formData.append("default_values", JSON.stringify(defaultValues));
  }

  const response = await fetch(`${BASE_URL}/jobs`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    throw new Error("Failed to create job");
  }

  const data: JobResponse = await response.json();
  return data.job_id;
}

export async function createUrlJob(payload: UrlScrapeRequest): Promise<string> {
  const response = await fetch(`${BASE_URL}/jobs/url`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error("Failed to create URL job");
  }

  const data: JobResponse = await response.json();
  return data.job_id;
}

async function postJobAction(
  jobId: string,
  action: "pause" | "resume" | "stop",
): Promise<JobSnapshot> {
  const response = await fetch(`${BASE_URL}/jobs/${jobId}/${action}`, {
    method: "POST",
  });

  if (!response.ok) {
    throw new Error(`Failed to ${action} job`);
  }

  return response.json();
}

export function pauseJob(jobId: string): Promise<JobSnapshot> {
  return postJobAction(jobId, "pause");
}

export function resumeJob(jobId: string): Promise<JobSnapshot> {
  return postJobAction(jobId, "resume");
}

export function stopJob(jobId: string): Promise<JobSnapshot> {
  return postJobAction(jobId, "stop");
}

export async function fetchJobStatus(jobId: string): Promise<JobSnapshot> {
  const response = await fetch(`${BASE_URL}/jobs/${jobId}`);

  if (!response.ok) {
    throw new Error("Failed to fetch job status");
  }

  return response.json();
}

export async function fetchJobs(): Promise<JobSnapshot[]> {
  const response = await fetch(`${BASE_URL}/jobs`);

  if (!response.ok) {
    throw new Error("Failed to fetch jobs");
  }

  return response.json();
}

