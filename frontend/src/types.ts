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

export interface ChatResponse {
  reply: string;
  tool_results: ToolResult[];
  metadata: Record<string, unknown>;
}

export interface HealthResponse {
  status: string;
  database: string;
  openai_configured: boolean;
}
