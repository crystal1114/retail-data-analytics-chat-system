// src/components/ChatBubble.tsx
import React, { useState } from 'react';
import type { ChatMessage, ToolResult, StructuredResponse } from '../types';
import ChartRenderer from './ChartRenderer';
import styles from './ChatBubble.module.css';

interface Props {
  message: ChatMessage;
  toolResults?: ToolResult[];
  structured?: StructuredResponse | null;
}

function formatContent(content: string): React.ReactNode {
  // Convert **bold** markdown
  const parts = content.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    // Handle newlines
    const lines = part.split('\n');
    return lines.map((line, j) => (
      <React.Fragment key={`${i}-${j}`}>
        {j > 0 && <br />}
        {line}
      </React.Fragment>
    ));
  });
}

function DebugPanel({ toolResults }: { toolResults: ToolResult[] }) {
  const [open, setOpen] = useState(false);
  if (!toolResults.length) return null;

  return (
    <div className={styles.debug}>
      <button
        className={styles.debugToggle}
        onClick={() => setOpen(o => !o)}
      >
        🔧 {toolResults.length} tool call{toolResults.length > 1 ? 's' : ''} {open ? '▲' : '▼'}
      </button>
      {open && (
        <div className={styles.debugContent}>
          {toolResults.map((tr, i) => (
            <div key={i} className={styles.debugItem}>
              <div className={styles.debugTool}>
                <span className={styles.debugBadge}>{tr.tool}</span>
                <span className={styles.debugArgs}>
                  {JSON.stringify(tr.args)}
                </span>
                <span className={tr.result.ok ? styles.ok : styles.err}>
                  {tr.result.ok ? '✓' : '✗'}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ChatBubble({ message, toolResults = [], structured }: Props) {
  const isUser = message.role === 'user';
  const hasViz = structured && structured.viz_type !== 'none' && structured.chart_data;

  return (
    <div className={`${styles.wrapper} ${isUser ? styles.user : styles.bot}`}>
      <div className={styles.avatar}>
        {isUser ? '👤' : '🤖'}
      </div>
      <div className={`${styles.bubble} ${isUser ? styles.userBubble : styles.botBubble}`}>
        <div className={styles.content}>
          {formatContent(message.content)}
        </div>

        {/* Visualization (chart, kpi, table) */}
        {!isUser && hasViz && (
          <ChartRenderer structured={structured!} />
        )}

        {/* Debug tool calls */}
        {!isUser && toolResults.length > 0 && (
          <DebugPanel toolResults={toolResults} />
        )}
      </div>
    </div>
  );
}
