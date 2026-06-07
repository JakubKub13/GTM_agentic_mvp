import { useRef, useState } from "react";
import { Upload } from "lucide-react";

import type { Company } from "@/lib/api";
import { cn } from "@/lib/utils";

import { CsvParseError, parseCompaniesCsv } from "./parseCompaniesCsv";

/** Read a File as text. Prefers `Blob.text()`; falls back to FileReader (jsdom has no `.text`). */
function readFileText(file: File): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsText(file);
  });
}

export interface FileDropzoneProps {
  /** Parsed companies, or `null` to use the repo-default companies.csv (plan #19). */
  companies: Company[] | null;
  onChange: (companies: Company[] | null) => void;
  disabled?: boolean;
}

/**
 * Upload companies.csv (or fall back to the repo default). Parses the file
 * client-side into `Company[]`; a `null` value means "let the server read its
 * repo-default companies.csv" (plan #19).
 */
export function FileDropzone({ companies, onChange, disabled }: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function ingest(file: File) {
    setError(null);
    try {
      const text = await readFileText(file);
      const parsed = parseCompaniesCsv(text);
      setFileName(file.name);
      onChange(parsed);
    } catch (e) {
      // Parse failed: drop back to the repo default. Only notify the parent if a
      // previously-parsed selection is being invalidated (avoids a spurious null call).
      setFileName(null);
      if (companies !== null) onChange(null);
      setError(e instanceof CsvParseError ? e.message : "Could not parse the CSV.");
    }
  }

  function clear() {
    setFileName(null);
    setError(null);
    onChange(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor="companies-csv" className="text-sm font-medium">
        Companies CSV
      </label>
      <label
        htmlFor="companies-csv"
        className={cn(
          "flex cursor-pointer items-center gap-3 rounded-md border border-dashed border-input bg-background p-4 text-sm shadow-sm transition-colors hover:bg-accent",
          disabled && "cursor-not-allowed opacity-50",
        )}
      >
        <Upload className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="min-w-0 flex-1">
          {companies && fileName ? (
            <>
              <span className="font-medium">{fileName}</span>
              <span className="text-muted-foreground"> · {companies.length} companies</span>
            </>
          ) : (
            <span className="text-muted-foreground">
              Drop or choose companies.csv — leave empty to use the repo default
            </span>
          )}
        </span>
      </label>
      <input
        id="companies-csv"
        ref={inputRef}
        type="file"
        accept=".csv,text/csv"
        aria-label="companies.csv"
        disabled={disabled}
        className="sr-only"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void ingest(file);
        }}
      />
      {companies && fileName ? (
        <button
          type="button"
          onClick={clear}
          disabled={disabled}
          className="self-start text-xs text-muted-foreground underline-offset-2 hover:underline"
        >
          Use repo default instead
        </button>
      ) : null}
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
