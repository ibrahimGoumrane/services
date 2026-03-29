import { motion } from "framer-motion";
import {
  ArrowRight,
  CheckCircle2,
  Download,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { JobMetrics, LogEntry } from "../lib/types";
import { fetchJobLogs } from "../lib/api";
interface CompletionStepProps {
  status: "completed" | "failed";
  jobId: string | null;
  metrics: JobMetrics;
  logs: LogEntry[];
  error?: string;
  onReset: () => void;
}
const containerVariants = {
  hidden: {
    opacity: 0,
    scale: 0.95,
  },
  visible: {
    opacity: 1,
    scale: 1,
    transition: {
      duration: 0.5,
      ease: "easeOut",
      staggerChildren: 0.1,
    },
  },
};
const itemVariants = {
  hidden: {
    opacity: 0,
    y: 20,
  },
  visible: {
    opacity: 1,
    y: 0,
  },
};
export function CompletionStep({
  status,
  jobId,
  metrics,
  logs,
  error,
  onReset,
}: CompletionStepProps) {
  const isSuccess = status === "completed";

  const downloadText = (content: string) => {
    const blob = new Blob([content], {
      type: "text/plain;charset=utf-8",
    });
    const objectUrl = URL.createObjectURL(blob);
    const element = document.createElement("a");
    element.setAttribute("href", objectUrl);
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    element.setAttribute(
      "download",
      `job-${jobId || "unknown"}-logs-${timestamp}.txt`,
    );
    element.style.display = "none";
    document.body.appendChild(element);
    element.click();
    document.body.removeChild(element);
    URL.revokeObjectURL(objectUrl);
  };

  const handleExportLogs = async () => {
    if (logs.length > 0) {
      const lines = logs.map((entry) => {
        const ts = new Date(entry.timestamp || Date.now()).toISOString();
        return `[${ts}] ${entry.level} ${entry.message}`;
      });
      downloadText(lines.join("\n"));
      return;
    }

    try {
      if (!jobId) {
        downloadText(
          [
            `Status: ${status}`,
            `Processed: ${metrics.processed}`,
            `Inserted: ${metrics.inserted}`,
            `Updated: ${metrics.updated}`,
            `Errors: ${metrics.errors}`,
            error ? `Error: ${error}` : "",
          ]
            .filter(Boolean)
            .join("\n"),
        );
        return;
      }

      const logsContent = await fetchJobLogs(jobId);
      downloadText(logsContent);
    } catch (downloadError) {
      console.error("Failed to export logs", downloadError);
      downloadText(
        [
          `Status: ${status}`,
          `Processed: ${metrics.processed}`,
          `Inserted: ${metrics.inserted}`,
          `Updated: ${metrics.updated}`,
          `Errors: ${metrics.errors}`,
          error ? `Error: ${error}` : "",
          "",
          "Detailed logs were not available from the API endpoint.",
        ]
          .filter(Boolean)
          .join("\n"),
      );
    }
  };
  return (
    <motion.div
      variants={containerVariants}
      initial="hidden"
      animate="visible"
      className="flex flex-col items-center justify-center py-16"
    >
      <div className="w-full max-w-2xl flex flex-col items-center text-center">
        <motion.div variants={itemVariants} className="relative mb-8">
          <div
            className={`absolute inset-0 blur-3xl opacity-30 rounded-full scale-150 ${isSuccess ? "bg-emerald-500" : "bg-rose-500"}`}
          />

          <div
            className={`relative z-10 w-24 h-24 rounded-full flex items-center justify-center bg-slate-900 border-4 shadow-2xl ${isSuccess ? "border-emerald-500/30 shadow-emerald-500/20" : "border-rose-500/30 shadow-rose-500/20"}`}
          >
            {isSuccess ? (
              <CheckCircle2 className="w-12 h-12 text-emerald-400" />
            ) : (
              <XCircle className="w-12 h-12 text-rose-400" />
            )}
          </div>
        </motion.div>

        <motion.div variants={itemVariants}>
          <h2 className="text-3xl font-bold tracking-tight text-slate-50 mb-3">
            {isSuccess ? "Seeding Complete" : "Job Failed"}
          </h2>
          <p className="text-base font-medium text-slate-400 mb-10 max-w-md mx-auto">
            {isSuccess
              ? "Your data has been successfully processed and seeded into the system."
              : "An error occurred during the seeding process. Please check the details below."}
          </p>
        </motion.div>

        {/* Metrics Summary Grid */}
        <motion.div
          variants={itemVariants}
          className="w-full grid grid-cols-2 sm:grid-cols-4 gap-4 mb-10"
        >
          {[
            {
              label: "Processed",
              value: metrics.processed,
              color: "text-slate-100",
              border: "border-t-slate-500",
            },
            {
              label: "Inserted",
              value: metrics.inserted,
              color: "text-emerald-400",
              border: "border-t-emerald-500",
            },
            {
              label: "Updated",
              value: metrics.updated,
              color: "text-blue-400",
              border: "border-t-blue-500",
            },
            {
              label: "Errors",
              value: metrics.errors,
              color: "text-rose-400",
              border: "border-t-rose-500",
            },
          ].map((m) => (
            <motion.div
              key={m.label}
              whileHover={{
                y: -2,
              }}
              className={`glass-card rounded-2xl p-6 flex flex-col items-center justify-center border-t-2 ${m.border} transition-all`}
            >
              <span className={`text-3xl font-bold font-mono ${m.color}`}>
                {m.value}
              </span>
              <span className="text-xs font-semibold tracking-wider uppercase text-slate-500 mt-2">
                {m.label}
              </span>
            </motion.div>
          ))}
        </motion.div>

        {/* Error Details */}
        {!isSuccess && error && (
          <motion.div
            variants={itemVariants}
            className="w-full bg-rose-500/10 border border-rose-500/20 rounded-2xl p-6 mb-10 text-left shadow-inner"
          >
            <h4 className="text-sm font-bold tracking-wide uppercase text-rose-400 mb-2">
              Error Details
            </h4>
            <p className="text-sm text-rose-300/90 font-mono break-words leading-relaxed">
              {error}
            </p>
          </motion.div>
        )}

        {/* Actions */}
        <motion.div
          variants={itemVariants}
          className="flex flex-col sm:flex-row items-center justify-center gap-4 w-full max-w-md"
        >
          <motion.button
            whileHover={{
              scale: 1.02,
            }}
            whileTap={{
              scale: 0.98,
            }}
            onClick={onReset}
            className={`w-full flex items-center justify-center gap-2 px-6 py-3.5 rounded-xl font-semibold text-sm shadow-lg transition-all ${isSuccess ? "bg-gradient-to-r from-blue-500 to-violet-500 text-white shadow-violet-500/25 hover:shadow-violet-500/40" : "bg-slate-100 text-slate-900 hover:bg-white shadow-white/10"}`}
          >
            {isSuccess ? (
              <>
                Start New Job <ArrowRight className="w-4 h-4" />
              </>
            ) : (
              <>
                Try Again <RefreshCw className="w-4 h-4" />
              </>
            )}
          </motion.button>

          <motion.button
            whileHover={{
              scale: 1.02,
            }}
            whileTap={{
              scale: 0.98,
            }}
            onClick={handleExportLogs}
            className="w-full flex items-center justify-center gap-2 bg-slate-800/80 border border-slate-700 text-slate-300 px-6 py-3.5 rounded-xl font-semibold text-sm hover:bg-slate-700 hover:text-slate-100 transition-colors backdrop-blur-md"
          >
            <Download className="w-4 h-4" />
            Export Logs
          </motion.button>
        </motion.div>
      </div>
    </motion.div>
  );
}
