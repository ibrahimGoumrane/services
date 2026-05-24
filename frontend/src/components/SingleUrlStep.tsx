import { useState } from "react";
import { Globe, Link2, Play, Settings2 } from "lucide-react";
import { motion } from "framer-motion";
import { JobSettings } from "../lib/types";

interface SingleUrlStepProps {
  onSubmit: (urls: string[], settings: JobSettings) => void;
  isSubmitting: boolean;
}

export function SingleUrlStep({ onSubmit, isSubmitting }: SingleUrlStepProps) {
  const [urlsText, setUrlsText] = useState("");
  const [settings, setSettings] = useState<JobSettings>({
    batchSize: 1,
    enableWebScraping: true,
    skipGoogleSearch: false,
    enablePersonSearch: true,
    enableCompanySearch: true,
  });

  const parsedUrls = urlsText
    .split("\n")
    .map((u) => u.trim())
    .filter(Boolean);

  const canSubmit = parsedUrls.length > 0 && !isSubmitting;

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex flex-col gap-6 max-w-3xl"
    >
      <div>
        <h2 className="text-2xl font-bold tracking-tight text-slate-50">
          Batch URL Scraping
        </h2>
        <p className="text-sm font-medium text-slate-400 mt-2">
          Enter multiple website URLs (one per line) to scrape contact data and
          store it directly in the database.
        </p>
      </div>

      <div className="glass-card rounded-2xl p-6 flex flex-col gap-5">
        <label className="text-sm font-semibold text-slate-300 flex items-center gap-2">
          <Link2 className="w-4 h-4 text-indigo-400" />
          Website URLs
          <span className="text-xs font-normal text-slate-500 ml-auto">
            {parsedUrls.length} URL{parsedUrls.length !== 1 ? "s" : ""}
          </span>
        </label>
        <div className="relative">
          <Globe className="w-4 h-4 text-slate-500 absolute left-3 top-3" />
          <textarea
            value={urlsText}
            onChange={(event) => setUrlsText(event.target.value)}
            placeholder="example.com&#10;https://another.com&#10;site.org"
            rows={6}
            className="w-full bg-slate-950 border border-slate-700 text-sm font-medium rounded-xl pl-10 pr-4 py-3 text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500 resize-none"
          />
        </div>
      </div>

      <div className="glass-card rounded-2xl p-6">
        <div className="mb-4 flex items-center gap-2 text-slate-300">
          <Settings2 className="w-4 h-4 text-indigo-400" />
          <span className="text-sm font-semibold">Scraping Settings</span>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="flex items-center gap-3 rounded-xl border border-slate-700/60 bg-slate-900/40 px-4 py-3">
            <input
              type="checkbox"
              checked={settings.enableWebScraping}
              onChange={(event) =>
                setSettings((previous) => ({
                  ...previous,
                  enableWebScraping: event.target.checked,
                }))
              }
              className="h-4 w-4 rounded border-slate-600 bg-slate-900 text-indigo-500"
            />
            <span className="text-sm font-medium text-slate-300">
              Enable web scraping
            </span>
          </label>

          <label className="flex items-center gap-3 rounded-xl border border-slate-700/60 bg-slate-900/40 px-4 py-3">
            <input
              type="checkbox"
              checked={settings.skipGoogleSearch}
              onChange={(event) =>
                setSettings((previous) => ({
                  ...previous,
                  skipGoogleSearch: event.target.checked,
                }))
              }
              className="h-4 w-4 rounded border-slate-600 bg-slate-900 text-indigo-500"
            />
            <span className="text-sm font-medium text-slate-300">
              Skip Google search fallback
            </span>
          </label>
        </div>
      </div>

      <div className="flex justify-end">
        <button
          type="button"
          disabled={!canSubmit}
          onClick={() => onSubmit(parsedUrls, settings)}
          className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-indigo-500 to-cyan-500 px-5 py-3 text-sm font-semibold text-white shadow-lg shadow-indigo-500/20 transition hover:shadow-indigo-500/35 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Play className="w-4 h-4" />
          {isSubmitting ? "Starting..." : "Start URL Job"}
        </button>
      </div>
    </motion.div>
  );
}
