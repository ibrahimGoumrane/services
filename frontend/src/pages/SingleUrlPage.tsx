import { useEffect, useMemo, useState } from "react";
import { StepIndicator } from "../components/StepIndicator";
import { SingleUrlStep } from "../components/SingleUrlStep";
import { ProcessingStep } from "../components/ProcessingStep";
import { CompletionStep } from "../components/CompletionStep";
import { createUrlJob, fetchJobStatus, fetchJobs } from "../lib/api";
import {
  JobSettings,
  JobMetrics,
  JobSnapshot,
  LogEntry,
  deriveDisplayStatus,
} from "../lib/types";
import { AlertCircle, ListChecks } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

export function SingleUrlPage() {
  const [step, setStep] = useState(1);
  const [jobId, setJobId] = useState<string | null>(null);
  const [finalStatus, setFinalStatus] = useState<"completed" | "failed" | null>(
    null,
  );
  const [finalMetrics, setFinalMetrics] = useState<JobMetrics>({
    processed: 0,
    inserted: 0,
    updated: 0,
    errors: 0,
    synthetic_emails_created: 0,
  });
  const [finalLogs, setFinalLogs] = useState<LogEntry[]>([]);
  const [finalError, setFinalError] = useState<string | undefined>();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<JobSnapshot[]>([]);

  const toMetrics = (job: JobSnapshot): JobMetrics => ({
    processed: Number(job.result?.processed ?? 0),
    inserted: Number(job.result?.inserted ?? 0),
    updated: Number(job.result?.updated ?? 0),
    errors: Array.isArray(job.result?.errors) ? job.result.errors.length : 0,
    synthetic_emails_created: Number(job.result?.synthetic_emails_created ?? 0),
  });

  const refreshJobs = async () => {
    try {
      const data = await fetchJobs();
      setJobs(data);
    } catch {
      /* keep UI usable */
    }
  };

  useEffect(() => {
    void refreshJobs();
    const interval = setInterval(() => {
      void refreshJobs();
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  const recoverJob = (job: JobSnapshot) => {
    setJobId(job.job_id);
    const displayStatus = deriveDisplayStatus(job);
    if (displayStatus === "completed" || displayStatus === "failed") {
      setFinalStatus(displayStatus);
      setFinalMetrics(toMetrics(job));
      setFinalLogs([]);
      setFinalError(job.error ?? undefined);
      setStep(3);
      return;
    }
    setFinalStatus(null);
    setFinalError(undefined);
    setStep(2);
  };

  const trackedJobs = useMemo(
    () =>
      jobs.filter((job) => {
        const display = deriveDisplayStatus(job);
        return ["queued", "running", "paused", "completed", "failed"].includes(
          display,
        );
      }),
    [jobs],
  );

  const handleSingleUrlSubmit = async (urls: string[], settings: JobSettings) => {
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const newJobId = await createUrlJob({
        urls,
        enable_web_scraping: settings.enableWebScraping,
        skip_google_search: settings.skipGoogleSearch,
      });
      setJobId(newJobId);
      setStep(2);
      void refreshJobs();
    } catch (err) {
      setSubmitError(
        err instanceof Error ? err.message : "Failed to start URL job",
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleJobComplete = (
    status: "completed" | "failed",
    metrics: JobMetrics,
    error?: string,
    logs?: LogEntry[],
  ) => {
    setFinalStatus(status);
    setFinalMetrics(metrics);
    setFinalLogs(logs || []);
    setFinalError(error);
    setStep(3);
    if (jobId) {
      void fetchJobStatus(jobId)
        .then((snapshot) => {
          setFinalMetrics(toMetrics(snapshot));
          setFinalError(snapshot.error ?? error);
        })
        .catch((e) => {
          console.error("Failed to fetch final job status:", e);
        });
    }
  };

  const resetPage = () => {
    setStep(1);
    setJobId(null);
    setFinalStatus(null);
    setFinalLogs([]);
    setFinalError(undefined);
  };

  const indicatorStep = step === 1 ? 1 : step === 3 ? 3 : 2;

  return (
    <div className="flex-1 w-full max-w-5xl mx-auto px-6 py-10 flex flex-col relative z-10">
      <StepIndicator currentStep={indicatorStep} mode="url" />

      <div className="mt-10 flex-1">
        {trackedJobs.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            className="mb-8 rounded-2xl border border-slate-800/60 glass-card p-4"
          >
            <div className="mb-3 flex items-center gap-2 text-slate-300">
              <ListChecks className="h-4 w-4 text-cyan-400" />
              <span className="text-sm font-semibold">Background Jobs</span>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {trackedJobs.map((job) => {
                const displayStatus = deriveDisplayStatus(job);
                return (
                  <button
                    key={job.job_id}
                    type="button"
                    onClick={() => recoverJob(job)}
                    className="rounded-xl border border-slate-700/60 bg-slate-950/50 px-3 py-2 text-left transition hover:border-slate-600 hover:bg-slate-900"
                  >
                    <div className="text-xs font-mono text-slate-400">
                      {job.job_id}
                    </div>
                    <div className="mt-1 flex items-center justify-between">
                      <span className="text-sm font-medium text-slate-200 capitalize">
                        {displayStatus}
                      </span>
                      <span className="text-xs text-slate-500">
                        {job.created_at
                          ? new Date(job.created_at).toLocaleTimeString()
                          : ""}
                      </span>
                    </div>
                    <div className="mt-1 text-xs text-slate-500">
                      {typeof job.current_row === "number" &&
                      typeof job.total_rows === "number"
                        ? `Row ${Math.max(1, job.current_row)} / ${Math.max(0, job.total_rows)}`
                        : "Progress unavailable"}
                    </div>
                  </button>
                );
              })}
            </div>
          </motion.div>
        )}

        <AnimatePresence mode="wait">
          {submitError && step === 1 && (
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              className="mb-8 flex items-start gap-3 bg-rose-500/10 border border-rose-500/20 rounded-2xl p-4 text-rose-400 shadow-lg shadow-rose-500/5"
            >
              <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
              <div>
                <h4 className="text-sm font-semibold text-rose-300">
                  Failed to start job
                </h4>
                <p className="text-sm mt-1 opacity-80">{submitError}</p>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence mode="wait">
          {step === 1 && (
            <motion.div
              key="url-input"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ duration: 0.4, ease: "easeOut" }}
            >
              <SingleUrlStep
                onSubmit={handleSingleUrlSubmit}
                isSubmitting={isSubmitting}
              />
            </motion.div>
          )}

          {step === 2 && jobId && (
            <motion.div
              key="processing"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ duration: 0.4, ease: "easeOut" }}
            >
              <ProcessingStep jobId={jobId} onComplete={handleJobComplete} />
            </motion.div>
          )}

          {step === 3 && finalStatus && (
            <motion.div
              key="completion"
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.5, ease: "easeOut" }}
            >
              <CompletionStep
                status={finalStatus}
                jobId={jobId}
                metrics={finalMetrics}
                logs={finalLogs}
                error={finalError}
                onReset={resetPage}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
