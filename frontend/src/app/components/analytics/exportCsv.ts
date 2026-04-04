export interface CsvColumn {
  key: string;
  header: string;
  format?: (v: unknown) => string;
}

function escapeCell(value: unknown): string {
  const str = value == null ? "" : String(value);
  if (str.includes(",") || str.includes('"') || str.includes("\n")) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

export function exportCsv(
  data: Record<string, unknown>[],
  columns: CsvColumn[],
  filename: string,
) {
  const header = columns.map((c) => escapeCell(c.header)).join(",");
  const rows = data.map((row) =>
    columns
      .map((c) => {
        const raw = row[c.key];
        const val = c.format ? c.format(raw) : raw;
        return escapeCell(val);
      })
      .join(","),
  );
  const csv = "\uFEFF" + [header, ...rows].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function makeCsvFilename(widget: string, period: string): string {
  const date = new Date().toISOString().split("T")[0];
  return `fng_analytics_${widget}_${period}_${date}.csv`;
}
