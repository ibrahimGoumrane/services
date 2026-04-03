import { HeadersResponse, JobResponse, JobSnapshot } from "./types";

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
  formData.append("csv_mapping", JSON.stringify(mapping));
  formData.append("csv_separator", separator);
  formData.append("batch_size", batchSize.toString());
  formData.append("enable_web_scraping", enableWebScraping.toString());
  formData.append("skip_google_search", skipGoogleSearch.toString());

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

export async function fetchJobLogs(jobId: string): Promise<string> {
  const response = await fetch(`${BASE_URL}/jobs/${jobId}/logs`);

  if (!response.ok) {
    throw new Error("Failed to fetch job logs");
  }

  return response.text();
}
