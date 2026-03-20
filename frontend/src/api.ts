// src/api.ts
import axios from 'axios';
import type { ChatMessage, ChatResponse, HealthResponse, StructuredResponse, ToolResult } from './types';

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

const client = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  timeout: 90_000,
});

// ── Non-streaming (kept for test / fallback) ─────────────────────────────────
export async function sendChat(messages: ChatMessage[]): Promise<ChatResponse> {
  const { data } = await client.post<ChatResponse>('/api/chat', { messages });
  return data;
}

export async function checkHealth(): Promise<HealthResponse> {
  const { data } = await client.get<HealthResponse>('/api/health');
  return data;
}

// ── SSE streaming types ───────────────────────────────────────────────────────

export type StreamEvent =
  | { type: 'token';     content: string }
  | { type: 'tool_call'; tool: string; status: string }
  | { type: 'tool_done'; tool: string; args: Record<string, unknown>; ok: boolean }
  | { type: 'done';
      structured: StructuredResponse | null;
      tool_results: ToolResult[];
      metadata: Record<string, unknown> }
  | { type: 'error'; message: string };

export interface StreamCallbacks {
  onToken:    (token: string) => void;
  onToolCall: (tool: string) => void;
  onToolDone: (tool: string, ok: boolean) => void;
  onDone:     (structured: StructuredResponse | null, toolResults: ToolResult[], metadata: Record<string, unknown>) => void;
  onError:    (msg: string) => void;
}

// ── Streaming send (SSE via fetch + ReadableStream) ──────────────────────────

export async function sendChatStream(
  messages: ChatMessage[],
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const url = `${BASE_URL}/api/chat/stream`;

  let response: Response;
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages }),
      signal,
    });
  } catch (err) {
    callbacks.onError(err instanceof Error ? err.message : 'Network error');
    return;
  }

  if (!response.ok) {
    callbacks.onError(`HTTP ${response.status}: ${response.statusText}`);
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) {
    callbacks.onError('No response body from server');
    return;
  }

  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // SSE lines are separated by \n\n; process complete events
      const parts = buffer.split('\n\n');
      buffer = parts.pop() ?? '';   // keep incomplete last chunk

      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith('data: ')) continue;

        const jsonStr = line.slice(6);
        let event: StreamEvent;
        try {
          event = JSON.parse(jsonStr);
        } catch {
          continue;
        }

        switch (event.type) {
          case 'token':
            callbacks.onToken(event.content);
            break;
          case 'tool_call':
            callbacks.onToolCall(event.tool);
            break;
          case 'tool_done':
            callbacks.onToolDone(event.tool, event.ok);
            break;
          case 'done':
            callbacks.onDone(event.structured, event.tool_results, event.metadata);
            break;
          case 'error':
            callbacks.onError(event.message);
            break;
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
