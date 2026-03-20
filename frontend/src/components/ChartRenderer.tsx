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
);

// ── Color palette ────────────────────────────────────────────────────────────

const PALETTE = [
  '#6366f1', // indigo
  '#22c55e', // green
  '#f59e0b', // amber
  '#ec4899', // pink
  '#3b82f6', // blue
  '#14b8a6', // teal
  '#f97316', // orange
  '#8b5cf6', // violet
  '#ef4444', // red
  '#06b6d4', // cyan
];

const PALETTE_ALPHA = [
  'rgba(99,102,241,0.15)',
  'rgba(34,197,94,0.15)',
  'rgba(245,158,11,0.15)',
  'rgba(236,72,153,0.15)',
  'rgba(59,130,246,0.15)',
  'rgba(20,184,166,0.15)',
  'rgba(249,115,22,0.15)',
  'rgba(139,92,246,0.15)',
  'rgba(239,68,68,0.15)',
  'rgba(6,182,212,0.15)',
];

function color(i: number) {
  return PALETTE[i % PALETTE.length];
}

// ── Common chart options ─────────────────────────────────────────────────────

const baseFont = {
  family: "'Inter', 'system-ui', sans-serif",
  size: 12,
};

function commonOptions(horizontal = false): ChartOptions<any> {
  return {
    responsive: true,
    maintainAspectRatio: true,
    animation: { duration: 600 },
    plugins: {
      legend: {
        position: 'top' as const,
        labels: { font: baseFont, padding: 16, boxWidth: 12 },
      },
      tooltip: {
        backgroundColor: 'rgba(15,23,42,0.92)',
        titleFont: { ...baseFont, weight: 'bold' as const },
        bodyFont: baseFont,
        padding: 12,
        cornerRadius: 8,
        callbacks: {
          label: (ctx: any) => {
            const val = ctx.parsed?.y ?? ctx.parsed;
            if (typeof val === 'number') {
              return ` ${ctx.dataset.label}: ${val.toLocaleString()}`;
            }
            return ` ${ctx.dataset.label}: ${val}`;
          },
        },
      },
    },
    scales: horizontal
      ? {
          x: {
            grid: { color: 'rgba(148,163,184,0.1)' },
            ticks: { font: baseFont },
          },
          y: {
            grid: { display: false },
            ticks: { font: baseFont },
          },
        }
      : {
          x: {
            grid: { display: false },
            ticks: { font: baseFont, maxRotation: 45 },
          },
          y: {
            grid: { color: 'rgba(148,163,184,0.1)' },
            ticks: { font: baseFont },
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
        <div key={i} className={styles.kpiItem}>
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

// ── Table ────────────────────────────────────────────────────────────────────

function DataTable({ data }: { data: ChartData }) {
  const columns = data.columns || [];
  const rows = data.rows || [];

  if (columns.length === 0 || rows.length === 0) return null;

  return (
    <div className={styles.tableWrapper}>
      <table className={styles.dataTable}>
        <thead>
          <tr>
            {columns.map((col, i) => (
              <th key={i}>{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri}>
              {row.map((cell, ci) => (
                <td key={ci}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Line Chart ───────────────────────────────────────────────────────────────

function LineChart({ data }: { data: ChartData }) {
  const chartData: ChartJSData<'line'> = useMemo(() => ({
    labels: data.labels || [],
    datasets: (data.datasets || []).map((ds, i) => ({
      label: ds.label,
      data: ds.data,
      borderColor: color(i),
      backgroundColor: PALETTE_ALPHA[i % PALETTE_ALPHA.length],
      pointBackgroundColor: color(i),
      pointRadius: 4,
      pointHoverRadius: 6,
      tension: 0.3,
      fill: (data.datasets || []).length === 1,
    })),
  }), [data]);

  const options: ChartOptions<'line'> = {
    ...commonOptions(),
    plugins: {
      ...commonOptions().plugins,
      legend: {
        ...commonOptions().plugins?.legend,
        display: (data.datasets || []).length > 1,
      },
    },
  };

  return <Line data={chartData} options={options} />;
}

// ── Bar Chart ────────────────────────────────────────────────────────────────

function BarChart({ data }: { data: ChartData }) {
  const multiDataset = (data.datasets || []).length > 1;

  const chartData: ChartJSData<'bar'> = useMemo(() => ({
    labels: data.labels || [],
    datasets: (data.datasets || []).map((ds, i) => ({
      label: ds.label,
      data: ds.data,
      backgroundColor: multiDataset
        ? color(i)
        : (data.labels || []).map((_label, idx) => color(idx)),
      borderColor: 'transparent',
      borderRadius: 6,
      borderSkipped: false,
    })),
  }), [data, multiDataset]);

  return <Bar data={chartData} options={commonOptions()} />;
}

// ── Horizontal Bar Chart ─────────────────────────────────────────────────────

function HorizontalBarChart({ data }: { data: ChartData }) {
  const chartData: ChartJSData<'bar'> = useMemo(() => ({
    labels: data.labels || [],
    datasets: (data.datasets || []).map((ds) => ({
      label: ds.label,
      data: ds.data,
      backgroundColor: (data.labels || []).map((_lbl, idx) => color(idx)),
      borderColor: 'transparent',
      borderRadius: 4,
      borderSkipped: false,
    })),
  }), [data]);

  const options: ChartOptions<'bar'> = {
    ...commonOptions(true),
    indexAxis: 'y' as const,
    plugins: {
      ...commonOptions(true).plugins,
      legend: { display: false },
    },
  };

  return <Bar data={chartData} options={options} />;
}

// ── Pie / Doughnut Chart ─────────────────────────────────────────────────────

function PieChart({ data }: { data: ChartData }) {
  const labels = data.labels || [];
  const ds = (data.datasets || [])[0] || { label: '', data: [] };

  const chartData: ChartJSData<'doughnut'> = useMemo(() => ({
    labels,
    datasets: [
      {
        label: ds.label,
        data: ds.data,
        backgroundColor: labels.map((_lbl, i) => color(i)),
        borderColor: '#1e293b',
        borderWidth: 2,
        hoverOffset: 8,
      },
    ],
  }), [data]);

  const options: ChartOptions<'doughnut'> = {
    responsive: true,
    maintainAspectRatio: true,
    animation: { duration: 600 },
    cutout: '55%',
    plugins: {
      legend: {
        position: 'right' as const,
        labels: { font: baseFont, padding: 16, boxWidth: 14 },
      },
      tooltip: {
        backgroundColor: 'rgba(15,23,42,0.92)',
        titleFont: { ...baseFont, weight: 'bold' as const },
        bodyFont: baseFont,
        padding: 12,
        cornerRadius: 8,
        callbacks: {
          label: (ctx: any) => {
            const val = typeof ctx.parsed === 'number' ? ctx.parsed : 0;
            return ` ${ctx.label}: ${val.toLocaleString()}`;
          },
        },
      },
    },
  };

  return <Doughnut data={chartData} options={options} />;
}

// ── Main ChartRenderer ───────────────────────────────────────────────────────

interface Props {
  structured: StructuredResponse;
}

export default function ChartRenderer({ structured }: Props) {
  const { viz_type, insight, chart_data } = structured;

  if (!chart_data || viz_type === 'none') {
    return null;
  }

  const renderChart = () => {
    switch (viz_type as VizType) {
      case 'line_chart':
        return <LineChart data={chart_data} />;
      case 'bar_chart':
        return <BarChart data={chart_data} />;
      case 'horizontal_bar_chart':
        return <HorizontalBarChart data={chart_data} />;
      case 'pie_chart':
        return <PieChart data={chart_data} />;
      case 'kpi_card':
        return <KpiCard data={chart_data} />;
      case 'table':
        return <DataTable data={chart_data} />;
      default:
        return null;
    }
  };

  const chart = renderChart();
  if (!chart) return null;

  // Determine if chart should be constrained in height
  const isChart = ['line_chart', 'bar_chart', 'horizontal_bar_chart', 'pie_chart'].includes(viz_type);

  return (
    <div className={styles.container}>
      {insight && (
        <div className={styles.insight}>
          <span className={styles.insightIcon}>💡</span>
          <span>{insight}</span>
        </div>
      )}
      <div className={`${styles.chartArea} ${isChart ? styles.chartConstrained : ''}`}>
        {chart}
      </div>
    </div>
  );
}
