import fs from "node:fs";

function csvEscape(value) {
  const text = value == null ? "" : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

const input = process.argv[2] || "data/superset-dashboard-117-listen-20260705-145716.jsonl";
const output = process.argv[3] || "data/superset-dashboard-117-teacher-class-detail.csv";
const sliceId = Number(process.argv[4] || 3191);

let target = null;
for (const line of fs.readFileSync(input, "utf8").trim().split(/\n/)) {
  if (!line.trim()) continue;
  let record;
  try {
    record = JSON.parse(line);
  } catch {
    continue;
  }
  if (record.event !== "body" || !record.responseBody) continue;
  const requestSliceId = record.postJson?.form_data?.slice_id;
  if (requestSliceId === sliceId) {
    target = record;
  }
}

if (!target) {
  throw new Error(`Could not find Superset chart body for slice_id=${sliceId} in ${input}`);
}

const columns = target.postJson?.queries?.[0]?.columns || target.postJson?.form_data?.all_columns || [];
const data = target.responseBody?.result?.[0]?.data || [];
const rows = Array.isArray(data)
  ? data.filter((row) => {
      const termId = row?.term_id ?? row?.termId ?? "";
      const termName = row?.term_name ?? row?.termName ?? "";
      const packageId = row?.package_id ?? row?.packageId ?? "";
      const packageName = row?.package_name ?? row?.packageName ?? "";
      return [termId, termName, packageId, packageName].some((value) => String(value ?? "").trim());
    })
  : [];

fs.writeFileSync(
  output,
  `\uFEFF${[columns.map(csvEscape).join(","), ...rows.map((row) => columns.map((column) => csvEscape(row[column])).join(","))].join("\n")}`,
  "utf8",
);

console.log(
  JSON.stringify(
    {
      input,
      output,
      sliceId,
      rowCount: rows.length,
      columns,
      sample: rows.slice(0, 5),
      filters: target.postJson?.queries?.[0]?.filters || [],
    },
    null,
    2,
  ),
);
