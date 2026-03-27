import React, { useState, useRef, Children } from "react";
import {
  UploadCloud,
  FileText,
  AlertCircle,
  Loader2,
  ChevronRight,
  X,
} from "lucide-react";
import { fetchCsvHeaders } from "../lib/api";
import { motion, AnimatePresence } from "framer-motion";
interface UploadStepProps {
  onNext: (input: File | string, separator: string, headers: string[]) => void;
}
const SEPARATORS = [
  {
    label: "Comma (,)",
    value: ",",
  },
  {
    label: "Semicolon (;)",
    value: ";",
  },
  {
    label: "Tab (\\t)",
    value: "\t",
  },
  {
    label: "Pipe (|)",
    value: "|",
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
    y: 10,
  },
  visible: {
    opacity: 1,
    y: 0,
  },
};
type InputMode = "file" | "text";

export function UploadStep({ onNext }: UploadStepProps) {
  const [inputMode, setInputMode] = useState<InputMode>("file");
  const [isDragging, setIsDragging] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [csvText, setCsvText] = useState("");
  const [separator, setSeparator] = useState(",");
  const [headers, setHeaders] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFile = async (selectedFile: File) => {
    if (!selectedFile.name.endsWith(".csv")) {
      setError("Please upload a valid CSV file.");
      return;
    }
    setFile(selectedFile);
    setError(null);
    await loadHeaders(selectedFile, separator);
  };

  const handleTextInput = async (text: string) => {
    setCsvText(text);
    setError(null);
    if (text.trim()) {
      // Auto-load headers when text is pasted
      setTimeout(() => {
        loadHeadersFromText(text, separator);
      }, 100);
    }
  };

  const loadHeaders = async (f: File, sep: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const detectedHeaders = await fetchCsvHeaders(f, sep);
      if (detectedHeaders.length === 0) {
        setError("No headers found. Please check the file and separator.");
        setHeaders([]);
      } else {
        setHeaders(detectedHeaders);
      }
    } catch (err) {
      setError("Failed to parse CSV. Please check the format and separator.");
      setHeaders([]);
    } finally {
      setIsLoading(false);
    }
  };

  const loadHeadersFromText = async (text: string, sep: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const detectedHeaders = await fetchCsvHeaders(text, sep);
      if (detectedHeaders.length === 0) {
        setError("No headers found. Please check the text and separator.");
        setHeaders([]);
      } else {
        setHeaders(detectedHeaders);
      }
    } catch (err) {
      setError("Failed to parse CSV. Please check the format and separator.");
      setHeaders([]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSeparatorChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const newSep = e.target.value;
    setSeparator(newSep);
    if (inputMode === "file" && file) {
      loadHeaders(file, newSep);
    } else if (inputMode === "text" && csvText) {
      loadHeadersFromText(csvText, newSep);
    }
  };

  const switchMode = (mode: InputMode) => {
    setInputMode(mode);
    setFile(null);
    setCsvText("");
    setHeaders([]);
    setError(null);
  };

  const hasInput = inputMode === "file" ? !!file : csvText.trim().length > 0;

  return (
    <motion.div
      variants={containerVariants}
      initial="hidden"
      animate="visible"
      className="flex flex-col gap-8 max-w-4xl mx-auto"
    >
      <motion.div variants={itemVariants} className="text-center md:text-left">
        <h2 className="text-2xl font-bold tracking-tight text-slate-50">
          Upload Data
        </h2>
        <p className="text-sm font-medium text-slate-400 mt-2">
          Select a CSV file or paste CSV data to begin the seeding process.
        </p>
      </motion.div>

      {/* Mode Tabs */}
      <motion.div
        variants={itemVariants}
        className="flex gap-2 bg-slate-900/60 p-1 rounded-xl"
      >
        <button
          onClick={() => switchMode("file")}
          className={`flex-1 px-4 py-2.5 rounded-lg font-medium text-sm transition-all ${
            inputMode === "file"
              ? "bg-slate-800 text-slate-50 shadow-md"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          Upload File
        </button>
        <button
          onClick={() => switchMode("text")}
          className={`flex-1 px-4 py-2.5 rounded-lg font-medium text-sm transition-all ${
            inputMode === "text"
              ? "bg-slate-800 text-slate-50 shadow-md"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          Paste CSV Text
        </button>
      </motion.div>

      {/* File Upload Mode */}
      <AnimatePresence mode="wait">
        {inputMode === "file" && (
          <motion.div
            key="file-mode"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
          >
            <div
              className={`relative rounded-2xl p-1 transition-all duration-300 ease-out
                ${isDragging ? "scale-[1.02] shadow-2xl shadow-blue-500/20" : "hover:scale-[1.01]"}
              `}
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setIsDragging(false);
                if (e.dataTransfer.files && e.dataTransfer.files[0]) {
                  handleFile(e.dataTransfer.files[0]);
                }
              }}
            >
              {/* Animated Border Background */}
              <div
                className={`absolute inset-0 rounded-2xl opacity-50 transition-opacity duration-300 ${isDragging ? "animated-border-gradient opacity-100" : "bg-slate-800"}`}
              />

              <div
                className={`relative h-full w-full rounded-xl border-2 border-dashed flex flex-col items-center justify-center p-12 transition-colors duration-300
                ${isDragging ? "border-transparent bg-slate-900/90 backdrop-blur-xl" : "border-slate-700 bg-slate-900/60 backdrop-blur-xl hover:bg-slate-900/80 hover:border-slate-600"}
                ${file ? "border-slate-700/50 bg-slate-900/80" : ""}
              `}
              >
                <input
                  type="file"
                  accept=".csv"
                  className="hidden"
                  ref={fileInputRef}
                  onChange={(e) =>
                    e.target.files && handleFile(e.target.files[0])
                  }
                />

                <AnimatePresence mode="wait">
                  {file ? (
                    <motion.div
                      key="file-selected"
                      initial={{
                        opacity: 0,
                        scale: 0.95,
                      }}
                      animate={{
                        opacity: 1,
                        scale: 1,
                      }}
                      exit={{
                        opacity: 0,
                        scale: 0.95,
                      }}
                      className="flex flex-col items-center gap-4 w-full max-w-sm"
                    >
                      <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-blue-500/20 to-violet-500/20 flex items-center justify-center text-blue-400 border border-blue-500/20 shadow-inner">
                        <FileText className="w-8 h-8" />
                      </div>
                      <div className="text-center w-full">
                        <p className="text-base font-semibold text-slate-200 truncate px-4">
                          {file.name}
                        </p>
                        <p className="text-sm font-mono text-slate-500 mt-1">
                          {(file.size / 1024).toFixed(1)} KB
                        </p>
                      </div>
                      <motion.button
                        whileHover={{
                          scale: 1.05,
                        }}
                        whileTap={{
                          scale: 0.95,
                        }}
                        onClick={(e) => {
                          e.stopPropagation();
                          setFile(null);
                          setHeaders([]);
                          setError(null);
                        }}
                        className="flex items-center gap-1.5 text-xs font-medium text-slate-400 hover:text-rose-400 bg-slate-800/50 hover:bg-rose-500/10 px-3 py-1.5 rounded-full transition-colors mt-2 border border-slate-700/50 hover:border-rose-500/30"
                      >
                        <X className="w-3 h-3" /> Remove file
                      </motion.button>
                    </motion.div>
                  ) : (
                    <motion.div
                      key="upload-prompt"
                      initial={{
                        opacity: 0,
                      }}
                      animate={{
                        opacity: 1,
                      }}
                      exit={{
                        opacity: 0,
                      }}
                      className="flex flex-col items-center gap-5 cursor-pointer w-full"
                      onClick={() => fileInputRef.current?.click()}
                    >
                      <motion.div
                        animate={{
                          y: [0, -5, 0],
                        }}
                        transition={{
                          duration: 4,
                          repeat: Infinity,
                          ease: "easeInOut",
                        }}
                        className="w-16 h-16 rounded-2xl bg-slate-800/80 flex items-center justify-center text-slate-400 border border-slate-700 shadow-lg"
                      >
                        <UploadCloud className="w-8 h-8" />
                      </motion.div>
                      <div className="text-center">
                        <p className="text-base font-semibold text-slate-200">
                          Click to upload{" "}
                          <span className="text-slate-400 font-normal">
                            or drag and drop
                          </span>
                        </p>
                        <p className="text-sm font-medium text-slate-500 mt-2">
                          CSV files only
                        </p>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </div>
          </motion.div>
        )}

        {/* Text Paste Mode */}
        {inputMode === "text" && (
          <motion.div
            key="text-mode"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
          >
            <div className="relative rounded-2xl p-1">
              <div className="absolute inset-0 rounded-2xl opacity-50 bg-slate-800" />

              <div className="relative rounded-xl bg-slate-900/60 backdrop-blur-xl border border-slate-700 p-6 transition-colors hover:bg-slate-900/80 hover:border-slate-600">
                <textarea
                  value={csvText}
                  onChange={(e) => handleTextInput(e.target.value)}
                  placeholder="Paste your CSV data here. Example:&#10;name,email,company&#10;John Doe,john@example.com,ACME Corp&#10;Jane Smith,jane@example.com,Tech Inc"
                  className="w-full h-48 bg-slate-950 border border-slate-700 rounded-lg p-4 text-slate-200 placeholder-slate-600 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-violet-500/50 focus:border-violet-500/50 resize-none transition-all"
                />
                <p className="text-xs text-slate-500 mt-2">
                  {csvText.length} characters
                </p>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Settings & Preview */}
      <AnimatePresence>
        {hasInput && (
          <motion.div
            initial={{
              opacity: 0,
              height: 0,
              y: 20,
            }}
            animate={{
              opacity: 1,
              height: "auto",
              y: 0,
            }}
            exit={{
              opacity: 0,
              height: 0,
              y: -20,
            }}
            className="flex flex-col gap-5 glass-card rounded-2xl p-6 overflow-hidden"
          >
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
              <div>
                <label className="text-sm font-semibold text-slate-200 block">
                  CSV Separator
                </label>
                <span className="text-xs text-slate-500">
                  How your columns are divided
                </span>
              </div>
              <div className="relative">
                <select
                  value={separator}
                  onChange={handleSeparatorChange}
                  className="appearance-none bg-slate-950 border border-slate-700 text-sm font-medium rounded-xl pl-4 pr-10 py-2.5 text-slate-200 focus:outline-none focus:ring-2 focus:ring-violet-500/50 focus:border-violet-500/50 transition-all shadow-inner min-w-[160px]"
                >
                  {SEPARATORS.map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </select>
                <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-slate-500">
                  <ChevronRight className="w-4 h-4 rotate-90" />
                </div>
              </div>
            </div>

            <div className="h-px w-full bg-gradient-to-r from-transparent via-slate-700/50 to-transparent" />

            <div>
              <div className="flex items-center gap-3 mb-4">
                <h3 className="text-sm font-semibold text-slate-200">
                  Detected Headers
                </h3>
                {isLoading && (
                  <Loader2 className="w-4 h-4 animate-spin text-violet-400" />
                )}
              </div>

              {error ? (
                <motion.div
                  initial={{
                    opacity: 0,
                  }}
                  animate={{
                    opacity: 1,
                  }}
                  className="flex items-start gap-3 text-rose-400 bg-rose-500/10 p-4 rounded-xl border border-rose-500/20 shadow-inner"
                >
                  <AlertCircle className="w-5 h-5 mt-0.5 shrink-0" />
                  <p className="text-sm font-medium">{error}</p>
                </motion.div>
              ) : headers.length > 0 ? (
                <motion.div
                  initial="hidden"
                  animate="visible"
                  variants={containerVariants}
                  className="flex flex-wrap gap-2"
                >
                  {headers.map((h, i) => (
                    <motion.span
                      variants={itemVariants}
                      key={i}
                      className="px-3 py-1.5 rounded-lg bg-slate-800/80 text-xs font-mono font-medium text-slate-300 border border-slate-700/50 shadow-sm hover:border-slate-600 transition-colors cursor-default"
                    >
                      {h}
                    </motion.span>
                  ))}
                </motion.div>
              ) : (
                <p className="text-sm font-medium text-slate-500 italic bg-slate-900/50 p-4 rounded-xl border border-slate-800 border-dashed text-center">
                  No headers loaded yet.
                </p>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Action */}
      <motion.div variants={itemVariants} className="flex justify-end mt-2">
        <motion.button
          whileHover={
            !hasInput || headers.length === 0 || !!error || isLoading
              ? {}
              : {
                  scale: 1.02,
                }
          }
          whileTap={
            !hasInput || headers.length === 0 || !!error || isLoading
              ? {}
              : {
                  scale: 0.98,
                }
          }
          disabled={!hasInput || headers.length === 0 || !!error || isLoading}
          onClick={() => {
            const input = inputMode === "file" ? file! : csvText;
            onNext(input, separator, headers);
          }}
          className="flex items-center gap-2 bg-gradient-to-r from-blue-500 to-violet-500 text-white px-6 py-3 rounded-xl font-semibold text-sm shadow-lg shadow-violet-500/25 hover:shadow-violet-500/40 disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-none transition-all"
        >
          Continue to Mapping
          <ChevronRight className="w-4 h-4" />
        </motion.button>
      </motion.div>
    </motion.div>
  );
}
