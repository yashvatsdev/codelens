"use client";

import { useMemo } from "react";
import { BarChart2 } from "lucide-react";
import { DonutChart } from "@/components/ui/donut-chart";
import { calculateHealthScore } from "@/lib/health";
import type { FindingResponse, RepositoryResponse } from "@/types/api";

interface RepoDetails extends RepositoryResponse {
  findingsCount?: number;
  filesCount?: number;
}

interface RepositoryHealthDistributionProps {
  repositories: RepoDetails[];
  findings: FindingResponse[];
}

const CATEGORIES = [
  { label: "Excellent", range: "90–100", color: "#34d399", min: 90, max: 100 },
  { label: "Good", range: "75–89", color: "#22d3ee", min: 75, max: 89 },
  { label: "Fair", range: "50–74", color: "#fbbf24", min: 50, max: 74 },
  { label: "Poor", range: "0–49", color: "#f43f5e", min: 0, max: 49 },
] as const;

export function RepositoryHealthDistribution({
  repositories,
  findings,
}: RepositoryHealthDistributionProps) {
  const total = repositories.length;

  const distribution = useMemo(() => {
    const counts = { Excellent: 0, Good: 0, Fair: 0, Poor: 0 };

    for (const repo of repositories) {
      const repoFindings = findings.filter((f) => f.repository_id === repo.id);
      const health = calculateHealthScore(repoFindings);
      counts[health.label]++;
    }

    return CATEGORIES.map((cat) => ({
      ...cat,
      count: counts[cat.label],
      pct: total > 0 ? Math.round((counts[cat.label] / total) * 100) : 0,
    }));
  }, [repositories, findings]);

  const segments = useMemo(
    () =>
      distribution.map((d) => ({
        label: d.label,
        value: d.count,
        color: d.color,
      })),
    [distribution],
  );

  return (
    <div className="flex h-full flex-col rounded-xl border border-white/[0.08] bg-white/[0.025] p-5 shadow-sm">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/[0.06] pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-zinc-100">
              Repository Health Distribution
            </h3>
          </div>
          <p className="mt-1 text-xs text-zinc-500">
            Health score distribution across all repositories.
          </p>
        </div>
      </div>

      {total === 0 ? (
        /* Empty state */
        <div className="my-auto flex flex-col items-center justify-center py-10 text-center">
          <div className="mb-3 flex size-12 items-center justify-center rounded-xl border border-white/[0.08] bg-black/40">
            <BarChart2 className="size-5 text-zinc-500" />
          </div>
          <h4 className="text-sm font-medium text-zinc-300">
            No repositories yet
          </h4>
          <p className="mt-1 max-w-xs text-xs text-zinc-500 leading-relaxed">
            Connect a repository to see its health score distribution.
          </p>
        </div>
      ) : (
        /* Chart + Legend */
        <div className="mt-5 flex flex-col items-center gap-6 sm:flex-row sm:items-center sm:justify-center">
          <DonutChart
            segments={segments}
            totalValue={total}
            totalLabel="Repositories"
            size={152}
            strokeWidth={22}
          />

          {/* Legend */}
          <div className="flex flex-col gap-3 min-w-[180px]">
            {distribution.map((item) => (
              <div
                key={item.label}
                className="flex items-center justify-between gap-4"
              >
                <div className="flex items-center gap-2">
                  <span
                    className="size-2.5 shrink-0 rounded-full"
                    style={{ backgroundColor: item.color }}
                  />
                  <div>
                    <span className="text-xs text-zinc-300">{item.label}</span>
                    <span className="ml-1.5 font-mono text-[10px] text-zinc-600">
                      ({item.range})
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="font-mono text-sm font-semibold text-zinc-100">
                    {item.count}
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
