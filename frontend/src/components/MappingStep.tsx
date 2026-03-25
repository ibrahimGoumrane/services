import { useEffect, useState } from "react";
import { ChevronLeft, Play, Settings2, CheckCircle2 } from "lucide-react";
import { JobSettings } from "../lib/types";
import { motion } from "framer-motion";
interface MappingStepProps {
  headers: string[];
  onBack: () => void;
  onSubmit: (mapping: Record<string, string>, settings: JobSettings) => void;
}
const REQUIRED_FIELDS = [
  {
    id: "email",
    label: "Email Address",
  },
  {
    id: "company",
    label: "Company Name",
  },
];

const OPTIONAL_FIELDS = [
  {
    id: "url",
    label: "Website URL",
  },
  {
    id: "fname",
    label: "First Name",
  },
  {
    id: "lname",
    label: "Last Name",
  },
  {
    id: "position",
    label: "Position",
  },
  {
    id: "phone",
    label: "Phone Number",
  },
  {
    id: "mobile",
    label: "Mobile",
  },
  {
    id: "fax",
    label: "Fax",
  },
  {
    id: "address",
    label: "Address",
  },
  {
    id: "city",
    label: "City",
  },
  {
    id: "zip",
    label: "ZIP",
  },
  {
    id: "country",
    label: "Country",
  },
  {
    id: "urlcontactform",
    label: "Contact Form URL",
  },
  {
    id: "linkedin",
    label: "LinkedIn",
  },
  {
    id: "image",
    label: "Image",
  },
  {
    id: "mx",
    label: "MX",
  },
  {
    id: "emailgeneric",
    label: "Email Generic",
  },
  {
    id: "usergeneric",
    label: "User Generic",
  },
  {
    id: "syntaxeemail",
    label: "Email Syntax",
  },
  {
    id: "sourcefile",
    label: "Source File",
  },
];

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
export function MappingStep({ headers, onBack, onSubmit }: MappingStepProps) {
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [settings, setSettings] = useState<JobSettings>({
    batchSize: 100,
    enableWebScraping: true,
    skipGoogleSearch: false,
  });
  useEffect(() => {
    const initialMapping: Record<string, string> = {};
    [...REQUIRED_FIELDS, ...OPTIONAL_FIELDS].forEach((field) => {
      const match = headers.find(
        (h) =>
          h.toLowerCase() === field.id.toLowerCase() ||
          h.toLowerCase() === field.label.toLowerCase(),
      );
      if (match) {
        initialMapping[field.id] = match;
      }
    });
    setMapping(initialMapping);
  }, [headers]);
  const handleMapChange = (fieldId: string, headerValue: string) => {
    setMapping((prev) => ({
      ...prev,
      [fieldId]: headerValue,
    }));
  };
  const missingRequired = REQUIRED_FIELDS.filter((f) => !mapping[f.id]);
  const isReady = missingRequired.length === 0;
  return (
    <motion.div
      variants={containerVariants}
      initial="hidden"
      animate="visible"
      className="flex flex-col gap-8"
    >
      <motion.div variants={itemVariants}>
        <h2 className="text-2xl font-bold tracking-tight text-slate-50">
          Map Fields
        </h2>
        <p className="text-sm font-medium text-slate-400 mt-2">
          Match your CSV columns to the required system fields.
        </p>
      </motion.div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Mapping Form */}
        <div className="lg:col-span-2 flex flex-col gap-6">
          <motion.div
            variants={itemVariants}
            className="glass-card rounded-2xl overflow-hidden relative"
          >
            {/* Subtle gradient border top for required fields */}
            <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-blue-500 to-violet-500 opacity-50" />

            <div className="px-6 py-5 border-b border-slate-700/50 bg-slate-800/40 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <h3 className="text-base font-semibold text-slate-100">
                  Required Fields
                </h3>
                {isReady && (
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                )}
              </div>
              {missingRequired.length > 0 && (
                <span className="px-2.5 py-1 rounded-md bg-rose-500/10 border border-rose-500/20 text-xs font-semibold text-rose-400">
                  {missingRequired.length} missing
                </span>
              )}
            </div>
            <div className="p-6 flex flex-col gap-5">
              {REQUIRED_FIELDS.map((field) => (
                <div
                  key={field.id}
                  className="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-6 group"
                >
                  <div className="sm:w-1/3 flex items-center gap-1.5">
                    <label className="text-sm font-semibold text-slate-300 group-hover:text-slate-200 transition-colors">
                      {field.label}
                    </label>
                    <span className="text-rose-500 font-bold">*</span>
                  </div>
                  <div className="flex-1 relative">
                    <select
                      value={mapping[field.id] || ""}
                      onChange={(e) =>
                        handleMapChange(field.id, e.target.value)
                      }
                      className={`w-full appearance-none bg-slate-950 border text-sm font-medium rounded-xl pl-4 pr-10 py-2.5 focus:outline-none focus:ring-2 focus:ring-violet-500/50 transition-all shadow-inner
                        ${!mapping[field.id] ? "border-rose-500/50 text-slate-400 focus:border-rose-500" : "border-slate-700 text-slate-200 focus:border-violet-500"}
                      `}
                    >
                      <option value="" disabled>
                        Select column...
                      </option>
                      {headers.map((h) => (
                        <option key={h} value={h}>
                          {h}
                        </option>
                      ))}
                    </select>
                    <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-slate-500">
                      <ChevronLeft className="w-4 h-4 -rotate-90" />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </motion.div>

          <motion.div
            variants={itemVariants}
            className="glass-card rounded-2xl overflow-hidden"
          >
            <div className="px-6 py-5 border-b border-slate-700/50 bg-slate-800/40">
              <h3 className="text-base font-semibold text-slate-100">
                Optional Fields
              </h3>
              <p className="text-xs text-slate-400 mt-1">
                If Website URL is not mapped, we can try Google search using
                company details when enabled.
              </p>
            </div>
            <div className="p-6 flex flex-col gap-5">
              {OPTIONAL_FIELDS.map((field) => (
                <div
                  key={field.id}
                  className="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-6 group"
                >
                  <div className="sm:w-1/3">
                    <label className="text-sm font-semibold text-slate-400 group-hover:text-slate-300 transition-colors">
                      {field.label}
                    </label>
                  </div>
                  <div className="flex-1 relative">
                    <select
                      value={mapping[field.id] || ""}
                      onChange={(e) =>
                        handleMapChange(field.id, e.target.value)
                      }
                      className="w-full appearance-none bg-slate-950 border border-slate-700 text-sm font-medium rounded-xl pl-4 pr-10 py-2.5 text-slate-200 focus:outline-none focus:ring-2 focus:ring-violet-500/50 focus:border-violet-500 transition-all shadow-inner"
                    >
                      <option value="" className="text-slate-500">
                        Skip this field
                      </option>
                      {headers.map((h) => (
                        <option key={h} value={h}>
                          {h}
                        </option>
                      ))}
                    </select>
                    <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-slate-500">
                      <ChevronLeft className="w-4 h-4 -rotate-90" />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </motion.div>
        </div>

        {/* Settings Sidebar */}
        <motion.div variants={itemVariants} className="flex flex-col gap-6">
          <div className="glass-card rounded-2xl p-6 sticky top-24">
            <div className="flex items-center gap-2.5 mb-6 pb-4 border-b border-slate-700/50">
              <div className="p-1.5 rounded-lg bg-slate-800/80 border border-slate-700">
                <Settings2 className="w-4 h-4 text-slate-300" />
              </div>
              <h3 className="text-base font-semibold text-slate-100">
                Job Settings
              </h3>
            </div>

            <div className="flex flex-col gap-6">
              <div className="flex flex-col gap-2.5">
                <label className="text-sm font-semibold text-slate-300">
                  Batch Size
                </label>
                <input
                  type="number"
                  min="1"
                  max="1000"
                  value={settings.batchSize}
                  onChange={(e) =>
                    setSettings((p) => ({
                      ...p,
                      batchSize: parseInt(e.target.value) || 100,
                    }))
                  }
                  className="bg-slate-950 border border-slate-700 text-sm font-medium rounded-xl px-4 py-2.5 text-slate-200 focus:outline-none focus:ring-2 focus:ring-violet-500/50 focus:border-violet-500 transition-all shadow-inner"
                />
              </div>

              <div className="h-px w-full bg-slate-800/50" />

              <label className="flex items-center justify-between cursor-pointer group">
                <span className="text-sm font-semibold text-slate-300 group-hover:text-slate-100 transition-colors">
                  Enable Web Scraping
                </span>
                <div className="relative inline-flex items-center">
                  <input
                    type="checkbox"
                    className="sr-only peer"
                    checked={settings.enableWebScraping}
                    onChange={(e) =>
                      setSettings((p) => ({
                        ...p,
                        enableWebScraping: e.target.checked,
                      }))
                    }
                  />

                  <div className="w-11 h-6 bg-slate-800 border border-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-slate-300 after:border-slate-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-gradient-to-r peer-checked:from-blue-500 peer-checked:to-violet-500 peer-checked:border-transparent"></div>
                </div>
              </label>

              <label className="flex items-center justify-between cursor-pointer group">
                <span className="text-sm font-semibold text-slate-300 group-hover:text-slate-100 transition-colors">
                  Skip Google Search
                </span>
                <div className="relative inline-flex items-center">
                  <input
                    type="checkbox"
                    className="sr-only peer"
                    checked={settings.skipGoogleSearch}
                    onChange={(e) =>
                      setSettings((p) => ({
                        ...p,
                        skipGoogleSearch: e.target.checked,
                      }))
                    }
                  />

                  <div className="w-11 h-6 bg-slate-800 border border-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-slate-300 after:border-slate-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-gradient-to-r peer-checked:from-blue-500 peer-checked:to-violet-500 peer-checked:border-transparent"></div>
                </div>
              </label>
            </div>
          </div>
        </motion.div>
      </div>

      {/* Actions */}
      <motion.div
        variants={itemVariants}
        className="flex items-center justify-between mt-4 pt-6 border-t border-slate-800/50"
      >
        <motion.button
          whileHover={{
            scale: 1.02,
            x: -2,
          }}
          whileTap={{
            scale: 0.98,
          }}
          onClick={onBack}
          className="flex items-center gap-2 text-slate-400 hover:text-slate-200 px-4 py-2.5 rounded-xl text-sm font-semibold transition-colors"
        >
          <ChevronLeft className="w-4 h-4" /> Back
        </motion.button>
        <motion.button
          whileHover={
            !isReady
              ? {}
              : {
                  scale: 1.02,
                }
          }
          whileTap={
            !isReady
              ? {}
              : {
                  scale: 0.98,
                }
          }
          disabled={!isReady}
          onClick={() => onSubmit(mapping, settings)}
          className="flex items-center gap-2 bg-gradient-to-r from-blue-500 to-violet-500 text-white px-8 py-3 rounded-xl font-semibold text-sm shadow-lg shadow-violet-500/25 hover:shadow-violet-500/40 disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-none transition-all"
        >
          <Play className="w-4 h-4 fill-current" />
          Start Seeding Job
        </motion.button>
      </motion.div>
    </motion.div>
  );
}
