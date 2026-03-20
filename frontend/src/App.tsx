// src/App.tsx
import { useCallback, useEffect, useRef, useState } from 'react';
import { checkHealth, sendChatStream } from './api';
import ChatBubble from './components/ChatBubble';
import TypingIndicator from './components/TypingIndicator';
import type { ChatMessage, HealthResponse, ToolResult, StructuredResponse } from './types';
import styles from './App.module.css';

// ── Sidebar prompt list ──────────────────────────────────────────────────────

const SIDEBAR_PROMPTS = [
  { label: '📈 Monthly Revenue Trend',      text: 'Show me the monthly revenue trend' },
  { label: '📊 Category Breakdown',         text: 'Compare revenue across all product categories' },
  { label: '🥧 Payment Method Share',       text: 'What is the payment method distribution?' },
  { label: '🏆 Top Products Ranking',       text: 'Which products rank highest by revenue?' },
  { label: '📉 Category vs Category',       text: 'Compare Electronics vs Books vs Clothing revenue' },
  { label: '📅 Monthly by Category',        text: 'Show monthly revenue trends split by category' },
  { label: '💰 Overall KPIs',               text: 'What are the overall business KPIs?' },
  { label: '👤 Customer Lookup',            text: 'What has customer 109318 purchased?' },
  { label: '📦 Product Stats',              text: 'Show me detailed stats for product A' },
  { label: '🔄 Compare Customers',          text: 'Compare customer 109318 and customer 579675' },
  { label: '🏬 Top Stores Ranking',         text: 'Which stores have the highest revenue?' },
  { label: '💳 Discount Analysis',          text: 'What is the average discount by category?' },
];

const WELCOME_CHIPS = [
  'Show monthly revenue trends',
  'Compare revenue by category',
  'What are overall KPIs?',
  'Which products rank highest?',
  'Payment method distribution',
  'Show details for product A',
];

// ── Types ────────────────────────────────────────────────────────────────────

interface DisplayMessage {
  id: string;
  message: ChatMessage;
  toolResults?: ToolResult[];
  structured?: StructuredResponse | null;
  /** true while streaming tokens into this bubble */
  streaming?: boolean;
  /** tool calls in flight for this message */
  activeTools?: string[];
}

// ── Component ────────────────────────────────────────────────────────────────

