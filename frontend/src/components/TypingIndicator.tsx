// src/components/TypingIndicator.tsx
import styles from './TypingIndicator.module.css';

export default function TypingIndicator() {
  return (
    <div className={styles.wrap}>
      <div className={styles.dot} />
      <div className={styles.dot} />
      <div className={styles.dot} />
    </div>
  );
}
