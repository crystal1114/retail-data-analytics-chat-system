// src/components/ChartRenderer.tsx
// Renders the appropriate chart based on viz_type from the structured response

import { useMemo } from 'react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  Title,
  Tooltip,
  Legend,
  Filler,
  type ChartOptions,
  type ChartData as ChartJSData,
} from 'chart.js';
import { Line, Bar, Doughnut } from 'react-chartjs-2';
import type { VizType, ChartData, StructuredResponse } from '../types';
import styles from './ChartRenderer.module.css';

ChartJS.register(
  CategoryScale, LinearScale, PointElement, LineElement,
  BarElement, ArcElement, Title, Tooltip, Legend, Filler,
);

// ── Palette ──────────────────────────────────────────────────────────────────

const PALETTE = [
  '#7c6cfa', // violet (primary)
  '#34d399', // emerald
  '#f59e0b', // amber
  '#f472b6', // pink
  '#38bdf8', // sky
  '#a78bfa', // purple
  '#fb923c', // orange
  '#2dd4bf', // teal
  '#f87171', // red
  '#60a5fa', // blue
];

const FILL_ALPHA = [
  'rgba(124,108,250,0.12)',
  'rgba(52,211,153,0.12)',
  'rgba(245,158,11,0.12)',
  'rgba(244,114,182,0.12)',
  'rgba(56,189,248,0.12)',
  'rgba(167,139,250,0.12)',
  'rgba(251,146,60,0.12)',
  'rgba(45,212,191,0.12)',
  'rgba(248,113,113,0.12)',
  'rgba(96,165,250,0.12)',
];

const c = (i: number) => PALETTE[i % PALETTE.length];

// ── Shared chart.js config ────────────────────────────────────────────────────

const font = { family: "'Inter', system-ui, sans-serif", size: 11 };

function baseOptions(horizontal = false): ChartOptions<any> {
  return {
    responsive: true,
    maintainAspectRatio: true,
    animation: { duration: 550, easing: 'easeOutQuart' },
    plugins: {
      legend: {
        position: 'top' as const,
        labels: {
          font,
          color: '#9ba8c0',
          padding: 18,
          boxWidth: 10,
          boxHeight: 10,
          usePointStyle: true,
          pointStyle: 'circle',
        },
      },
      tooltip: {
        backgroundColor: 'rgba(8,11,20,0.95)',
        titleColor: '#f0f4ff',
        bodyColor: '#9ba8c0',
        titleFont: { ...font, size: 12, weight: 'bold' as const },
        bodyFont: font,
        padding: 12,
        cornerRadius: 10,
        borderColor: 'rgba(124,108,250,0.25)',
        borderWidth: 1,
        callbacks: {
          label: (ctx: any) => {
            const val = ctx.parsed?.y ?? ctx.parsed;
            if (typeof val === 'number') {
              return `  ${ctx.dataset.label}: ${val.toLocaleString()}`;
            }
            return `  ${ctx.dataset.label}: ${val}`;
          },
        },
      },
    },
    scales: horizontal ? {
      x: {
        grid: { color: 'rgba(255,255,255,0.04)' },
        ticks: { font, color: '#5b6880' },
        border: { color: 'rgba(255,255,255,0.05)' },
      },
      y: {
        grid: { display: false },
        ticks: { font, color: '#5b6880' },
        border: { display: false },
      },
    } : {
      x: {
        grid: { display: false },
        ticks: { font, color: '#5b6880', maxRotation: 40 },
        border: { color: 'rgba(255,255,255,0.05)' },
      },
      y: {
        grid: { color: 'rgba(255,255,255,0.04)' },
        ticks: { font, color: '#5b6880' },
        border: { display: false },
      },
    },
  };
}

// ── KPI Card ─────────────────────────────────────────────────────────────────

