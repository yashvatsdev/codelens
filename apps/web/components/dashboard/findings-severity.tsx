"use client";

import { useMemo } from "react";
import { PieChart } from "lucide-react";
import { DonutChart } from "@/components/ui/donut-chart";
import type { FindingResponse } from "@/types/api";

interface FindingsSeverityProps {
  findings: FindingResponse[];
  errorCount: number;
  warningCount: number;
  infoCount: number;
}

export function FindingsSeverity({
  findings,
  errorCount,
  warningCount,
  infoCount,
}: FindingsSeverityProps) {
  const total = findings.length;

  const segments = useMemo(
    () => [
      { label: "Errors", value: errorCount, color: "#f43f5e" },
      { label: "Warnings", value: warningCount, color: "#fbbf24" },
      { label: "Info", value: infoCount, color: "#22d3ee" },
    ],
    [errorCount, warningCount, infoCount],
  );

  const legend = useMemo(
    () =>
      segments.map((s) => ({
        ...s,
        pct: total > 0 ? Math.round((s.value / total) * 100) : 0,
      })),
    [segments, total],
  );

  return (
    <div className="flex h-full flex-col rounded-xl border border-white/[0.08] bg-white/[0.025] p-5 shadow-sm">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/[0.06] pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-zinc-100">
              Findings by Severity
            </h3>
          </div>
          <p className="mt-1 text-xs text-zinc-500">
            Distribution of all findings across severity levels.
          </p>
        </div>
      </div>

      {total === 0 ? (
        /* Empty state */
        <div className="my-auto flex flex-col items-center justify-center py-10 text-center">
          <div className="mb-3 flex size-12 items-center justify-center rounded-xl border border-white/[0.08] bg-black/40">
            <PieChart className="size-5 text-zinc-500" />
          </div>
          <h4 className="text-sm font-medium text-zinc-300">No findings yet</h4>
          <p className="mt-1 max-w-xs text-xs text-zinc-500 leading-relaxed">
            Run static analysis on a repository to see severity distribution.
          </p>
        </div>
      ) : (
        /* Chart + Legend */
        <div className="mt-5 flex flex-col items-center gap-6 sm:flex-row sm:items-center sm:justify-center">
          <DonutChart
            segments={segments}
            totalValue={total}
            totalLabel="Total Findings"
            size={152}
            strokeWidth={22}
          />

          {/* Legend */}
          <div className="flex flex-col gap-3 min-w-[160px]">
            {legend.map((item) => (
              <div
                key={item.label}
                className="flex items-center justify-between gap-4"
              >
                <div className="flex items-center gap-2">
                  <span
                    className="size-2.5 shrink-0 rounded-full"
                    style={{ backgroundColor: item.color }}
                  />
                  <span className="text-xs text-zinc-300">{item.label}</span>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="font-mono text-sm font-semibold text-zinc-100">
                    {item.value}
                  </span>
                  <span className="font-mono text-xs text-zinc-500 w-8 text-right">
                    {item.pct}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
