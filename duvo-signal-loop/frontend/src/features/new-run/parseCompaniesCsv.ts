import type { Company } from "@/lib/api";

/** Cap on parsed rows — mirrors the backend's CSV-trust guard (plan risk: "CSV upload trust"). */
export const MAX_COMPANY_ROWS = 1000;

export class CsvParseError extends Error {}

/** Split a single CSV line, honoring double-quoted fields (with "" escapes). */
function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      out.push(field);
      field = "";
    } else {
      field += ch;
    }
  }
  out.push(field);
  return out.map((f) => f.trim());
}

/**
 * Parse a companies.csv into the `Company[]` the API expects (plan #19 FileDropzone).
 *
 * Requires `name` and `domain` columns; `country` and `description` are optional.
 * Skips blank lines, coerces missing optional cells to undefined, and caps row count.
 *
 * @throws CsvParseError on missing required columns, no data rows, or too many rows.
 */
export function parseCompaniesCsv(text: string): Company[] {
  const lines = text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l.length > 0);

  if (lines.length === 0) {
    throw new CsvParseError("The CSV is empty.");
  }

  const header = splitCsvLine(lines[0]).map((h) => h.toLowerCase());
  const idx = {
    name: header.indexOf("name"),
    domain: header.indexOf("domain"),
    country: header.indexOf("country"),
    description: header.indexOf("description"),
  };
  if (idx.name === -1 || idx.domain === -1) {
    throw new CsvParseError('CSV must have "name" and "domain" columns.');
  }

  const rows = lines.slice(1);
  if (rows.length === 0) {
    throw new CsvParseError("The CSV has a header but no data rows.");
  }
  if (rows.length > MAX_COMPANY_ROWS) {
    throw new CsvParseError(`Too many rows (${rows.length} > ${MAX_COMPANY_ROWS}).`);
  }

  const companies: Company[] = [];
  for (const [i, row] of rows.entries()) {
    const cells = splitCsvLine(row);
    const name = cells[idx.name] ?? "";
    const domain = cells[idx.domain] ?? "";
    if (!name || !domain) {
      throw new CsvParseError(`Row ${i + 2}: "name" and "domain" are required.`);
    }
    const country = idx.country === -1 ? "" : (cells[idx.country] ?? "");
    const description = idx.description === -1 ? "" : (cells[idx.description] ?? "");
    companies.push({
      name,
      domain,
      ...(country ? { country } : {}),
      ...(description ? { description } : {}),
    });
  }
  return companies;
}
