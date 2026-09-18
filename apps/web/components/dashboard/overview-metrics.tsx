"use client";

import { useMemo } from "react";
import {
  AlertCircle,
  FileCode2,
  FolderGit2,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { calculateHealthScore, getHealthColor } from "@/lib/health";
import type { FindingResponse, RepositoryResponse } from "@/types/api";

interface RepoDetails extends RepositoryResponse {
  findingsCount?: number;
  filesCount?: number;
}

interface OverviewMetricsProps {
  repositories: RepoDetails[];
  findings: FindingResponse[];
  errorCount: number;
  warningCount: number;
  infoCount: number;
}

export function OverviewMetrics({
  repositories,
  findings,
  errorCount,
  warningCount,
  infoCount,
}: OverviewMetricsProps) {
  // Only repos that have been ingested/analyzed (filesCount > 0)
  const analyzedRepos = useMemo(
    () => repositories.filter((r) => (r.filesCount || 0) > 0),
    [repositories],
  );

  // Workspace health = average of each analyzed repo's individual score.
  // Returns null when no repos have been analyzed yet.
  const workspaceHealth = useMemo(() => {
    if (analyzedRepos.length === 0) return null;
    let sum = 0;
    for (const r of analyzedRepos) {
      const repoFindings = findings.filter((f) => f.repository_id === r.id);
      sum += calculateHealthScore(repoFindings).score;
    }
    const avgScore = Math.round(sum / analyzedRepos.length);
    let label: "Excellent" | "Good" | "Fair" | "Poor" = "Excellent";
    if (avgScore < 50) label = "Poor";
    else if (avgScore < 75) label = "Fair";
    else if (avgScore < 90) label = "Good";
    return { score: avgScore, label };
  }, [analyzedRepos, findings]);

  const colors = useMemo(
    () => (workspaceHealth ? getHealthColor(workspaceHealth.label) : null),
    [workspaceHealth],
  );

  // Aggregate total stored files across all repositories
  const totalFiles = useMemo(
    () => repositories.reduce((acc, r) => acc + (r.filesCount || 0), 0),
    [repositories],
  );

  const reposWithFiles = useMemo(
    () => repositories.filter((r) => (r.filesCount || 0) > 0).length,
    [repositories],
  );

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {/* 1. Workspace Health Metric */}
      <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5 shadow-sm hover:border-white/[0.12] transition">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider font-mono">
            Workspace Health
          </span>
          <span
            className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-medium border font-mono ${
              colors
                ? colors.badge
                : "bg-zinc-500/15 text-zinc-400 border-zinc-500/30"
            }`}
          >
            {workspaceHealth ? (
              workspaceHealth.label === "Excellent" ||
              workspaceHealth.label === "Good" ? (
                <ShieldCheck className="size-3" />
              ) : (
                <ShieldAlert className="size-3" />
              )
            ) : (
              <ShieldAlert className="size-3" />
            )}
            {workspaceHealth ? workspaceHealth.label : "N/A"}
          </span>
        </div>
        <div className="mt-3 flex items-baseline gap-2">
          <span
            className={`text-3xl font-bold font-mono tracking-tight ${
              colors ? colors.text : "text-zinc-500"
            }`}
          >
            {workspaceHealth ? workspaceHealth.score : "–"}
          </span>
          <span className="text-xs font-mono text-zinc-500">
            {workspaceHealth ? "/ 100" : ""}
          </span>
        </div>
        <p className="mt-2 text-xs text-zinc-400">
          {workspaceHealth
            ? `${analyzedRepos.length} of ${repositories.length} ${
                repositories.length === 1 ? "repository" : "repositories"
              } analyzed`
            : "No repositories analyzed yet"}
        </p>
      </div>

      {/* 2. Total Findings Metric */}
      <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5 shadow-sm hover:border-white/[0.12] transition">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider font-mono">
            Total Findings
          </span>
          <ShieldCheck className="size-4 text-cyan-400" />
        </div>
        <div className="mt-3 flex items-baseline gap-2">
          <span className="text-3xl font-bold font-mono tracking-tight text-zinc-100">
            {findings.length}
          </span>
        </div>
        <div className="mt-2 flex items-center gap-1.5 text-xs">
          {errorCount > 0 ? (
            <span className="inline-flex items-center gap-1 text-red-400 font-medium">
              <AlertCircle className="size-3" />
              {errorCount} {errorCount === 1 ? "error" : "errors"} need
              attention
            </span>
          ) : (
            <span className="text-zinc-400">
              {warningCount} {warningCount === 1 ? "warning" : "warnings"} ·{" "}
              {infoCount} info
            </span>
          )}
        </div>
      </div>

      {/* 3. Repositories Metric */}
      <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5 shadow-sm hover:border-white/[0.12] transition">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider font-mono">
            Repositories
          </span>
          <FolderGit2 className="size-4 text-cyan-400" />
        </div>
        <div className="mt-3 flex items-baseline gap-2">
          <span className="text-3xl font-bold font-mono tracking-tight text-zinc-100">
            {repositories.length}
          </span>
          <span className="text-xs font-mono text-zinc-500">connected</span>
        </div>
        <p className="mt-2 text-xs text-zinc-400">
          {repositories.filter((r) => (r.findingsCount || 0) > 0).length} of{" "}
          {repositories.length} scanned
        </p>
      </div>

      {/* 4. Real Codebase Metric: Monitored Source Files */}
      <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5 shadow-sm hover:border-white/[0.12] transition">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider font-mono">
            Source Files
          </span>
          <FileCode2 className="size-4 text-cyan-400" />
        </div>
        <div className="mt-3 flex items-baseline gap-2">
          <span className="text-3xl font-bold font-mono tracking-tight text-zinc-100">
            {totalFiles}
          </span>
          <span className="text-xs font-mono text-zinc-500">files</span>
        </div>
        <p className="mt-2 text-xs text-zinc-400">
          {reposWithFiles > 0
            ? `Stored across ${reposWithFiles} ${reposWithFiles === 1 ? "repository" : "repositories"}`
            : "No source files ingested yet"}
        </p>
      </div>
    </div>
  );
}
