// src/components/ChatBubble.tsx
import React, { useState } from 'react';
import type { ChatMessage, ToolResult, StructuredResponse, ResultMeta } from '../types';
import ChartRenderer from './ChartRenderer';
import styles from './ChatBubble.module.css';

interface Props {
  message:      ChatMessage;
  toolResults?: ToolResult[];
  structured?:  StructuredResponse | null;
  resultMeta?:  ResultMeta;
}

// ── Safety: extract clean text from content that may have leaked raw JSON ────
function cleanContent(content: string): string {
  const trimmed = content.trim();
  if (trimmed.startsWith('{')) {
    try {
      const obj = JSON.parse(trimmed);
      if (obj && typeof obj === 'object' && typeof obj.answer === 'string') {
        return obj.answer;
      }
    } catch {
      if (/"answer"\s*:/.test(trimmed)) {
        const m = trimmed.match(/"answer"\s*:\s*"((?:[^"\\]|\\.)*)"/);
        if (m) return m[1].replace(/\\n/g, '\n').replace(/\\"/g, '"');
      }
    }
  }
  return content;
}

// ── Lightweight markdown: **bold** + newlines ────────────────────────────────
function formatContent(content: string): React.ReactNode {
  const safe  = cleanContent(content);
  const parts = safe.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    return part.split('\n').map((line, j) => (
      <React.Fragment key={`${i}-${j}`}>
        {j > 0 && <br />}
        {line}
      </React.Fragment>
    ));
  });
}

// ── Truncation / pagination warning banner ───────────────────────────────────
function TruncationBanner({ meta }: { meta: ResultMeta }) {
  const { truncated, has_more, total_rows, limit_injected, fallback_mode, warning } = meta;

  if (fallback_mode === 'timeout') {
    return (
      <div className={`${styles.banner} ${styles.bannerWarn}`}>
        <span className={styles.bannerIcon}>⏱</span>
        <span>
          <strong>Query timed out.</strong>{' '}
          The query was scanning too many rows. Try adding a filter or asking for an aggregate.
        </span>
      </div>
    );
  }

  if (fallback_mode === 'broad_query') {
    return (
      <div className={`${styles.banner} ${styles.bannerInfo}`}>
        <span className={styles.bannerIcon}>📋</span>
        <span>
          <strong>Showing a 5-row sample</strong>
          {total_rows ? ` of ${total_rows.toLocaleString()} total rows` : ''}.{' '}
          Ask a more specific question to get targeted results.
        </span>
      </div>
    );
  }

  if (warning === 'max_tool_rounds_exceeded') {
    return (
      <div className={`${styles.banner} ${styles.bannerWarn}`}>
        <span className={styles.bannerIcon}>⚠️</span>
        <span>
          <strong>Response incomplete</strong> — the query required too many steps.
          Try rephrasing or simplifying your question.
        </span>
      </div>
    );
  }

  if (truncated || has_more) {
    const label = has_more && total_rows
      ? `Showing a preview of ${total_rows.toLocaleString()} total rows`
      : 'Results truncated to fit the response';
    return (
      <div className={`${styles.banner} ${styles.bannerInfo}`}>
        <span className={styles.bannerIcon}>📄</span>
        <span>
          <strong>{label}.</strong>{' '}
          Narrow your question with filters for complete results.
        </span>
      </div>
    );
  }

  return null;
}

// ── Collapsed tool-call debug panel ─────────────────────────────────────────
function DebugPanel({ toolResults }: { toolResults: ToolResult[] }) {
  const [open, setOpen] = useState(false);
  if (!toolResults.length) return null;

  const count = toolResults.length;

  return (
    <div className={styles.debugSection}>
      <button className={styles.debugToggle} onClick={() => setOpen(o => !o)}>
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z"/>
        </svg>
        {count} tool call{count > 1 ? 's' : ''}&nbsp;
        {open ? '▲' : '▼'}
      </button>

      {open && (
        <div className={styles.debugList}>
          {toolResults.map((tr, i) => (
            <div key={i} className={styles.debugEntry}>
              <span className={styles.debugBadge}>{tr.tool}</span>
              <span className={styles.debugArgs}>{JSON.stringify(tr.args)}</span>
              <span className={`${styles.debugStatus} ${tr.result.ok ? styles.ok : styles.err}`}>
                {tr.result.ok
                  ? `✓ ${tr.result.truncated ? 'rows (truncated)' : 'ok'}`
                  : `✗ ${tr.result.error ?? 'err'}`}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────
export default function ChatBubble({ message, toolResults = [], structured, resultMeta }: Props) {
  const isUser = message.role === 'user';
  const hasViz = structured && structured.viz_type !== 'none' && structured.chart_data;
  const showBanner = !isUser && resultMeta && (
    resultMeta.truncated ||
    resultMeta.has_more ||
    resultMeta.fallback_mode ||
    resultMeta.warning === 'max_tool_rounds_exceeded'
  );

  return (
    <div className={`${styles.row} ${isUser ? styles.rowUser : styles.rowBot}`}>

      {/* Avatar */}
      <div className={`${styles.avatar} ${isUser ? styles.avatarUser : styles.avatarBot}`}>
        {isUser ? '↑' : '✦'}
      </div>

      {/* Bubble */}
      <div className={`${styles.bubble} ${isUser ? styles.bubbleUser : styles.bubbleBot}`}>

        {/* Truncation / timeout warning banner — above content */}
        {showBanner && <TruncationBanner meta={resultMeta!} />}

        <div className={styles.content}>
          {formatContent(message.content)}
        </div>

        {/* Visualization */}
        {!isUser && hasViz && (
          <ChartRenderer structured={structured!} />
        )}

        {/* Debug panel */}
        {!isUser && toolResults.length > 0 && (
          <DebugPanel toolResults={toolResults} />
        )}
      </div>
    </div>
  );
}
