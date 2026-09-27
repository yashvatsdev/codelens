"use client";

import { useEffect, useState } from "react";
import { TrendingUp, AlertCircle } from "lucide-react";
import { api } from "@/lib/api";
import type { FindingsTrendPoint } from "@/types/api";

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/**
 * Returns a label function for X-axis ticks.
 * - All points on the same calendar date  -> time labels ("05:12 AM")
 * - Points span multiple calendar dates   -> date labels ("Sep 27")
 */
function makeLabelFormatter(
  data: { date: string }[]
): (iso: string) => string {
  const uniqueDays = new Set(
    data.map((p) =>
      new Date(p.date).toLocaleDateString(undefined, {
        year: "numeric",
        month: "numeric",
        day: "numeric",
      })
    )
  );
  return uniqueDays.size === 1 ? formatTime : formatDate;
}

function Sparkline({ data }: { data: FindingsTrendPoint[] }) {
  const W = 600;
  const H = 120;
  const PAD = { top: 12, right: 16, bottom: 32, left: 36 };

  const maxFindings = Math.max(...data.map((p) => p.findings), 1);
  const xStep = (W - PAD.left - PAD.right) / Math.max(data.length - 1, 1);

  const cx = (i: number) => PAD.left + i * xStep;
  const cy = (v: number) =>
    PAD.top + (H - PAD.top - PAD.bottom) * (1 - v / maxFindings);

  const points = data.map((p, i) => ({ x: cx(i), y: cy(p.findings), p }));

  const linePath = points
    .map((pt, i) => `${i === 0 ? "M" : "L"}${pt.x.toFixed(1)},${pt.y.toFixed(1)}`)
    .join(" ");

  const areaPath =
    linePath +
    ` L${points[points.length - 1].x.toFixed(1)},${(H - PAD.bottom).toFixed(1)}` +
    ` L${points[0].x.toFixed(1)},${(H - PAD.bottom).toFixed(1)} Z`;

  // Adaptive thinning: show at most ~5 labels
  const labelEvery = Math.ceil(data.length / 5);
  // Decide date vs time labels based on whether all points share a calendar day
  const labelFormatter = makeLabelFormatter(data);

  return (
    <div className="relative w-full overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minHeight: 90 }} aria-hidden="true">
        {/* Horizontal grid lines */}
        {[0, 0.5, 1].map((frac) => {
          const y = PAD.top + (H - PAD.top - PAD.bottom) * (1 - frac);
          return (
            <line key={frac} x1={PAD.left} x2={W - PAD.right} y1={y} y2={y} stroke="#27272a" strokeWidth={1} />
          );
        })}
        {/* Y-axis labels */}
        {[0, 0.5, 1].map((frac) => {
          const y = PAD.top + (H - PAD.top - PAD.bottom) * (1 - frac);
          const val = Math.round(maxFindings * frac);
          return (
            <text key={frac} x={PAD.left - 6} y={y + 4} textAnchor="end" fontSize={9} fill="#52525b">{val}</text>
          );
        })}
        {/* Area fill */}
        <path d={areaPath} fill="rgba(34,211,238,0.07)" />
        {/* Line */}
        <path d={linePath} fill="none" stroke="#22d3ee" strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" />
        {/* Dots + X-axis labels */}
        {points.map((pt, i) => (
          <g key={i}>
            <circle cx={pt.x} cy={pt.y} r={3.5} fill="#22d3ee" />
            {(i === 0 || i === points.length - 1 || i % labelEvery === 0) && (
              <text x={pt.x} y={H - PAD.bottom + 14} textAnchor="middle" fontSize={9} fill="#52525b">
                {labelFormatter(pt.p.date)}
              </text>
            )}
          </g>
        ))}
      </svg>
    </div>
  );
}

