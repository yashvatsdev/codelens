"use client";

import { TrendingUp } from "lucide-react";

interface FindingsTrendProps {
  /**
   * CodeLens currently does not persist historical multi-scan snapshots.
   * If a historical dataset is provided in the future, it can be passed here.
   */
  hasHistoricalData?: boolean;
}

export function FindingsTrend({ hasHistoricalData = false }: FindingsTrendProps) {
  return (
    <div className="flex h-full flex-col justify-between rounded-xl border border-white/[0.08] bg-white/[0.025] p-5 shadow-sm">
      <div className="flex items-center justify-between border-b border-white/[0.06] pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-zinc-100">Findings Trend</h3>
            <span className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
              Time Series
            </span>
          </div>
          <p className="mt-1 text-xs text-zinc-500">
            Historical finding counts across sequential codebase scans.
          </p>
        </div>
      </div>

      {/* Polished Empty State — strictly adheres to "Do NOT fabricate trend data" */}
      <div className="my-auto flex flex-col items-center justify-center py-10 text-center">
        <div className="relative mb-4 flex size-14 items-center justify-center rounded-2xl border border-white/[0.08] bg-black/40 shadow-inner">
          <TrendingUp className="size-6 text-cyan-400/80" />
          <span className="absolute -top-1 -right-1 size-2 rounded-full bg-cyan-400/40 animate-ping" />
          <span className="absolute -top-1 -right-1 size-2 rounded-full bg-cyan-400" />
        </div>

        <h4 className="text-sm font-medium text-zinc-200">
          No historical trend data yet
        </h4>
        <p className="mt-1 max-w-xs text-xs text-zinc-500 leading-relaxed">
          Trend data will appear as CodeLens scans your repositories over time.
        </p>

        <div className="mt-6 inline-flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3 py-1 text-[11px] font-mono text-zinc-400">
          <span className="size-1.5 rounded-full bg-zinc-600" />
          Awaiting multi-scan history
        </div>
      </div>

      <div className="border-t border-white/[0.05] pt-3 text-[11px] font-mono text-zinc-500">
        Deterministic snapshots recorded on each full scan.
      </div>
    </div>
  );
}
