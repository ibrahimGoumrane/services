import { useEffect, useRef } from "react";
import { useWebSocket } from "../hooks/useWebSocket";
import { useJobPolling } from "../hooks/useJobPolling";
import { JobStatus, JobMetrics, LogEntry } from "../lib/types";
import { pauseJob, resumeJob } from "../lib/api";
import {
  Loader2,
  CheckCircle2,
  XCircle,
  Clock,
  Terminal,
  Activity,
  Pause,
  Play,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
interface ProcessingStepProps {
  jobId: string;
  onComplete: (
    status: "completed" | "failed",
    metrics: JobMetrics,
    error?: string,
    logs?: LogEntry[],
  ) => void;
}
const STATUS_ORDER = ["queued", "running", "paused", "completed"];
const containerVariants = {
  hidden: {
    opacity: 0,
  },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: 0.1,
    },
  },
};
const itemVariants = {
  hidden: {
    opacity: 0,
    y: 15,
  },
  visible: {
    opacity: 1,
    y: 0,
  },
};
export function ProcessingStep({ jobId, onComplete }: ProcessingStepProps) {
  const { status, logs, metrics, error, isConnected } = useWebSocket(jobId);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const isPaused = status === "paused";
  const isActive = status === "queued" || status === "running";
  const handlePollingUpdate = (
    polledStatus: JobStatus,
    polledMetrics?: JobMetrics,
    polledError?: string,
  ) => {
    if (polledStatus === "completed" || polledStatus === "failed") {
      onComplete(polledStatus, polledMetrics || metrics, polledError, logs);
    }
  };
  useJobPolling(jobId, status, isConnected, handlePollingUpdate);
  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({
        behavior: "smooth",
      });
    }
  }, [logs]);
  useEffect(() => {
    if (status === "completed" || status === "failed") {
      const timer = setTimeout(() => {
        onComplete(status, metrics, error || undefined, logs);
      }, 1500);
      return () => clearTimeout(timer);
    }
  }, [status, metrics, error, onComplete, logs]);
  const getStatusIcon = (stepStatus: string) => {
    if (status === "failed" && stepStatus === "running")
      return <XCircle className="w-5 h-5 text-rose-500" />;
    const currentIndex = STATUS_ORDER.indexOf(status || "queued");
    const stepIndex = STATUS_ORDER.indexOf(stepStatus);
    if (
      stepIndex < currentIndex ||
      (status === "completed" && stepStatus === "completed")
    ) {
      return <CheckCircle2 className="w-5 h-5 text-emerald-400" />;
    }
    if (
      stepIndex === currentIndex &&
      status !== "completed" &&
      status !== "failed"
    ) {
      return <Loader2 className="w-5 h-5 text-violet-400 animate-spin" />;
    }
    return <Clock className="w-5 h-5 text-slate-600" />;
  };
  const getLogLevelColor = (level: string) => {
    switch (level) {
      case "INFO":
        return "text-blue-400 bg-blue-500/10 border-blue-500/20";
      case "WARN":
        return "text-amber-400 bg-amber-500/10 border-amber-500/20";
      case "ERROR":
        return "text-rose-400 bg-rose-500/10 border-rose-500/20";
      case "DEBUG":
        return "text-slate-400 bg-slate-500/10 border-slate-500/20";
      default:
        return "text-slate-400 bg-slate-800 border-slate-700";
    }
  };
  return (
    <motion.div
      variants={containerVariants}
      initial="hidden"
      animate="visible"
      className="flex flex-col gap-8"
    >
      <motion.div
        variants={itemVariants}
        className="flex items-center justify-between"
      >
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-slate-50">
            Processing Job
          </h2>
          <p className="text-sm font-mono text-slate-400 mt-2 flex items-center gap-2">
            <span className="text-slate-500">ID:</span> {jobId}
          </p>
        </div>
        <div className="flex items-center gap-2.5 px-4 py-2 rounded-full bg-slate-900/80 border border-slate-700/50 backdrop-blur-md shadow-sm">
          <div
            className={`w-2.5 h-2.5 rounded-full ${isConnected ? "bg-emerald-400 animate-pulse-fast shadow-[0_0_8px_rgba(52,211,153,0.6)]" : "bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.6)]"}`}
          />

          <span className="text-xs font-bold tracking-wide uppercase text-slate-300">
            {isConnected ? "Live" : "Polling"}
          </span>
        </div>
      </motion.div>

      <motion.div variants={itemVariants} className="flex flex-wrap gap-3">
        {isActive && !isPaused && (
          <button
            type="button"
            onClick={() => void pauseJob(jobId)}
            className="inline-flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm font-semibold text-amber-300 transition hover:bg-amber-500/20"
          >
            <Pause className="h-4 w-4" />
            Stop
          </button>
        )}
        {isPaused && (
          <button
            type="button"
            onClick={() => void resumeJob(jobId)}
            className="inline-flex items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-sm font-semibold text-emerald-300 transition hover:bg-emerald-500/20"
          >
            <Play className="h-4 w-4" />
            Resume
          </button>
        )}
      </motion.div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Timeline & Metrics */}
        <div className="flex flex-col gap-8">
          <motion.div
            variants={itemVariants}
            className="glass-card rounded-2xl p-6"
          >
            <h3 className="text-sm font-semibold text-slate-200 mb-6 flex items-center gap-2 pb-4 border-b border-slate-700/50">
              <Activity className="w-4 h-4 text-violet-400" /> Status
            </h3>
            <div className="flex flex-col gap-6 relative before:absolute before:inset-0 before:ml-[11px] before:-translate-x-px before:h-full before:w-0.5 before:bg-gradient-to-b before:from-slate-700 before:via-slate-800 before:to-transparent">
              {["queued", "running", "paused", "completed"].map(
                (stepStatus) => {
                  const isActive =
                    status === stepStatus ||
                    (status === "failed" && stepStatus === "running");
                  return (
                    <div
                      key={stepStatus}
                      className="relative flex items-center gap-4 group z-10"
                    >
                      <div
                        className={`flex items-center justify-center w-6 h-6 rounded-full border-4 border-slate-900 bg-slate-800 shrink-0 transition-colors duration-300
                      ${isActive ? "border-slate-900 bg-slate-800 shadow-[0_0_10px_rgba(139,92,246,0.3)]" : ""}
                    `}
                      >
                        {stepStatus === "completed" && status === "failed" ? (
                          <Clock className="w-5 h-5 text-slate-600" />
                        ) : (
                          getStatusIcon(stepStatus)
                        )}
                      </div>
                      <div
                        className={`flex-1 p-4 rounded-xl border transition-all duration-300
                      ${isActive ? "bg-slate-800/80 border-slate-600 shadow-lg" : "bg-slate-900/50 border-slate-800/50"}
                    `}
                      >
                        <div className="font-semibold text-slate-200 text-sm capitalize">
                          {stepStatus === "completed" && status === "failed"
                            ? "Failed"
                            : stepStatus === "running"
                              ? "Processing"
                              : stepStatus === "paused"
                                ? "Paused"
                                : stepStatus}
                        </div>
                        <div className="text-xs font-medium text-slate-500 mt-1">
                          {stepStatus === "queued" && "Job accepted by server"}
                          {stepStatus === "running" &&
                            "Executing seeding tasks"}
                          {stepStatus === "paused" &&
                            "Paused and ready to resume"}
                          {stepStatus === "completed" && "Final job state"}
                        </div>
                      </div>
                    </div>
                  );
                },
              )}
            </div>
          </motion.div>

          <motion.div
            variants={itemVariants}
            className="glass-card rounded-2xl p-6"
          >
            <h3 className="text-sm font-semibold text-slate-200 mb-5 pb-4 border-b border-slate-700/50">
              Live Metrics
            </h3>
            <div className="grid grid-cols-2 gap-4">
              {[
                {
                  label: "Processed",
                  value: metrics.processed,
                  color: "text-slate-100",
                },
                {
                  label: "Inserted",
                  value: metrics.inserted,
                  color: "text-emerald-400",
                },
                {
                  label: "Updated",
                  value: metrics.updated,
                  color: "text-blue-400",
                },
                {
                  label: "Errors",
                  value: metrics.errors,
                  color: "text-rose-400",
                },
              ].map((m) => (
                <div
                  key={m.label}
                  className="bg-slate-950/50 border border-slate-800/80 rounded-xl p-4 shadow-inner"
                >
                  <div className="text-xs font-semibold text-slate-500 mb-1.5 uppercase tracking-wider">
                    {m.label}
                  </div>
                  <motion.div
                    key={m.value}
                    initial={{
                      scale: 1.2,
                      opacity: 0.5,
                    }}
                    animate={{
                      scale: 1,
                      opacity: 1,
                    }}
                    className={`text-2xl font-bold font-mono ${m.color}`}
                  >
                    {m.value}
                  </motion.div>
                </div>
              ))}
            </div>
          </motion.div>
        </div>

        {/* Streaming Logs */}
        <motion.div
          variants={itemVariants}
          className="lg:col-span-2 bg-[#0a0a0f] border border-slate-800 rounded-2xl flex flex-col overflow-hidden h-[600px] shadow-2xl shadow-black/40 relative"
        >
          <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-slate-800 via-slate-700 to-slate-800" />
          <div className="px-5 py-4 border-b border-slate-800/80 bg-slate-900/50 flex items-center justify-between backdrop-blur-md">
            <div className="flex items-center gap-2.5">
              <Terminal className="w-4 h-4 text-slate-400" />
              <h3 className="text-sm font-semibold text-slate-200">
                Server Logs
              </h3>
            </div>
            <span className="text-xs font-mono font-medium text-slate-500 bg-slate-950 px-2.5 py-1 rounded-md border border-slate-800">
              {logs.length} events
            </span>
          </div>

          <div className="flex-1 overflow-y-auto p-5 font-mono text-[13px] leading-relaxed flex flex-col gap-2">
            <AnimatePresence initial={false}>
              {logs.length === 0 ? (
                <motion.div
                  initial={{
                    opacity: 0,
                  }}
                  animate={{
                    opacity: 1,
                  }}
                  className="h-full flex items-center justify-center text-slate-600 italic font-sans"
                >
                  Waiting for logs...
                </motion.div>
              ) : (
                logs.map((log, i) => (
                  <motion.div
                    key={i}
                    initial={{
                      opacity: 0,
                      x: -10,
                    }}
                    animate={{
                      opacity: 1,
                      x: 0,
                    }}
                    className="flex items-start gap-3 hover:bg-slate-800/30 py-1.5 px-2.5 rounded-lg -mx-2.5 transition-colors group"
                  >
                    <span className="text-slate-600 shrink-0 select-none group-hover:text-slate-500 transition-colors">
                      {new Date(log.timestamp || Date.now()).toLocaleTimeString(
                        [],
                        {
                          hour12: false,
                        },
                      )}
                    </span>
                    <span
                      className={`px-2 py-0.5 rounded-md border text-[10px] font-bold tracking-wider shrink-0 ${getLogLevelColor(log.level)}`}
                    >
                      {log.level}
                    </span>
                    <span className="text-slate-300 break-all">
                      {log.message}
                    </span>
                  </motion.div>
                ))
              )}
            </AnimatePresence>
            <div ref={logsEndRef} />
          </div>
        </motion.div>
      </div>
    </motion.div>
  );
}
