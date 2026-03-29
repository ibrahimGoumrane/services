import { useEffect, useMemo, useState } from "react";
import { StepIndicator } from "./components/StepIndicator";
import { UploadStep } from "./components/UploadStep";
import { MappingStep } from "./components/MappingStep";
import { ProcessingStep } from "./components/ProcessingStep";
import { CompletionStep } from "./components/CompletionStep";
import { createJob, fetchJobs } from "./lib/api";
import { JobSettings, JobMetrics, JobSnapshot, LogEntry } from "./lib/types";
import { Database, AlertCircle, ListChecks } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
export function App() {
  const [step, setStep] = useState(1);
  // Job State
  const [csvInput, setCsvInput] = useState<File | string | null>(null);
  const [separator, setSeparator] = useState(",");
  const [headers, setHeaders] = useState<string[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  // Final State
  const [finalStatus, setFinalStatus] = useState<"completed" | "failed" | null>(
    null,
  );
  const [finalMetrics, setFinalMetrics] = useState<JobMetrics>({
    processed: 0,
    inserted: 0,
    updated: 0,
    errors: 0,
  });
  const [finalLogs, setFinalLogs] = useState<LogEntry[]>([]);
  const [finalError, setFinalError] = useState<string | undefined>();
  // UI State
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<JobSnapshot[]>([]);

  const toMetrics = (job: JobSnapshot): JobMetrics => ({
    processed: Number(job.result?.processed ?? 0),
    inserted: Number(job.result?.inserted ?? 0),
    updated: Number(job.result?.updated ?? 0),
    errors: Array.isArray(job.result?.errors) ? job.result.errors.length : 0,
  });

  const refreshJobs = async () => {
    try {
      const data = await fetchJobs();
      setJobs(data);
    } catch {
      // Keep UI usable if jobs listing fails temporarily.
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

    if (job.status === "completed" || job.status === "failed") {
      setFinalStatus(job.status);
      setFinalMetrics(toMetrics(job));
      setFinalLogs([]);
      setFinalError(job.error ?? undefined);
      setStep(4);
      return;
    }

    setFinalStatus(null);
    setFinalError(undefined);
    setStep(3);
  };

  const trackedJobs = useMemo(
    () =>
      jobs.filter((job) =>
        ["queued", "running", "completed", "failed"].includes(job.status),
      ),
    [jobs],
  );
  const handleUploadComplete = (
    selectedInput: File | string,
    selectedSeparator: string,
    detectedHeaders: string[],
  ) => {
    setCsvInput(selectedInput);
    setSeparator(selectedSeparator);
    setHeaders(detectedHeaders);
    setStep(2);
  };
  const handleMappingSubmit = async (
    mapping: Record<string, string>,
    settings: JobSettings,
  ) => {
    if (!csvInput) return;
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const newJobId = await createJob(
        csvInput,
        mapping,
        separator,
        settings.batchSize,
        settings.enableWebScraping,
        settings.skipGoogleSearch,
      );
      setJobId(newJobId);
      setStep(3);
      void refreshJobs();
    } catch (err) {
      setSubmitError(
        err instanceof Error ? err.message : "Failed to start job",
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
    setStep(4);
  };
  const resetWizard = () => {
    setStep(1);
    setCsvInput(null);
    setHeaders([]);
    setJobId(null);
    setFinalStatus(null);
    setFinalLogs([]);
    setFinalError(undefined);
  };
  return (
    <div className="min-h-screen flex flex-col font-sans relative overflow-hidden">
      {/* Header */}
      <header className="border-b border-slate-800/50 bg-slate-950/50 backdrop-blur-xl sticky top-0 z-50">
        <div className="max-w-5xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center shadow-lg shadow-violet-500/20 border border-white/10">
              <Database className="w-4 h-4 text-white" />
            </div>
            <span className="font-bold text-slate-50 tracking-tight text-lg">
              FormaFast
            </span>
            <span className="px-2.5 py-0.5 rounded-full bg-slate-800/80 text-[10px] font-mono text-slate-300 border border-slate-700/50 shadow-sm">
              API v1
            </span>
          </div>
          <div className="text-sm font-medium text-slate-400">
            Data Seeding Wizard
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 w-full max-w-5xl mx-auto px-6 py-10 flex flex-col relative z-10">
        <StepIndicator currentStep={step} />

        <div className="mt-10 flex-1">
          {trackedJobs.length > 0 && (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              className="mb-8 rounded-2xl border border-slate-800/80 bg-slate-900/45 p-4"
            >
              <div className="mb-3 flex items-center gap-2 text-slate-300">
                <ListChecks className="h-4 w-4 text-violet-400" />
                <span className="text-sm font-semibold">Background Jobs</span>
              </div>

              <div className="grid gap-2 sm:grid-cols-2">
                {trackedJobs.map((job) => (
                  <button
                    key={job.job_id}
                    type="button"
                    onClick={() => recoverJob(job)}
                    className="rounded-xl border border-slate-700/70 bg-slate-950/60 px-3 py-2 text-left transition hover:border-slate-600 hover:bg-slate-900"
                  >
                    <div className="text-xs font-mono text-slate-400">
                      {job.job_id}
                    </div>
                    <div className="mt-1 flex items-center justify-between">
                      <span className="text-sm font-medium text-slate-200 capitalize">
                        {job.status}
                      </span>
                      <span className="text-xs text-slate-500">
                        {job.created_at
                          ? new Date(job.created_at).toLocaleTimeString()
                          : ""}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            </motion.div>
          )}

          <AnimatePresence mode="wait">
            {submitError && step === 2 && (
              <motion.div
                initial={{
                  opacity: 0,
                  y: -10,
                }}
                animate={{
                  opacity: 1,
                  y: 0,
                }}
                exit={{
                  opacity: 0,
                  y: -10,
                }}
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
                key="step1"
                initial={{
                  opacity: 0,
                  y: 20,
                }}
                animate={{
                  opacity: 1,
                  y: 0,
                }}
                exit={{
                  opacity: 0,
                  y: -20,
                }}
                transition={{
                  duration: 0.4,
                  ease: "easeOut",
                }}
              >
                <UploadStep onNext={handleUploadComplete} />
              </motion.div>
            )}

            {step === 2 && (
              <motion.div
                key="step2"
                initial={{
                  opacity: 0,
                  y: 20,
                }}
                animate={{
                  opacity: 1,
                  y: 0,
                }}
                exit={{
                  opacity: 0,
                  y: -20,
                }}
                transition={{
                  duration: 0.4,
                  ease: "easeOut",
                }}
                className={
                  isSubmitting
                    ? "opacity-50 pointer-events-none transition-opacity duration-300"
                    : ""
                }
              >
                <MappingStep
                  headers={headers}
                  onBack={() => setStep(1)}
                  onSubmit={handleMappingSubmit}
                />
              </motion.div>
            )}

            {step === 3 && jobId && (
              <motion.div
                key="step3"
                initial={{
                  opacity: 0,
                  y: 20,
                }}
                animate={{
                  opacity: 1,
                  y: 0,
                }}
                exit={{
                  opacity: 0,
                  y: -20,
                }}
                transition={{
                  duration: 0.4,
                  ease: "easeOut",
                }}
              >
                <ProcessingStep jobId={jobId} onComplete={handleJobComplete} />
              </motion.div>
            )}

            {step === 4 && finalStatus && (
              <motion.div
                key="step4"
                initial={{
                  opacity: 0,
                  scale: 0.95,
                }}
                animate={{
                  opacity: 1,
                  scale: 1,
                }}
                transition={{
                  duration: 0.5,
                  ease: "easeOut",
                }}
              >
                <CompletionStep
                  status={finalStatus}
                  jobId={jobId}
                  metrics={finalMetrics}
                  logs={finalLogs}
                  error={finalError}
                  onReset={resetWizard}
                />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </main>
    </div>
  );
}
