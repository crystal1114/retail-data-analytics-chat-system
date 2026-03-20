// src/App.tsx
import { useCallback, useEffect, useRef, useState } from 'react';
import { checkHealth, sendChat } from './api';
import ChatBubble from './components/ChatBubble';
import TypingIndicator from './components/TypingIndicator';
import type { ChatMessage, HealthResponse, ToolResult } from './types';
import styles from './App.module.css';

// ── Example prompts ─────────────────────────────────────────────────────────────

const SIDEBAR_PROMPTS = [
  { label: '👤 Customer History', text: 'What has customer 109318 purchased?' },
  { label: '💰 Customer Spend',   text: 'How much has customer 579675 spent in total?' },
  { label: '📦 Product Info',     text: 'Show me details for product A' },
  { label: '🏪 Product Stores',   text: 'Which stores sell product B?' },
  { label: '📊 Total Revenue',    text: 'What is the total revenue?' },
  { label: '📈 Monthly Trends',   text: 'Show monthly revenue trends' },
  { label: '🏆 Top Categories',   text: 'Which product categories generate the most revenue?' },
  { label: '🏬 Store Rankings',   text: 'Which stores generate the most sales?' },
  { label: '🔄 Compare Customers', text: 'Compare customer 109318 and customer 579675' },
  { label: '💳 Payment Methods',  text: 'What is the payment method breakdown?' },
];

const WELCOME_CHIPS = [
  'What is the total revenue?',
  'Show me details for product A',
  'What has customer 109318 purchased?',
  'Which categories generate the most revenue?',
  'Show monthly revenue trends',
  'Which stores have the most sales?',
];

// ── Types ────────────────────────────────────────────────────────────────────────

interface DisplayMessage {
  id: string;
  message: ChatMessage;
  toolResults?: ToolResult[];
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

  // ── Scroll to bottom ──────────────────────────────────────────────────────────
  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => { scrollToBottom(); }, [displayMessages, loading, scrollToBottom]);

  // ── Health check on mount ─────────────────────────────────────────────────────
  useEffect(() => {
    checkHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  // ── Auto-resize textarea ──────────────────────────────────────────────────────
  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, []);

  // ── Send message ──────────────────────────────────────────────────────────────
  const handleSend = useCallback(async (text?: string) => {
    const content = (text ?? input).trim();
    if (!content || loading) return;

    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
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
      const assistantMsg: ChatMessage = { role: 'assistant', content: response.reply };

      setHistory(prev => [...prev, assistantMsg]);
      setDisplayMessages(prev => [
        ...prev,
        {
          id: `bot-${Date.now()}`,
          message: assistantMsg,
          toolResults: response.tool_results,
        },
      ]);
    } catch (err: unknown) {
      const msg =
        err instanceof Error
          ? err.message
          : 'Failed to reach the backend. Is the server running?';
      setError(msg);
      // Remove the last user message from history so user can retry
      setHistory(prev => prev.slice(0, -1));
    } finally {
      setLoading(false);
    }
  }, [input, history, loading]);

  // ── Keyboard handler ──────────────────────────────────────────────────────────
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  // ── Clear chat ────────────────────────────────────────────────────────────────
  const handleClear = useCallback(() => {
    setDisplayMessages([]);
    setHistory([]);
    setError(null);
  }, []);

  // ── Health indicator ──────────────────────────────────────────────────────────
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
              Ask questions about customers, products, and business performance
            </div>
          </div>
        </div>

        {/* Messages */}
        {displayMessages.length === 0 ? (
          <div className={styles.welcome}>
            <div className={styles.welcomeIcon}>📊</div>
            <div className={styles.welcomeTitle}>What would you like to know?</div>
            <div className={styles.welcomeSubtitle}>
              Ask about any customer, product (A, B, C, D), or business metric.
              The assistant uses live data from the retail transaction database.
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
              placeholder="Ask about a customer, product, or business metric…"
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
