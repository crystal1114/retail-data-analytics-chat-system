// src/components/TypingIndicator.tsx
import styles from './TypingIndicator.module.css';

export default function TypingIndicator() {
  return (
    <div className={styles.wrap}>
      <span className={styles.label}>Thinking</span>
      <div className={styles.dots}>
        <div className={styles.dot} />
        <div className={styles.dot} />
        <div className={styles.dot} />
      </div>
    </div>
  );
}
