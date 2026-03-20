// src/components/ChatBubble.tsx
import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import type { ChatMessage, ToolResult } from '../types';
import styles from './ChatBubble.module.css';

interface Props {
  message: ChatMessage;
  toolResults?: ToolResult[];
}

export default function ChatBubble({ message, toolResults }: Props) {
  const [showDebug, setShowDebug] = useState(false);
  const isUser = message.role === 'user';

  return (
    <div className={`${styles.bubble} ${isUser ? styles.user : styles.assistant}`}>
      <div className={styles.header}>
        {!isUser && (
          <div className={`${styles.avatar} ${styles.botAvatar}`}>🤖</div>
        )}
        <span className={styles.label}>{isUser ? 'You' : 'Assistant'}</span>
        {isUser && (
          <div className={`${styles.avatar} ${styles.userAvatar}`}>U</div>
        )}
      </div>

      <div className={styles.content}>
        {isUser ? (
          <span style={{ whiteSpace: 'pre-wrap' }}>{message.content}</span>
        ) : (
          <ReactMarkdown>{message.content}</ReactMarkdown>
        )}
      </div>

      {!isUser && toolResults && toolResults.length > 0 && (
        <>
          <button
            className={styles.debugToggle}
            onClick={() => setShowDebug(v => !v)}
          >
            {showDebug ? '▲ Hide' : '▼ Show'} tool results ({toolResults.length})
          </button>
          {showDebug && (
            <div className={styles.debugPanel}>
              {toolResults.map((tr, i) => (
                <div key={i} style={{ marginBottom: 12 }}>
                  <strong style={{ color: 'var(--accent)' }}>
                    🔧 {tr.tool}
                  </strong>
                  <div style={{ color: 'var(--text-muted)', marginTop: 2 }}>
                    Args: {JSON.stringify(tr.args)}
                  </div>
                  <pre style={{ marginTop: 4 }}>
                    {JSON.stringify(tr.result, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
