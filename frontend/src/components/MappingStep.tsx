import { useEffect, useState } from "react";
import { ChevronLeft, Play, Settings2, CheckCircle2 } from "lucide-react";
import { JobSettings, FieldMapping } from "../lib/types";
import { motion } from "framer-motion";

interface MappingStepProps {
  headers: string[];
  onBack: () => void;
  onSubmit: (mapping: FieldMapping, settings: JobSettings) => void;
}

type FieldMappingMode = "skip" | "map" | "default";

interface FieldConfig {
  id: string;
  label: string;
  isRequired: boolean;
}

interface FieldState {
  mode: FieldMappingMode;
  column: string;
  defaultValue: string;
}

const REQUIRED_FIELDS: FieldConfig[] = [
  { id: "name", label: "Name", isRequired: true },
  { id: "company", label: "Company Name", isRequired: true },
  { id: "email", label: "Email Address", isRequired: true },
];

const OPTIONAL_FIELDS: FieldConfig[] = [
  { id: "url", label: "Website URL", isRequired: false },
  { id: "fname", label: "First Name", isRequired: false },
  { id: "lname", label: "Last Name", isRequired: false },
  { id: "position", label: "Position", isRequired: false },
  { id: "phone", label: "Phone Number", isRequired: false },
  { id: "mobile", label: "Mobile", isRequired: false },
  { id: "fax", label: "Fax", isRequired: false },
  { id: "address", label: "Address", isRequired: false },
  { id: "city", label: "City", isRequired: false },
  { id: "zip", label: "ZIP", isRequired: false },
  { id: "country", label: "Country", isRequired: false },
  { id: "urlcontactform", label: "Contact Form URL", isRequired: false },
  { id: "linkedin", label: "LinkedIn", isRequired: false },
  { id: "image", label: "Image", isRequired: false },
  { id: "mx", label: "MX", isRequired: false },
  { id: "emailgeneric", label: "Email Generic", isRequired: false },
  { id: "usergeneric", label: "User Generic", isRequired: false },
  { id: "syntaxeemail", label: "Email Syntax", isRequired: false },
  { id: "sourcefile", label: "Source File", isRequired: false },
];

const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { staggerChildren: 0.1 },
  },
};

const itemVariants = {
  hidden: { opacity: 0, y: 15 },
  visible: { opacity: 1, y: 0 },
};

// Helper to serialize field state to mapping value
function serializeFieldValue(state: FieldState): string {
  if (state.mode === "skip") return "";
  if (state.mode === "default") return `__default__:${state.defaultValue}`;
  return state.column; // mode === "map"
}

