// src/api.ts
import axios from 'axios';
import type { ChatMessage, ChatResponse, HealthResponse, AnalysisSSEEvent } from './types';

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

const client = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  timeout: 90_000,
});

export async function sendChat(messages: ChatMessage[]): Promise<ChatResponse> {
  const { data } = await client.post<ChatResponse>('/api/chat', { messages });
  return data;
}

export async function checkHealth(): Promise<HealthResponse> {
  const { data } = await client.get<HealthResponse>('/api/health');
  return data;
}

// ── Voice transcription ──────────────────────────────────────────────────────

export async function transcribeAudio(audioBlob: Blob): Promise<string> {
  const form = new FormData();
  form.append('file', audioBlob, 'recording.webm');
  const { data } = await client.post<{ text: string }>('/api/transcribe', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 30_000,
  });
  return data.text;
}

// ── Analysis SSE stream ─────────────────────────────────────────────────────

export function streamAnalysis(
  prompt: string,
  onEvent: (event: AnalysisSSEEvent) => void,
): AbortController {
  const controller = new AbortController();
  const url = `${BASE_URL}/api/analysis`;

  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt }),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok || !res.body) {
        onEvent({ type: 'error', message: `HTTP ${res.status}: ${res.statusText}` });
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const messages = buffer.split('\n\n');
        buffer = messages.pop() || '';

        for (const msg of messages) {
          const lines = msg.trim().split('\n');
          let eventType = '';
          let dataStr = '';

          for (const line of lines) {
            if (line.startsWith('event: ')) eventType = line.slice(7);
            else if (line.startsWith('data: ')) dataStr = line.slice(6);
          }

          if (!eventType || !dataStr) continue;

          try {
            const payload = JSON.parse(dataStr);
            onEvent({ type: eventType, ...payload } as AnalysisSSEEvent);
          } catch {
            // skip malformed events
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onEvent({ type: 'error', message: err.message || 'Stream connection failed' });
      }
    });

  return controller;
}
