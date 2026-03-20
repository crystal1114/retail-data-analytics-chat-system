// src/components/TypingIndicator.tsx
import styles from './TypingIndicator.module.css';

export default function TypingIndicator() {
  return (
    <div style={{ display: 'flex', alignSelf: 'flex-start' }}>
      <div className={styles.wrapper}>
        <div className={styles.dot} />
        <div className={styles.dot} />
        <div className={styles.dot} />
      </div>
    </div>
  );
}
