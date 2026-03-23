// src/App.tsx
import { useCallback, useEffect, useRef, useReducer, useState } from 'react';
import { checkHealth, sendChat } from './api';
import AnalysisView, {
  analysisReducer,
  analysisInitialState,
} from './components/AnalysisView';
import ChatBubble from './components/ChatBubble';
import TypingIndicator from './components/TypingIndicator';
import { useMicRecorder } from './hooks/useMicRecorder';
import type { ChatMessage, HealthResponse, ToolResult, StructuredResponse, ResultMeta } from './types';
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
  resultMeta?: ResultMeta;
}

// ── Component ────────────────────────────────────────────────────────────────

export default function App() {
  const [mode, setMode]                       = useState<'chat' | 'thinking'>('chat');
  const [displayMessages, setDisplayMessages] = useState<DisplayMessage[]>([]);
  const [history, setHistory]                 = useState<ChatMessage[]>([]);
  const [input, setInput]                     = useState('');
  const [loading, setLoading]                 = useState(false);
  const [error, setError]                     = useState<string | null>(null);
  const [health, setHealth]                   = useState<HealthResponse | null>(null);

  // Analysis state lives here so it persists across mode switches
  const [analysisState, analysisDispatch] = useReducer(analysisReducer, analysisInitialState);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef    = useRef<HTMLTextAreaElement>(null);

  const mic = useMicRecorder({
    onTranscript: (text) => {
      setInput((prev) => (prev ? `${prev} ${text}` : text));
      setTimeout(() => {
        const el = textareaRef.current;
        if (el) { el.style.height = 'auto'; el.style.height = `${Math.min(el.scrollHeight, 140)}px`; }
      }, 0);
    },
    onError: (msg) => setError(msg),
  });

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

    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setError(null);

    const userMsg: ChatMessage  = { role: 'user', content };
    const newHistory: ChatMessage[] = [...history, userMsg];

    setDisplayMessages(prev => [...prev, { id: `user-${Date.now()}`, message: userMsg }]);
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
          structured:  response.structured,
          resultMeta:  {
            truncated:    !!response.metadata.truncated,
            has_more:     !!response.metadata.has_more,
            total_rows:   response.metadata.total_rows as number | undefined,
            limit_injected: !!response.metadata.limit_injected,
            fallback_mode: response.metadata.fallback_mode as ResultMeta['fallback_mode'],
            warning:      response.metadata.warning as string | undefined,
          },
        },
      ]);
    } catch (err: unknown) {
      const msg = err instanceof Error
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
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
    },
    [handleSend],
  );

  const handleClear = useCallback(() => {
    setDisplayMessages([]);
    setHistory([]);
    setError(null);
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
          <div style={{ marginLeft: 'auto' }}>
            <button
              className={styles.modeToggle}
              onClick={() => setMode(mode === 'chat' ? 'thinking' : 'chat')}
              disabled={loading}
              title={mode === 'chat' ? 'Switch to Thinking Mode' : 'Switch to Chat Mode'}
            >
              {mode === 'chat' ? '🧠 Thinking Mode' : '💬 Chat Mode'}
            </button>
          </div>
        </div>

        {/* ── Thinking Mode ──────────────────────────────────────────── */}
        {mode === 'thinking' ? (
          <AnalysisView state={analysisState} dispatch={analysisDispatch} />
        ) : (
        <>
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
                resultMeta={dm.resultMeta}
              />
            ))}
            {loading && <TypingIndicator />}
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
              className={`${styles.micBtn} ${mic.status === 'recording' ? styles.micBtnRecording : ''}`}
              onClick={mic.toggle}
              disabled={loading || mic.status === 'transcribing'}
              title={mic.status === 'recording' ? 'Stop recording' : 'Voice input'}
              aria-label={mic.status === 'recording' ? 'Stop recording' : 'Voice input'}
            >
              {mic.status === 'transcribing' ? (
                <span className={styles.micSpinner} />
              ) : (
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="9" y="1" width="6" height="12" rx="3" />
                  <path d="M19 10v1a7 7 0 0 1-14 0v-1" />
                  <line x1="12" y1="19" x2="12" y2="23" />
                  <line x1="8" y1="23" x2="16" y2="23" />
                </svg>
              )}
            </button>
            <button
              className={styles.sendBtn}
              onClick={() => handleSend()}
              disabled={loading || !input.trim()}
              title="Send (Enter)"
              aria-label="Send message"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13"/>
                <polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            </button>
          </div>
          <p className={styles.hint}>
            <strong>Enter</strong> to send · <strong>Shift + Enter</strong> for new line
          </p>
        </div>
        </>
        )}
      </main>
    </div>
  );
}
