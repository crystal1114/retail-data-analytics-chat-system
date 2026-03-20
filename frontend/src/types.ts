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

export interface ChatResponse {
  reply: string;
  structured: StructuredResponse | null;
  tool_results: ToolResult[];
  metadata: Record<string, unknown>;
}

export interface HealthResponse {
  status: string;
  database: string;
  openai_configured: boolean;
}