export function MappingStep({ headers, onBack, onSubmit }: MappingStepProps) {
  const [fieldStates, setFieldStates] = useState<Record<string, FieldState>>(
    {},
  );
  const [settings, setSettings] = useState<JobSettings>({
    batchSize: 20,
    enableWebScraping: true,
    skipGoogleSearch: false,
  });
  const [globalMode, setGlobalMode] = useState<"map" | "default">("map");
  const [globalDefaultValue, setGlobalDefaultValue] = useState("");

  // Initialize field states with auto-detection
  useEffect(() => {
    const initialStates: Record<string, FieldState> = {};

    [...REQUIRED_FIELDS, ...OPTIONAL_FIELDS].forEach((field) => {
      const match = headers.find(
        (h) =>
          h.toLowerCase() === field.id.toLowerCase() ||
          h.toLowerCase() === field.label.toLowerCase(),
      );
      if (match) {
        initialStates[field.id] = {
          mode: "map",
          column: match,
          defaultValue: "",
        };
      } else {
        initialStates[field.id] = {
          mode: "skip",
          column: "",
          defaultValue: "",
        };
      }
    });

    setFieldStates(initialStates);
  }, [headers]);

  const handleGlobalModeChange = (mode: "map" | "default") => {
    setGlobalMode(mode);
    if (mode === "default") {
      // Set all fields to default mode with the global value
      const newStates: Record<string, FieldState> = {};
      [...REQUIRED_FIELDS, ...OPTIONAL_FIELDS].forEach((field) => {
        const existing = fieldStates[field.id];
        newStates[field.id] = {
          mode: "default",
          column: existing?.column || headers[0] || "",
          defaultValue: globalDefaultValue,
        };
      });
      setFieldStates(newStates);
    } else {
      // Set all fields to map mode (keeping their existing column selections)
      const newStates: Record<string, FieldState> = {};
      [...REQUIRED_FIELDS, ...OPTIONAL_FIELDS].forEach((field) => {
        const existing = fieldStates[field.id];
        newStates[field.id] = {
          mode: existing?.column ? "map" : "skip",
          column: existing?.column || "",
          defaultValue: "",
        };
      });
      setFieldStates(newStates);
    }
  };

  const handleFieldModeChange = (fieldId: string, mode: FieldMappingMode) => {
    setFieldStates((prev) => ({
      ...prev,
      [fieldId]: {
        ...prev[fieldId],
        mode,
        column: mode === "map" ? prev[fieldId]?.column || headers[0] || "" : "",
        defaultValue:
          mode === "default"
            ? prev[fieldId]?.defaultValue || globalDefaultValue
            : "",
      },
    }));
  };

  const handleColumnChange = (fieldId: string, column: string) => {
    setFieldStates((prev) => ({
      ...prev,
      [fieldId]: { ...prev[fieldId], column },
    }));
  };

  const handleDefaultValueChange = (fieldId: string, defaultValue: string) => {
    setFieldStates((prev) => ({
      ...prev,
      [fieldId]: { ...prev[fieldId], defaultValue },
    }));
  };

  // Check if at least one required field is mapped (not skipped)
  const hasRequiredMapping = REQUIRED_FIELDS.some(
    (f) =>
      fieldStates[f.id]?.mode === "map" ||
      fieldStates[f.id]?.mode === "default",
  );

  const isReady = hasRequiredMapping;

  // Build the final mapping for submission
  const buildMapping = (): FieldMapping => {
    const mapping: FieldMapping = {};

    [...REQUIRED_FIELDS, ...OPTIONAL_FIELDS].forEach((field) => {
      const state = fieldStates[field.id];
      if (state) {
        const value = serializeFieldValue(state);
        if (value) mapping[field.id] = value;
      }
    });

    return mapping;
  };

  const handleSubmit = () => {
    const mapping = buildMapping();
    onSubmit(mapping, settings);
  };

  // Render a single field row
  const renderFieldRow = (field: FieldConfig) => {
    const state = fieldStates[field.id] || {
      mode: "skip" as FieldMappingMode,
      column: "",
      defaultValue: "",
    };
    const borderColor =
      field.isRequired && state.mode !== "skip"
        ? "border-emerald-500/30"
        : "border-slate-700/50";

    return (
      <div
        key={field.id}
        className={`flex flex-col sm:flex-row sm:items-center gap-3 p-4 rounded-xl bg-slate-800/30 border ${borderColor} transition-all`}
      >
        <div className="sm:w-2/5 flex items-center gap-2">
          <label
            className={`text-sm font-semibold ${field.isRequired ? "text-slate-200" : "text-slate-400"}`}
          >
            {field.label}
          </label>
          {field.isRequired && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 font-medium">
              Required
            </span>
          )}
        </div>

        <div className="sm:w-1/5">
          <select
            value={state.mode}
            onChange={(e) =>
              handleFieldModeChange(
                field.id,
                e.target.value as FieldMappingMode,
              )
            }
            className={`w-full text-xs font-medium rounded-lg px-3 py-2 border focus:outline-none focus:ring-2 focus:ring-violet-500/50 transition-all ${
              state.mode === "skip"
                ? "bg-slate-800 border-slate-600 text-slate-400"
                : state.mode === "default"
                  ? "bg-blue-500/20 border-blue-500/50 text-blue-400"
                  : "bg-emerald-500/20 border-emerald-500/50 text-emerald-400"
            }`}
          >
            <option value="map">Map from column</option>
            <option value="default">Set default</option>
            <option value="skip">Skip</option>
          </select>
        </div>

        <div className="sm:w-2/5">
          {state.mode === "map" && (
            <div className="relative">
              <select
                value={state.column}
                onChange={(e) => handleColumnChange(field.id, e.target.value)}
                className="w-full appearance-none bg-slate-950 border border-slate-700 text-sm font-medium rounded-xl pl-4 pr-10 py-2.5 text-slate-200 focus:outline-none focus:ring-2 focus:ring-violet-500/50 focus:border-violet-500 transition-all shadow-inner"
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
          )}

          {state.mode === "default" && (
            <input
              type="text"
              value={state.defaultValue}
              onChange={(e) =>
                handleDefaultValueChange(field.id, e.target.value)
              }
              placeholder="Enter default value..."
              className="w-full bg-slate-950 border border-slate-700 text-sm font-medium rounded-xl px-4 py-2.5 text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-violet-500/50 focus:border-violet-500 transition-all shadow-inner"
            />
          )}

          {state.mode === "skip" && (
            <p className="text-xs text-slate-500 italic">
              Field will not be included
            </p>
          )}
        </div>
      </div>
    );
  };

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
          Match your CSV columns to system fields. For each field, choose to
          skip, map from a column, or set a default value.
        </p>
      </motion.div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Mapping Form */}
        <div className="lg:col-span-2 flex flex-col gap-6">
          {/* Global Mode Selector */}
          <motion.div
            variants={itemVariants}
            className="glass-card rounded-2xl overflow-hidden"
          >
            <div className="px-6 py-5 border-b border-slate-700/50 bg-slate-800/40">
              <h3 className="text-base font-semibold text-slate-100">
                Mapping Mode
              </h3>
              <p className="text-xs text-slate-400 mt-1">
                Quick action: set all fields to map from columns or use a
                default value
              </p>
            </div>
            <div className="p-6 flex flex-col sm:flex-row gap-4 items-center">
              <div className="flex gap-3">
                <button
                  onClick={() => handleGlobalModeChange("map")}
                  className={`px-6 py-3 rounded-xl font-semibold text-sm transition-all ${
                    globalMode === "map"
                      ? "bg-emerald-500/20 border-2 border-emerald-500/50 text-emerald-400"
                      : "bg-slate-800 border border-slate-700 text-slate-400 hover:text-slate-200"
                  }`}
                >
                  Map from CSV
                </button>
                <button
                  onClick={() => handleGlobalModeChange("default")}
                  className={`px-6 py-3 rounded-xl font-semibold text-sm transition-all ${
                    globalMode === "default"
                      ? "bg-blue-500/20 border-2 border-blue-500/50 text-blue-400"
                      : "bg-slate-800 border border-slate-700 text-slate-400 hover:text-slate-200"
                  }`}
                >
                  Use Default Value
                </button>
              </div>

              {globalMode === "default" && (
                <div className="flex-1 flex items-center gap-2">
                  <span className="text-sm text-slate-400">Default:</span>
                  <input
                    type="text"
                    value={globalDefaultValue}
                    onChange={(e) => {
                      setGlobalDefaultValue(e.target.value);
                      // Update all default fields
                      setFieldStates((prev) => {
                        const newStates = { ...prev };
                        Object.keys(newStates).forEach((key) => {
                          if (newStates[key].mode === "default") {
                            newStates[key] = {
                              ...newStates[key],
                              defaultValue: e.target.value,
                            };
                          }
                        });
                        return newStates;
                      });
                    }}
                    placeholder="Enter value..."
                    className="flex-1 bg-slate-950 border border-slate-700 text-sm font-medium rounded-xl px-4 py-2.5 text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-violet-500/50 focus:border-violet-500 transition-all shadow-inner"
                  />
                </div>
              )}
            </div>
          </motion.div>

          {/* Required Fields */}
          <motion.div
            variants={itemVariants}
            className="glass-card rounded-2xl overflow-hidden relative"
          >
            <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-amber-500 to-orange-500 opacity-50" />
            <div className="px-6 py-5 border-b border-slate-700/50 bg-slate-800/40 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <h3 className="text-base font-semibold text-slate-100">
                  Required Fields
                </h3>
                {isReady && (
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                )}
              </div>
              <span className="text-xs text-slate-400">Map at least one</span>
            </div>
            <div className="p-6 flex flex-col gap-4">
              {REQUIRED_FIELDS.map((field) => renderFieldRow(field))}
            </div>
          </motion.div>

          {/* Optional Fields */}
          <motion.div
            variants={itemVariants}
            className="glass-card rounded-2xl overflow-hidden"
          >
            <div className="px-6 py-5 border-b border-slate-700/50 bg-slate-800/40">
              <h3 className="text-base font-semibold text-slate-100">
                Optional Fields
              </h3>
              <p className="text-xs text-slate-400 mt-1">
                These fields are optional. Skip, map from column, or set a
                default value.
              </p>
            </div>
            <div className="p-6 flex flex-col gap-4">
              {OPTIONAL_FIELDS.map((field) => renderFieldRow(field))}
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
                  <div className="w-11 h-6 bg-slate-800 border border-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-slate-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-gradient-to-r peer-checked:from-blue-500 peer-checked:to-violet-500 peer-checked:border-transparent"></div>
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
                  <div className="w-11 h-6 bg-slate-800 border border-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-slate-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-gradient-to-r peer-checked:from-blue-500 peer-checked:to-violet-500 peer-checked:border-transparent"></div>
                </div>
              </label>
            </div>
          </div>

          {/* Mapping Summary */}
          <div className="glass-card rounded-2xl p-6">
            <h4 className="text-sm font-semibold text-slate-100 mb-4">
              Mapping Summary
            </h4>
            <div className="space-y-2 text-xs max-h-64 overflow-y-auto">
              {[...REQUIRED_FIELDS, ...OPTIONAL_FIELDS]
                .filter((field) => {
                  const state = fieldStates[field.id];
                  return state && state.mode !== "skip";
                })
                .map((field) => {
                  const state = fieldStates[field.id];
                  return (
                    <div
                      key={field.id}
                      className="flex items-center justify-between py-1.5 px-2 rounded-lg bg-slate-800/50"
                    >
                      <span className="text-slate-400 truncate mr-2">
                        {field.label}
                      </span>
                      <span
                        className={`font-medium ${state.mode === "default" ? "text-blue-400" : "text-emerald-400"} truncate max-w-[150px]`}
                      >
                        {state.mode === "default"
                          ? `"${state.defaultValue}"`
                          : state.column}
                      </span>
                    </div>
                  );
                })}
              {Object.values(fieldStates).filter((s) => s.mode !== "skip")
                .length === 0 && (
                <p className="text-slate-500 italic">No fields mapped yet</p>
              )}
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
          whileHover={{ scale: 1.02, x: -2 }}
          whileTap={{ scale: 0.98 }}
          onClick={onBack}
          className="flex items-center gap-2 text-slate-400 hover:text-slate-200 px-4 py-2.5 rounded-xl text-sm font-semibold transition-colors"
        >
          <ChevronLeft className="w-4 h-4" /> Back
        </motion.button>
        <motion.button
          whileHover={!isReady ? {} : { scale: 1.02 }}
          whileTap={!isReady ? {} : { scale: 0.98 }}
          disabled={!isReady}
          onClick={handleSubmit}
          className="flex items-center gap-2 bg-gradient-to-r from-blue-500 to-violet-500 text-white px-8 py-3 rounded-xl font-semibold text-sm shadow-lg shadow-violet-500/25 hover:shadow-violet-500/40 disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-none transition-all"
        >
          <Play className="w-4 h-4 fill-current" />
          Start Seeding Job
        </motion.button>
      </motion.div>
    </motion.div>
  );
}