function TrendSummary({ data }: { data: FindingsTrendPoint[] }) {
  const latest = data[data.length - 1];
  const prev = data.length > 1 ? data[data.length - 2] : null;
  const delta = prev !== null ? latest.findings - prev.findings : null;
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 pt-2 text-[11px] font-mono text-zinc-500">
      <span>
        <span className="text-zinc-300 font-semibold">{latest.findings}</span> findings in latest scan
      </span>
      {delta !== null && (
        <span className={delta > 0 ? "text-red-400" : delta < 0 ? "text-emerald-400" : ""}>
          {delta > 0 ? `+${delta}` : delta < 0 ? `${delta}` : "+-0"} vs prev
        </span>
      )}
      <span><span className="text-zinc-300">{data.length}</span> scans recorded</span>
      <span>Last: {formatDate(latest.date)} {formatTime(latest.date)}</span>
    </div>
  );
}

export function FindingsTrend() {
  const [data, setData] = useState<FindingsTrendPoint[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getFindingsTrend()
      .then((pts) => { if (!cancelled) setData(pts); })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load trend data");
      });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="cl-card flex h-full flex-col justify-between p-6">
      <div className="flex items-center justify-between border-b border-zinc-800/80 pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-zinc-100">Findings Trend</h3>
            <span className="rounded bg-zinc-800/80 px-2 py-0.5 font-mono text-[10px] text-zinc-400 font-medium">
              Time Series
            </span>
          </div>
          <p className="mt-1 text-xs text-zinc-500">
            Historical finding counts across sequential codebase scans.
          </p>
        </div>
      </div>

      <div className="flex flex-1 flex-col justify-center pt-4">
        {/* Loading */}
        {data === null && error === null && (
          <div className="my-auto flex flex-col items-center justify-center py-10 text-center">
            <div className="size-5 animate-spin rounded-full border-2 border-zinc-700 border-t-cyan-400 mb-3" />
            <p className="text-xs text-zinc-500">Loading trend data...</p>
          </div>
        )}

        {/* Error */}
        {error !== null && (
          <div className="my-auto flex flex-col items-center justify-center py-10 text-center gap-2">
            <AlertCircle className="size-6 text-red-400" />
            <p className="text-xs text-zinc-400">Could not load trend data</p>
            <p className="text-[11px] font-mono text-zinc-600">{error}</p>
          </div>
        )}

        {/* Empty */}
        {data !== null && data.length === 0 && (
          <div className="my-auto flex flex-col items-center justify-center py-10 text-center">
            <div className="relative mb-4 flex size-14 items-center justify-center rounded-2xl border border-zinc-800 bg-zinc-900 shadow-inner">
              <TrendingUp className="size-6 text-cyan-300" />
            </div>
            <h4 className="text-sm font-medium text-zinc-200">No historical trend data yet</h4>
            <p className="mt-1.5 max-w-xs text-xs text-zinc-500 leading-relaxed">
              Trend data will appear as CodeLens scans your repositories over time.
            </p>
            <div className="mt-6 inline-flex items-center gap-2 rounded-full border border-zinc-800 bg-zinc-900/50 px-3 py-1.5 text-[11px] font-mono text-zinc-400">
              <span className="size-1.5 rounded-full bg-zinc-600 animate-pulse" />
              Awaiting history
            </div>
          </div>
        )}

        {/* Single snapshot */}
        {data !== null && data.length === 1 && (
          <div className="flex flex-col gap-4">
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-4 text-center">
              <p className="text-2xl font-bold text-zinc-100 tabular-nums">{data[0].findings}</p>
              <p className="mt-1 text-xs text-zinc-500">
                findings in your first scan &middot; {formatDate(data[0].date)} {formatTime(data[0].date)}
              </p>
              <div className="mt-3 flex justify-center gap-4 text-[11px] font-mono">
                <span className="text-red-400">{data[0].errors} errors</span>
                <span className="text-yellow-400">{data[0].warnings} warnings</span>
                <span className="text-blue-400">{data[0].info} info</span>
              </div>
            </div>
            <p className="text-center text-xs text-zinc-500">Run another scan to build your historical trend.</p>
          </div>
        )}

        {/* Multi-snapshot sparkline */}
        {data !== null && data.length > 1 && (
          <div className="flex flex-col gap-2">
            <Sparkline data={data} />
            <TrendSummary data={data} />
          </div>
        )}
      </div>

      <div className="border-t border-zinc-800/80 pt-4 text-[11px] font-mono text-zinc-500 mt-4">
        Deterministic snapshots recorded on each full scan.
      </div>
    </div>
  );
}
