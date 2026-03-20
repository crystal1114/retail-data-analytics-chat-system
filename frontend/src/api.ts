// src/api.ts
import axios from 'axios';
import type { ChatMessage, ChatResponse, HealthResponse } from './types';

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

const client = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  timeout: 60_000,
});

export async function sendChat(messages: ChatMessage[]): Promise<ChatResponse> {
  const { data } = await client.post<ChatResponse>('/api/chat', { messages });
  return data;
}

export async function checkHealth(): Promise<HealthResponse> {
  const { data } = await client.get<HealthResponse>('/api/health');
  return data;
}