function KpiCard({ data }: { data: ChartData }) {
  const kpis = data.kpis || [];
  return (
    <div className={styles.kpiGrid}>
      {kpis.map((kpi, i) => (
        <div key={i} className={styles.kpiCard}>
          {kpi.icon && <span className={styles.kpiIcon}>{kpi.icon}</span>}
          <div className={styles.kpiValue}>{kpi.value}</div>
          <div className={styles.kpiLabel}>{kpi.label}</div>
          {kpi.delta && (
            <div className={`${styles.kpiDelta} ${kpi.delta.startsWith('+') ? styles.positive : styles.negative}`}>
              {kpi.delta}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Data Table ────────────────────────────────────────────────────────────────

function DataTable({ data }: { data: ChartData }) {
  const columns = data.columns || [];
  const rows    = data.rows    || [];
  if (!columns.length || !rows.length) return null;

  // Normalise cell values: replace embedded newlines (store addresses) with ", "
  const normaliseCell = (cell: string | number) =>
    typeof cell === 'string' ? cell.replace(/\n/g, ', ') : cell;

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>{columns.map((col, i) => <th key={i}>{col}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri}>{row.map((cell, ci) => <td key={ci}>{normaliseCell(cell)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Line Chart ────────────────────────────────────────────────────────────────

function LineChart({ data }: { data: ChartData }) {
  const multiSeries = (data.datasets || []).length > 1;

  const chartData: ChartJSData<'line'> = useMemo(() => ({
    labels: data.labels || [],
    datasets: (data.datasets || []).map((ds, i) => ({
      label: ds.label,
      data:  ds.data,
      borderColor:       c(i),
      backgroundColor:   FILL_ALPHA[i % FILL_ALPHA.length],
      pointBackgroundColor: c(i),
      pointBorderColor:  'transparent',
      pointRadius:       multiSeries ? 3 : 4,
      pointHoverRadius:  6,
      borderWidth:       2,
      tension:           0.35,
      fill:              !multiSeries,
    })),
  }), [data, multiSeries]);

  const options: ChartOptions<'line'> = {
    ...baseOptions(),
    plugins: {
      ...baseOptions().plugins,
      legend: { ...baseOptions().plugins?.legend, display: multiSeries },
    },
  };

  return <Line data={chartData} options={options} />;
}

// ── Bar Chart ─────────────────────────────────────────────────────────────────

function BarChart({ data }: { data: ChartData }) {
  const multi = (data.datasets || []).length > 1;

  const chartData: ChartJSData<'bar'> = useMemo(() => ({
    labels: data.labels || [],
    datasets: (data.datasets || []).map((ds, i) => ({
      label: ds.label,
      data:  ds.data,
      backgroundColor: multi
        ? c(i)
        : (data.labels || []).map((_l, idx) => c(idx)),
      borderColor:   'transparent',
      borderRadius:  6,
      borderSkipped: false,
    })),
  }), [data, multi]);

  return <Bar data={chartData} options={baseOptions()} />;
}

// ── Horizontal Bar ────────────────────────────────────────────────────────────

function HorizontalBarChart({ data }: { data: ChartData }) {
  const chartData: ChartJSData<'bar'> = useMemo(() => ({
    labels: data.labels || [],
    datasets: (data.datasets || []).map((ds) => ({
      label: ds.label,
      data:  ds.data,
      backgroundColor: (data.labels || []).map((_l, idx) => c(idx)),
      borderColor:   'transparent',
      borderRadius:  5,
      borderSkipped: false,
    })),
  }), [data]);

  const options: ChartOptions<'bar'> = {
    ...baseOptions(true),
    indexAxis: 'y' as const,
    plugins: { ...baseOptions(true).plugins, legend: { display: false } },
  };

  return <Bar data={chartData} options={options} />;
}

// ── Doughnut Chart ────────────────────────────────────────────────────────────

function PieChart({ data }: { data: ChartData }) {
  const labels = data.labels || [];
  const ds     = (data.datasets || [])[0] || { label: '', data: [] };

  const chartData: ChartJSData<'doughnut'> = useMemo(() => ({
    labels,
    datasets: [{
      label:           ds.label,
      data:            ds.data,
      backgroundColor: labels.map((_l, i) => c(i)),
      borderColor:     '#111827',
      borderWidth:     2,
      hoverOffset:     10,
    }],
  }), [data]);

  const options: ChartOptions<'doughnut'> = {
    responsive: true,
    maintainAspectRatio: true,
    animation:  { duration: 550 },
    cutout:     '58%',
    plugins: {
      legend: {
        position: 'right' as const,
        labels: {
          font,
          color:       '#9ba8c0',
          padding:     16,
          boxWidth:    12,
          usePointStyle: true,
          pointStyle:  'circle',
        },
      },
      tooltip: {
        backgroundColor: 'rgba(8,11,20,0.95)',
        titleColor:  '#f0f4ff',
        bodyColor:   '#9ba8c0',
        titleFont:   { ...font, size: 12, weight: 'bold' as const },
        bodyFont:    font,
        padding:     12,
        cornerRadius: 10,
        borderColor: 'rgba(124,108,250,0.25)',
        borderWidth: 1,
        callbacks: {
          label: (ctx: any) => {
            const val = typeof ctx.parsed === 'number' ? ctx.parsed : 0;
            return `  ${ctx.label}: ${val.toLocaleString()}`;
          },
        },
      },
    },
  };

  return <Doughnut data={chartData} options={options} />;
}

// ── Main export ───────────────────────────────────────────────────────────────

interface Props { structured: StructuredResponse; }

export default function ChartRenderer({ structured }: Props) {
  const { viz_type, insight, chart_data } = structured;
  if (!chart_data || viz_type === 'none') return null;

  const isChartViz = ['line_chart', 'bar_chart', 'horizontal_bar_chart', 'pie_chart'].includes(viz_type);

  const renderViz = () => {
    switch (viz_type as VizType) {
      case 'line_chart':           return <LineChart           data={chart_data} />;
      case 'bar_chart':            return <BarChart            data={chart_data} />;
      case 'horizontal_bar_chart': return <HorizontalBarChart  data={chart_data} />;
      case 'pie_chart':            return <PieChart            data={chart_data} />;
      case 'kpi_card':             return <KpiCard             data={chart_data} />;
      case 'table':                return <DataTable           data={chart_data} />;
      default:                     return null;
    }
  };

  const viz = renderViz();
  if (!viz) return null;

  return (
    <div className={styles.card}>
      {insight && (
        <div className={styles.insight}>
          <span className={styles.insightDot} />
          <span>{insight}</span>
        </div>
      )}
      <div className={styles.body}>
        {isChartViz ? (
          <div className={styles.chartWrap}>{viz}</div>
        ) : viz}
      </div>
    </div>
  );
}
