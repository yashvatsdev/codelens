"use client";

import { useMemo } from "react";
import { ArrowRight, FolderGit2 } from "lucide-react";
import { calculateHealthScore, getHealthColor } from "@/lib/health";
import type { FindingResponse, RepositoryResponse } from "@/types/api";

interface RepoDetails extends RepositoryResponse {
  findingsCount?: number;
  filesCount?: number;
}

interface RepositoryRiskProps {
  repositories: RepoDetails[];
  findings: FindingResponse[];
  onNavigateRepositories?: () => void;
}

export function RepositoryRisk({
  repositories,
  findings,
  onNavigateRepositories,
}: RepositoryRiskProps) {
  // Top 5 repos by highest risk (lowest health score first)
  const rankedRepos = useMemo(() => {
    return repositories
      .map((repo) => {
        const repoFindings = findings.filter(
          (f) => f.repository_id === repo.id,
        );
        const health = calculateHealthScore(repoFindings);
        return {
          repo,
          health,
          findingsCount: repoFindings.length,
          errorCount: health.errorCount,
        };
      })
      .sort((a, b) => a.health.score - b.health.score) // ascending: riskiest first
      .slice(0, 5);
  }, [repositories, findings]);

  return (
    <div className="flex h-full flex-col rounded-xl border border-white/[0.08] bg-white/[0.025] p-5 shadow-sm">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/[0.06] pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-zinc-100">
              Repository Risk
            </h3>
          </div>
          <p className="mt-1 text-xs text-zinc-500">
            Top repositories by risk (based on real findings).
          </p>
        </div>
        {onNavigateRepositories && (
          <button
            onClick={onNavigateRepositories}
            className="flex items-center gap-1 text-xs font-medium text-cyan-400 hover:text-cyan-300 transition"
          >
            View all
            <ArrowRight className="size-3" />
          </button>
        )}
      </div>

      {rankedRepos.length === 0 ? (
        <div className="my-auto flex flex-col items-center justify-center py-10 text-center">
          <div className="mb-3 flex size-12 items-center justify-center rounded-xl border border-white/[0.08] bg-black/40">
            <FolderGit2 className="size-6 text-zinc-500" />
          </div>
          <h4 className="text-sm font-medium text-zinc-300">
            No repositories connected
          </h4>
          <p className="mt-1 max-w-xs text-xs text-zinc-500">
            Connect a GitHub repository to see real health scores and risk
            assessment.
          </p>
        </div>
      ) : (
        <div className="mt-4 flex flex-col gap-3">
          {rankedRepos.map(({ repo, health, findingsCount, errorCount }) => {
            const colors = getHealthColor(health.label);

            let barColor = "bg-emerald-400";
            if (health.score < 50) barColor = "bg-rose-400";
            else if (health.score < 75) barColor = "bg-amber-400";
            else if (health.score < 90) barColor = "bg-cyan-400";

            // Risk badge
            let riskLabel = "Low";
            let riskStyle =
              "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
            if (health.score < 50) {
              riskLabel = "High";
              riskStyle = "bg-rose-500/15 text-rose-300 border-rose-500/30";
            } else if (health.score < 75) {
              riskLabel = "Medium";
              riskStyle = "bg-amber-500/15 text-amber-300 border-amber-500/30";
            }

            return (
              <div key={repo.id} className="space-y-1.5">
                <div className="flex items-center justify-between gap-2 text-xs">
                  {/* Left: name + branch */}
                  <div className="flex min-w-0 items-center gap-2 pr-2">
                    <span className="truncate font-mono text-zinc-200 max-w-[140px] sm:max-w-[200px]">
                      {repo.name}
                    </span>
                    <span className="shrink-0 rounded bg-white/[0.05] px-1.5 py-0.5 font-mono text-[10px] text-zinc-500">
                      {repo.default_branch}
                    </span>
                  </div>

                  {/* Right: findings count + score + risk badge */}
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="hidden font-mono text-[11px] text-zinc-500 sm:inline">
                      {findingsCount === 0
                        ? "0 findings"
                        : errorCount > 0
                          ? `${errorCount} errors`
                          : `${findingsCount} findings`}
                    </span>
                    <span
                      className={`font-mono text-sm font-bold ${colors.text}`}
                    >
                      {health.score}
                    </span>
                    <span className="font-mono text-[10px] text-zinc-500">
                      / 100
                    </span>
                    <span
                      className={`hidden rounded border px-1.5 py-0.5 font-mono text-[10px] font-medium sm:inline ${riskStyle}`}
                    >
                      {riskLabel}
                    </span>
                  </div>
                </div>

                {/* Health bar */}
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
                  <div
                    className={`h-full ${barColor} rounded-full transition-all duration-500`}
                    style={{ width: `${Math.max(4, health.score)}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="mt-auto border-t border-white/[0.05] pt-3 text-[11px] font-mono text-zinc-600">
        Formula: 100 − (Err×15) − (Warn×5) − (Info×1)
      </div>
    </div>
  );
}
