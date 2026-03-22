import { useCallback, useReducer, useRef, useState } from 'react';
import { streamAnalysis } from '../api';
import type {
  AnalysisPhase,
  AnalysisReport as ReportType,
  AnalysisSSEEvent,
  AnalysisStepInfo,
} from '../types';
import AnalysisReport from './AnalysisReport';
import styles from './AnalysisView.module.css';

// ── State management via reducer ────────────────────────────────────────────

interface AnalysisState {
  phase: AnalysisPhase;
  steps: AnalysisStepInfo[];
  currentStep: number;
  totalSteps: number;
  report: ReportType | null;
  error: string | null;
}

const initialState: AnalysisState = {
  phase: 'idle',
  steps: [],
  currentStep: 0,
  totalSteps: 0,
  report: null,
  error: null,
};

type Action =
  | { type: 'RESET' }
  | { type: 'SET_PHASE'; phase: AnalysisPhase }
  | { type: 'SET_PLAN'; steps: Array<{ step_id: string; title: string; type: string }> }
  | { type: 'STEP_START'; step_id: string; current: number; total: number }
  | { type: 'STEP_DONE'; step_id: string; status: 'ok' | 'failed'; summary?: string }
  | { type: 'SET_REPORT'; report: ReportType }
  | { type: 'SET_ERROR'; message: string };

function reducer(state: AnalysisState, action: Action): AnalysisState {
  switch (action.type) {
    case 'RESET':
      return { ...initialState };

    case 'SET_PHASE':
      return { ...state, phase: action.phase };

    case 'SET_PLAN':
      return {
        ...state,
        steps: action.steps.map((s) => ({
          step_id: s.step_id,
          title: s.title,
          type: s.type as 'sql' | 'python',
          status: 'pending',
        })),
        totalSteps: action.steps.length,
      };

    case 'STEP_START':
      return {
        ...state,
        currentStep: action.current,
        totalSteps: action.total,
        steps: state.steps.map((s) =>
          s.step_id === action.step_id ? { ...s, status: 'running' } : s,
        ),
      };

    case 'STEP_DONE':
      return {
        ...state,
        steps: state.steps.map((s) =>
          s.step_id === action.step_id
            ? { ...s, status: action.status === 'ok' ? 'done' : 'failed', summary: action.summary }
            : s,
        ),
      };

    case 'SET_REPORT':
      return { ...state, phase: 'done', report: action.report };

    case 'SET_ERROR':
      return { ...state, phase: 'error', error: action.message };

    default:
      return state;
  }
}

// ── Component ───────────────────────────────────────────────────────────────

export default function AnalysisView() {
  const [prompt, setPrompt] = useState('');
  const [state, dispatch] = useReducer(reducer, initialState);
  const controllerRef = useRef<AbortController | null>(null);

  const handleEvent = useCallback((event: AnalysisSSEEvent) => {
    switch (event.type) {
      case 'status':
        dispatch({ type: 'SET_PHASE', phase: event.phase });
        break;
      case 'plan':
        dispatch({ type: 'SET_PLAN', steps: event.steps });
        break;
      case 'step_start':
        dispatch({
          type: 'STEP_START',
          step_id: event.step_id,
          current: event.current,
          total: event.total,
        });
        break;
      case 'step_done':
        dispatch({
          type: 'STEP_DONE',
          step_id: event.step_id,
          status: event.status,
          summary: event.summary,
        });
        break;
      case 'report':
        dispatch({
          type: 'SET_REPORT',
          report: { executive_summary: event.executive_summary, sections: event.sections },
        });
        break;
      case 'done':
        dispatch({ type: 'SET_PHASE', phase: 'done' });
        break;
      case 'error':
        dispatch({ type: 'SET_ERROR', message: event.message });
        break;
    }
  }, []);

  const handleSubmit = useCallback(() => {
    if (!prompt.trim()) return;
    dispatch({ type: 'RESET' });
    dispatch({ type: 'SET_PHASE', phase: 'planning' });
    controllerRef.current = streamAnalysis(prompt.trim(), handleEvent);
  }, [prompt, handleEvent]);

  const handleCancel = useCallback(() => {
    controllerRef.current?.abort();
    dispatch({ type: 'SET_ERROR', message: 'Analysis cancelled.' });
  }, []);

  const handleNewAnalysis = useCallback(() => {
    controllerRef.current?.abort();
    dispatch({ type: 'RESET' });
    setPrompt('');
  }, []);

  const isRunning = ['planning', 'executing', 'reporting'].includes(state.phase);
  const phaseLabels: Record<string, string> = {
    planning: 'Planning analysis steps…',
    executing: `Executing step ${state.currentStep} of ${state.totalSteps}…`,
    reporting: 'Generating report…',
  };

  // ── Idle: input form ──────────────────────────────────────────────────────
  if (state.phase === 'idle') {
    return (
      <div className={styles.container}>
        <div className={styles.inputForm}>
          <h2>Thinking Mode</h2>
          <p>
            Ask for a comprehensive analysis — the system will plan multiple steps,
            run SQL queries and pandas analysis, then assemble a structured report.
          </p>
          <textarea
            className={styles.promptInput}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="e.g. Analyze overall business performance, customer segments, product trends, and seasonal patterns…"
            rows={3}
          />
          <button
            className={styles.runBtn}
            onClick={handleSubmit}
            disabled={!prompt.trim()}
          >
            Run Analysis
          </button>
        </div>
      </div>
    );
  }

  // ── Done: show report ─────────────────────────────────────────────────────
  if (state.phase === 'done' && state.report) {
    return (
      <div className={styles.container}>
        <AnalysisReport report={state.report} onNewAnalysis={handleNewAnalysis} />
      </div>
    );
  }

  // ── Error ─────────────────────────────────────────────────────────────────
  if (state.phase === 'error') {
    return (
      <div className={styles.container}>
        <div className={styles.errorPanel}>
          {state.error}
          <br />
          <button className={styles.retryBtn} onClick={handleNewAnalysis}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  // ── Running: progress panel ───────────────────────────────────────────────
  return (
    <div className={styles.container}>
      <div className={styles.progressPanel}>
        <div className={styles.phaseLabel}>
          <div className={styles.spinner} />
          {phaseLabels[state.phase] || 'Working…'}
        </div>

        {state.steps.length > 0 && (
          <ul className={styles.stepList}>
            {state.steps.map((step) => (
              <li
                key={step.step_id}
                className={styles.stepItem}
                data-status={step.status}
              >
                <span className={styles.stepIcon}>
                  {step.status === 'done' && '✓'}
                  {step.status === 'failed' && '✗'}
                  {step.status === 'running' && '⟳'}
                  {step.status === 'pending' && '○'}
                </span>
                <span className={styles.stepTitle}>{step.title}</span>
                {step.summary && (
                  <span className={styles.stepSummary} title={step.summary}>
                    {step.summary}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}

        {isRunning && (
          <button className={styles.cancelBtn} onClick={handleCancel}>
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}
