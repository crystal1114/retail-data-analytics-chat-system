// src/components/ChatBubble.tsx
import React, { useState } from 'react';
import type { ChatMessage, ToolResult, StructuredResponse } from '../types';
import ChartRenderer from './ChartRenderer';
import styles from './ChatBubble.module.css';

interface Props {
  message:      ChatMessage;
  toolResults?: ToolResult[];
  structured?:  StructuredResponse | null;
  /** true while tokens are still being streamed in */
  streaming?:   boolean;
  /** tool names currently being called */
  activeTools?: string[];
}

// ── Lightweight markdown: **bold** + newlines ────────────────────────────────
function formatContent(content: string): React.ReactNode {
  const parts = content.split(/(\*\*[^*]+\*\*)/g);
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

// ── Blinking cursor shown while streaming ────────────────────────────────────
function StreamingCursor() {
  return <span className={styles.streamingCursor} aria-hidden="true">▋</span>;
}

// ── Tool activity pill (shown while a tool is running) ───────────────────────
function ToolActivity({ tools }: { tools: string[] }) {
  if (!tools.length) return null;
  return (
    <div className={styles.toolActivity}>
      <span className={styles.toolActivitySpinner} />
      <span className={styles.toolActivityText}>
        {tools.length === 1
          ? `Fetching ${tools[0].replace(/_/g, ' ')}…`
          : `Running ${tools.length} tool calls…`}
      </span>
    </div>
  );
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
                {tr.result.ok ? '✓ ok' : '✗ err'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────
export default function ChatBubble({
  message,
  toolResults = [],
  structured,
  streaming = false,
  activeTools = [],
}: Props) {
  const isUser = message.role === 'user';
  const hasViz = !streaming && structured && structured.viz_type !== 'none' && structured.chart_data;
  const isEmpty = !message.content && streaming;

  return (
    <div className={`${styles.row} ${isUser ? styles.rowUser : styles.rowBot}`}>

      {/* Avatar */}
      <div className={`${styles.avatar} ${isUser ? styles.avatarUser : styles.avatarBot}`}>
        {isUser ? '↑' : '✦'}
      </div>

      {/* Bubble */}
      <div className={`${styles.bubble} ${isUser ? styles.bubbleUser : styles.bubbleBot} ${streaming ? styles.bubbleStreaming : ''}`}>

        {/* Content – show skeleton dots if we haven't received any tokens yet */}
        {isEmpty ? (
          <div className={styles.thinkingDots}>
            <span /><span /><span />
          </div>
        ) : (
          <div className={styles.content}>
            {formatContent(message.content)}
            {streaming && <StreamingCursor />}
          </div>
        )}

        {/* Active tool calls */}
        {!isUser && (streaming || activeTools.length > 0) && (
          <ToolActivity tools={activeTools} />
        )}

        {/* Visualization — only rendered after streaming completes */}
        {!isUser && hasViz && (
          <ChartRenderer structured={structured!} />
        )}

        {/* Debug panel */}
        {!isUser && !streaming && toolResults.length > 0 && (
          <DebugPanel toolResults={toolResults} />
        )}
      </div>
    </div>
  );
}
