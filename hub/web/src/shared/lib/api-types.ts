// API response types mirroring the backend contract.

export interface HealthResponse {
  status: string;
  service: string;
  ts: string;
}

export type ScriptGroup = "bq_reports" | "scripts" | "adhoc";

export interface ScriptListItem {
  id: string;
  path: string;
  group: ScriptGroup;
  name: string;
  whoAsked: string;
  what: string;
  lastRunAt: string | null;
}

export interface ScriptDetail {
  id: string;
  path: string;
  group: ScriptGroup;
  name: string;
  whoAsked: string;
  what: string;
  source: string;
}

export interface ScriptsResponse {
  scripts: ScriptListItem[];
}

export interface RunResponse {
  runId: string;
}

export interface ReportFile {
  file: string;
  sizeKb: number;
  mtime: string;
  month: string;
  version: string;
}

export interface ReportGroup {
  name: string;
  files: ReportFile[];
}

export interface ReportsResponse {
  groups: ReportGroup[];
}

export interface ReportPreview {
  sheetNames: string[];
  sheet: number;
  total: number;
  header: string[];
  rows: string[][];
}

export interface DataDictField {
  name: string;
  type: string;
  comment: string;
}

export interface DataDictTable {
  table: string;
  description: string;
  fields: DataDictField[];
}

export interface DataDictTablesResponse {
  tables: DataDictTable[];
}

export interface DataDictMetric {
  name: string;
  definition: string;
  formula: string;
}

export interface DataDictMetricsResponse {
  metrics: DataDictMetric[];
}

export interface MetricFormula {
  business: string;
  sqlRefs: string[];
  excel: string | null;
}

export interface MetricLineage {
  sourceTables: string[];
  upstreamMetrics: string[];
}

export interface MetricReconciliation {
  anchor: string | null;
  impl: string | null;
  status: string | null;
}

export interface Metric {
  id: string;
  anchor: string;
  name: string;
  domain: string;
  status: string;
  statusLabel: string;
  confidence: "ACTUAL" | "ESTIMATED" | "NA";
  confidenceLabel: string;
  definition: string;
  grain: string | null;
  unit: string | null;
  formula: MetricFormula;
  lineage: MetricLineage;
  reconciliation: MetricReconciliation | null;
  industryBenchmark: string | null;
  currentValue: string | null;
  reportDisplay: string | null;
  notes: string | null;
  relatedDocs: string[];
}

export interface MetricDomain {
  key: string;
  label: string;
  metrics: Metric[];
}

export interface MetricsResponse {
  domains: MetricDomain[];
}

export interface AuditFile {
  file: string;
  kind: "csv" | "txt";
  sizeKb: number;
}

export interface AuditRun {
  dir: string;
  files: AuditFile[];
}

export interface AuditRunsResponse {
  runs: AuditRun[];
}

export type AuditFileContent =
  | { kind: "csv"; header: string[]; rows: string[][] }
  | { kind: "txt"; text: string };
