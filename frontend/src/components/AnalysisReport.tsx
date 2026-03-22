import type { AnalysisReport as ReportType } from '../types';
import styles from './AnalysisReport.module.css';

interface Props {
  report: ReportType;
  onNewAnalysis: () => void;
}

function formatValue(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map(formatValue).join(', ');
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export default function AnalysisReport({ report, onNewAnalysis }: Props) {
  return (
    <div className={styles.report}>
      {/* Executive summary */}
      <div className={styles.summary}>
        <div className={styles.summaryLabel}>Executive Summary</div>
        {formatValue(report.executive_summary)}
      </div>

      {/* Sections */}
      {report.sections.map((sec, i) => (
        <div key={i} className={styles.section}>
          <div className={styles.sectionTitle}>{sec.title}</div>
          <div className={styles.sectionContent}>{formatValue(sec.content)}</div>
          {sec.table && sec.table.columns && (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    {sec.table.columns.map((col, ci) => (
                      <th key={ci}>{formatValue(col)}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sec.table.rows.map((row, ri) => (
                    <tr key={ri}>
                      {row.map((cell, ci) => (
                        <td key={ci}>{formatValue(cell)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ))}

      <button className={styles.newBtn} onClick={onNewAnalysis}>
        Run another analysis
      </button>
    </div>
  );
}
