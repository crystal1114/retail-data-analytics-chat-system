// src/App.tsx
import { useCallback, useEffect, useRef, useState } from 'react';
import { checkHealth, sendChat } from './api';
import ChatBubble from './components/ChatBubble';
import TypingIndicator from './components/TypingIndicator';
import type { ChatMessage, HealthResponse, ToolResult, StructuredResponse } from './types';
import styles from './App.module.css';

// ── Example prompts ─────────────────────────────────────────────────────────────

const SIDEBAR_PROMPTS = [
  { label: '📈 Monthly Revenue Trend',      text: 'Show me the monthly revenue trend' },
  { label: '📊 Category Revenue Breakdown', text: 'Compare revenue across all product categories' },
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

// ── Types ────────────────────────────────────────────────────────────────────────

interface DisplayMessage {
  id: string;
  message: ChatMessage;
  toolResults?: ToolResult[];
  structured?: StructuredResponse | null;
}

// ── Component ────────────────────────────────────────────────────────────────────

export default function App() {
  const [displayMessages, setDisplayMessages] = useState<DisplayMessage[]>([]);
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => { scrollToBottom(); }, [displayMessages, loading, scrollToBottom]);

  useEffect(() => {
    checkHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
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

    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setError(null);

    const userMsg: ChatMessage = { role: 'user', content };
    const newHistory: ChatMessage[] = [...history, userMsg];

    setDisplayMessages(prev => [
      ...prev,
      { id: `user-${Date.now()}`, message: userMsg },
    ]);
    setHistory(newHistory);
    setLoading(true);

    try {
      const response = await sendChat(newHistory);
      const assistantMsg: ChatMessage = {
        role: 'assistant',
        content: response.reply,
      };

      setHistory(prev => [...prev, assistantMsg]);
      setDisplayMessages(prev => [
        ...prev,
        {
          id: `bot-${Date.now()}`,
          message: assistantMsg,
          toolResults: response.tool_results,
          structured: response.structured,
        },
      ]);
    } catch (err: unknown) {
      const msg =
        err instanceof Error
          ? err.message
          : 'Failed to reach the backend. Is the server running?';
      setError(msg);
      setHistory(prev => prev.slice(0, -1));
    } finally {
      setLoading(false);
    }
  }, [input, history, loading]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const handleClear = useCallback(() => {
    setDisplayMessages([]);
    setHistory([]);
    setError(null);
  }, []);

  const healthDotClass =
    health === null
      ? styles.offline
      : health.status === 'ok' && health.database === 'ok'
        ? styles.online
        : styles.warn;

  const healthText =
    health === null
      ? 'Backend offline'
      : !health.openai_configured
        ? 'No OpenAI key'
        : health.database !== 'ok'
          ? 'DB not ready'
          : 'Ready';

  return (
    <div className={styles.app}>
      {/* ── Sidebar ────────────────────────────────────────────────────────────── */}
      <aside className={styles.sidebar}>
        <div className={styles.sidebarHeader}>
          <div className={styles.logo}>
            <span className={styles.logoIcon}>🛍️</span>
            <span className={styles.logoText}>Retail<span>AI</span></span>
          </div>
          <div className={styles.tagline}>Analytics Chat Assistant</div>
        </div>

        <div className={styles.sidebarSection}>
          <div className={styles.sectionLabel}>Example Queries</div>
          {SIDEBAR_PROMPTS.map((p) => (
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

        <button className={styles.clearBtn} onClick={handleClear}>
          🗑️ Clear conversation
        </button>

        <div className={styles.statusBar}>
          <div className={`${styles.statusDot} ${healthDotClass}`} />
          <span>{healthText}</span>
          {health && (
            <span style={{ marginLeft: 'auto', color: 'var(--text-muted)' }}>
              {health.openai_configured ? '🔑' : '⚠️'}
            </span>
          )}
        </div>
      </aside>

      {/* ── Main area ──────────────────────────────────────────────────────────── */}
      <main className={styles.main}>
        <div className={styles.chatHeader}>
          <div>
            <div className={styles.chatTitle}>Retail Analytics Chat</div>
            <div className={styles.chatSubtitle}>
              Ask questions about trends, comparisons, rankings, and customer/product details
            </div>
          </div>
        </div>

        {/* Messages */}
        {displayMessages.length === 0 ? (
          <div className={styles.welcome}>
            <div className={styles.welcomeIcon}>📊</div>
            <div className={styles.welcomeTitle}>What would you like to explore?</div>
            <div className={styles.welcomeSubtitle}>
              Ask about trends, comparisons, rankings, or look up customers and products.
              Responses include interactive charts and visual insights.
            </div>
            <div className={styles.welcomeChips}>
              {WELCOME_CHIPS.map((chip) => (
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
            {displayMessages.map((dm) => (
              <ChatBubble
                key={dm.id}
                message={dm.message}
                toolResults={dm.toolResults}
                structured={dm.structured}
              />
            ))}
            {loading && <TypingIndicator />}
            <div ref={messagesEndRef} />
          </div>
        )}

        {/* Error banner */}
        {error && (
          <div className={styles.errorBanner}>
            <span>⚠️ {error}</span>
            <button onClick={() => setError(null)}>✕</button>
          </div>
        )}

        {/* Input */}
        <div className={styles.inputArea}>
          <div className={styles.inputWrapper}>
            <textarea
              ref={textareaRef}
              className={styles.textarea}
              value={input}
              onChange={(e) => { setInput(e.target.value); autoResize(); }}
              onKeyDown={handleKeyDown}
              placeholder="Ask about trends, comparisons, rankings, or specific customers/products…"
              rows={1}
              disabled={loading}
            />
            <button
              className={styles.sendBtn}
              onClick={() => handleSend()}
              disabled={loading || !input.trim()}
              title="Send (Enter)"
            >
              ➤
            </button>
          </div>
          <div className={styles.hint}>
            Press <strong>Enter</strong> to send · <strong>Shift+Enter</strong> for new line
          </div>
        </div>
      </main>
    </div>
  );
}
