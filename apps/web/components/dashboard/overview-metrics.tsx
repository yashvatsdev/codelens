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
  // Aggregate real health score across all repositories
  const health = useMemo(() => calculateHealthScore(findings), [findings]);
  const colors = useMemo(() => getHealthColor(health.label), [health.label]);

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
      {/* 1. Health Score Metric */}
      <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5 shadow-sm hover:border-white/[0.12] transition">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider font-mono">
            Health Score
          </span>
          <span
            className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-medium border font-mono ${colors.badge}`}
          >
            {health.label === "Excellent" || health.label === "Good" ? (
              <ShieldCheck className="size-3" />
            ) : (
              <ShieldAlert className="size-3" />
            )}
            {health.label}
          </span>
        </div>
        <div className="mt-3 flex items-baseline gap-2">
          <span className={`text-3xl font-bold font-mono tracking-tight ${colors.text}`}>
            {health.score}
          </span>
          <span className="text-xs font-mono text-zinc-500">/ 100</span>
        </div>
        <p className="mt-2 text-xs text-zinc-400">
          {health.totalFindings === 0
            ? "Clean codebase · 0 penalties"
            : `${health.totalFindings} active findings evaluated`}
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
              {errorCount} {errorCount === 1 ? "error" : "errors"} need attention
            </span>
          ) : (
            <span className="text-zinc-400">
              {warningCount} {warningCount === 1 ? "warning" : "warnings"} · {infoCount} info
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