export default function App() {
  const [displayMessages, setDisplayMessages] = useState<DisplayMessage[]>([]);
  const [history, setHistory]                 = useState<ChatMessage[]>([]);
  const [input, setInput]                     = useState('');
  const [loading, setLoading]                 = useState(false);
  const [error, setError]                     = useState<string | null>(null);
  const [health, setHealth]                   = useState<HealthResponse | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef    = useRef<HTMLTextAreaElement>(null);
  const abortRef       = useRef<AbortController | null>(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => { scrollToBottom(); }, [displayMessages, loading, scrollToBottom]);

  useEffect(() => {
    checkHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, []);

  const handleSend = useCallback(async (text?: string) => {
    const content = (text ?? input).trim();
    if (!content || loading) return;

    // Cancel any in-flight request
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setError(null);

    const userMsg: ChatMessage = { role: 'user', content };
    const newHistory: ChatMessage[] = [...history, userMsg];

    setDisplayMessages(prev => [...prev, { id: `user-${Date.now()}`, message: userMsg }]);
    setHistory(newHistory);
    setLoading(true);

    // Create a placeholder bot bubble that we'll stream into
    const botId = `bot-${Date.now()}`;
    const placeholderAssistant: ChatMessage = { role: 'assistant', content: '' };

    setDisplayMessages(prev => [
      ...prev,
      {
        id: botId,
        message: placeholderAssistant,
        streaming: true,
        activeTools: [],
      },
    ]);

    // Accumulate streamed text so we can update the bubble efficiently
    let streamedContent = '';

    await sendChatStream(
      newHistory,
      {
        onToken(token) {
          streamedContent += token;
          setDisplayMessages(prev =>
            prev.map(dm =>
              dm.id === botId
                ? { ...dm, message: { role: 'assistant', content: streamedContent } }
                : dm,
            ),
          );
        },

        onToolCall(tool) {
          setDisplayMessages(prev =>
            prev.map(dm =>
              dm.id === botId
                ? { ...dm, activeTools: [...(dm.activeTools ?? []), tool] }
                : dm,
            ),
          );
        },

        onToolDone(tool, _ok) {
          setDisplayMessages(prev =>
            prev.map(dm =>
              dm.id === botId
                ? {
                    ...dm,
                    activeTools: (dm.activeTools ?? []).filter(t => t !== tool),
                  }
                : dm,
            ),
          );
        },

        onDone(structured, toolResults, _metadata) {
          // Parse answer text out of structured if we have it
          const finalContent =
            structured?.answer ?? streamedContent;

          const assistantMsg: ChatMessage = { role: 'assistant', content: finalContent };

          setHistory(prev => [...prev, assistantMsg]);
          setDisplayMessages(prev =>
            prev.map(dm =>
              dm.id === botId
                ? {
                    ...dm,
                    message:      assistantMsg,
                    toolResults:  toolResults,
                    structured:   structured,
                    streaming:    false,
                    activeTools:  [],
                  }
                : dm,
            ),
          );
          setLoading(false);
        },

        onError(msg) {
          // If aborted by user, silently ignore
          if (controller.signal.aborted) return;
          setError(msg);
          // Remove empty placeholder bubble
          setDisplayMessages(prev => prev.filter(dm => dm.id !== botId));
          setHistory(prev => prev.slice(0, -1));
          setLoading(false);
        },
      },
      controller.signal,
    );
  }, [input, history, loading]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
    },
    [handleSend],
  );

  const handleClear = useCallback(() => {
    abortRef.current?.abort();
    setDisplayMessages([]);
    setHistory([]);
    setError(null);
    setLoading(false);
  }, []);

  // Status dot
  const statusCls =
    health === null              ? styles.offline :
    health.status === 'ok' && health.database === 'ok' ? styles.online  : styles.warn;

  const statusText =
    health === null              ? 'Backend offline'  :
    !health.openai_configured    ? 'No OpenAI key'    :
    health.database !== 'ok'     ? 'DB not ready'     : 'Ready';

  return (
    <div className={styles.app}>

      {/* ════════════════════════════════ SIDEBAR ════════════════════════════════ */}
      <aside className={styles.sidebar}>

        {/* Branding */}
        <div className={styles.sidebarHeader}>
          <div className={styles.logo}>
            <div className={styles.logoMark}>🛍️</div>
            <span className={styles.logoText}>Retail<span>AI</span></span>
          </div>
          <div className={styles.tagline}>Analytics Chat</div>
        </div>

        {/* Prompt list */}
        <div className={styles.sidebarSection}>
          <div className={styles.sectionLabel}>Suggestions</div>
          {SIDEBAR_PROMPTS.map(p => (
            <button
              key={p.text}
              className={styles.promptChip}
              onClick={() => handleSend(p.text)}
              disabled={loading}
            >
              {p.label}
            </button>
          ))}
        </div>

        {/* Footer: Clear + Status */}
        <div className={styles.sidebarFooter}>
          <button className={styles.clearBtn} onClick={handleClear}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6m4-6v6"/><path d="M9 6V4h6v2"/>
            </svg>
            Clear conversation
          </button>

          <div className={styles.statusBar}>
            <div className={`${styles.statusDot} ${statusCls}`} />
            <span>{statusText}</span>
            {health && (
              <span style={{ marginLeft: 'auto', fontSize: '11px' }}>
                {health.openai_configured ? '🔑' : '⚠️'}
              </span>
            )}
          </div>
        </div>
      </aside>

      {/* ═══════════════════════════════ MAIN AREA ═══════════════════════════════ */}
      <main className={styles.main}>

        {/* Top bar */}
        <div className={styles.chatHeader}>
          <div>
            <div className={styles.chatTitle}>Retail Analytics Chat</div>
            <div className={styles.chatSubtitle}>
              Trends · Comparisons · Rankings · Customer &amp; Product lookups
            </div>
          </div>
        </div>

        {/* Messages / Welcome */}
        {displayMessages.length === 0 ? (
          <div className={styles.welcome}>
            <div className={styles.welcomeIcon}>✦</div>
            <h1 className={styles.welcomeTitle}>What would you like to explore?</h1>
            <p className={styles.welcomeSubtitle}>
              Ask about revenue trends, category comparisons, product rankings,
              or look up any customer. Answers come with charts and data.
            </p>
            <div className={styles.welcomeGrid}>
              {WELCOME_CHIPS.map(chip => (
                <button
                  key={chip}
                  className={styles.welcomeChip}
                  onClick={() => handleSend(chip)}
                >
                  {chip}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className={styles.messages}>
            {displayMessages.map(dm => (
              <ChatBubble
                key={dm.id}
                message={dm.message}
                toolResults={dm.toolResults}
                structured={dm.structured}
                streaming={dm.streaming}
                activeTools={dm.activeTools}
              />
            ))}
            {/* Only show TypingIndicator when truly waiting (before first token) */}
            {loading && displayMessages[displayMessages.length - 1]?.message.content === '' && (
              <TypingIndicator />
            )}
            <div ref={messagesEndRef} />
          </div>
        )}

        {/* Error */}
        {error && (
          <div className={styles.errorBanner}>
            <span>⚠️ {error}</span>
            <button onClick={() => setError(null)} aria-label="Dismiss">✕</button>
          </div>
        )}

        {/* Composer */}
        <div className={styles.inputArea}>
          <div className={styles.inputWrapper}>
            <textarea
              ref={textareaRef}
              className={styles.textarea}
              value={input}
              onChange={e => { setInput(e.target.value); autoResize(); }}
              onKeyDown={handleKeyDown}
              placeholder="Ask about trends, comparisons, rankings, or any customer / product…"
              rows={1}
              disabled={loading}
            />
            <button
              className={styles.sendBtn}
              onClick={() => handleSend()}
              disabled={loading || !input.trim()}
              title="Send (Enter)"
              aria-label="Send message"
            >
              {loading ? (
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="6" y="6" width="12" height="12" rx="2"/>
                </svg>
              ) : (
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="22" y1="2" x2="11" y2="13"/>
                  <polygon points="22 2 15 22 11 13 2 9 22 2"/>
                </svg>
              )}
            </button>
          </div>
          <p className={styles.hint}>
            <strong>Enter</strong> to send · <strong>Shift + Enter</strong> for new line
          </p>
        </div>
      </main>
    </div>
  );
}
