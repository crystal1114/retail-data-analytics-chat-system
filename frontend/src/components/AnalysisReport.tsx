import type { AnalysisReport as ReportType } from '../types';
import styles from './AnalysisReport.module.css';

interface Props {
  report: ReportType;
  onNewAnalysis: () => void;
}

export default function AnalysisReport({ report, onNewAnalysis }: Props) {
  return (
    <div className={styles.report}>
      {/* Executive summary */}
      <div className={styles.summary}>
        <div className={styles.summaryLabel}>Executive Summary</div>
        {report.executive_summary}
      </div>

      {/* Sections */}
      {report.sections.map((sec, i) => (
        <div key={i} className={styles.section}>
          <div className={styles.sectionTitle}>{sec.title}</div>
          <div className={styles.sectionContent}>{sec.content}</div>
          {sec.table && sec.table.columns && (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    {sec.table.columns.map((col, ci) => (
                      <th key={ci}>{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sec.table.rows.map((row, ri) => (
                    <tr key={ri}>
                      {row.map((cell, ci) => (
                        <td key={ci}>{cell}</td>
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
