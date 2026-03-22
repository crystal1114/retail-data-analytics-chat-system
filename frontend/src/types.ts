// src/types.ts

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ToolResult {
  tool: string;
  args: Record<string, unknown>;
  result: {
    ok: boolean;
    data?: unknown;
    error?: string;
    message?: string;
    truncated?: boolean;
    has_more?: boolean;
    total_rows?: number;
    limit_injected?: boolean;
    fallback_mode?: string | null;
  };
}

// ── Visualization types ──────────────────────────────────────────────────────

export type VizType =
  | 'line_chart'
  | 'bar_chart'
  | 'horizontal_bar_chart'
  | 'pie_chart'
  | 'table'
  | 'kpi_card'
  | 'none';

export type IntentType =
  | 'customer_query'
  | 'product_query'
  | 'trend_query'
  | 'comparison_query'
  | 'ranking_query'
  | 'distribution_query'
  | 'kpi_query'
  | 'unsupported_query'
  | 'unknown';

export interface ChartDataset {
  label: string;
  data: number[];
  backgroundColor?: string | string[];
  borderColor?: string | string[];
  fill?: boolean;
}

export interface ChartData {
  // For line/bar/horizontal_bar charts
  labels?: string[];
  datasets?: ChartDataset[];
  // For kpi_card
  kpis?: Array<{ label: string; value: string; icon?: string; delta?: string }>;
  // For table
  columns?: string[];
  rows?: Array<Array<string | number>>;
}

export interface StructuredResponse {
  intent: IntentType;
  viz_type: VizType;
  insight: string;
  chart_data: ChartData | null;
  answer: string;
}

// ── Result metadata (truncation, pagination) ─────────────────────────────────

export interface ResultMeta {
  truncated?: boolean;
  has_more?: boolean;
  total_rows?: number;
  limit_injected?: boolean;
  fallback_mode?: 'broad_query' | 'timeout' | null;
  warning?: string;
}

export interface ChatResponse {
  reply: string;
  structured: StructuredResponse | null;
  tool_results: ToolResult[];
  metadata: Record<string, unknown> & ResultMeta;
}

export interface HealthResponse {
  status: string;
  database: string;
  openai_configured: boolean;
}

// ── Analysis / Thinking Mode ────────────────────────────────────────────────

export type AnalysisPhase =
  | 'idle'
  | 'planning'
  | 'executing'
  | 'reporting'
  | 'done'
  | 'error';

export interface AnalysisStepInfo {
  step_id: string;
  title: string;
  type: 'sql' | 'python';
  status: 'pending' | 'running' | 'done' | 'failed';
  summary?: string;
}

export interface AnalysisSection {
  title: string;
  content: string;
  table?: { columns: string[]; rows: Array<Array<string | number>> };
  chart_data?: Record<string, unknown>;
}

export interface AnalysisReport {
  executive_summary: string;
  sections: AnalysisSection[];
}

export type AnalysisSSEEvent =
  | { type: 'status'; phase: AnalysisPhase }
  | { type: 'plan'; steps: Array<{ step_id: string; title: string; type: string }> }
  | { type: 'step_start'; step_id: string; title: string; current: number; total: number }
  | { type: 'step_done'; step_id: string; status: 'ok' | 'failed'; summary?: string }
  | { type: 'report'; executive_summary: string; sections: AnalysisSection[] }
  | { type: 'done' }
  | { type: 'error'; message: string; partial_steps?: unknown[] };
