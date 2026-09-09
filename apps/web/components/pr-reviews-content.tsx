"use client";

import { useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  Check,
  CircleCheck,
  Code2,
  FileCode2,
  GitBranch,
  GitPullRequest,
  Lightbulb,
  Loader2,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import type {
  AIPRKeyFinding,
  AIPRReviewResponse,
  RepositoryResponse,
} from "@/types/api";

interface PRReviewsContentProps {
  repositories: RepositoryResponse[];
}

function RiskBadge({ riskLevel }: { riskLevel: string }) {
  const norm = riskLevel.toLowerCase();

  if (norm === "critical") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-red-500/30 bg-red-500/15 px-3 py-1 text-xs font-semibold uppercase tracking-wider text-red-300 shadow-[0_0_12px_rgba(239,68,68,0.2)]">
        <span className="size-2 rounded-full bg-red-500 animate-pulse" />
        Critical Risk
      </span>
    );
  }
  if (norm === "high") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-orange-500/30 bg-orange-500/15 px-3 py-1 text-xs font-semibold uppercase tracking-wider text-orange-300">
        <span className="size-2 rounded-full bg-orange-500" />
        High Risk
      </span>
    );
  }
  if (norm === "medium") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-400/30 bg-amber-400/15 px-3 py-1 text-xs font-semibold uppercase tracking-wider text-amber-200">
        <span className="size-2 rounded-full bg-amber-400" />
        Medium Risk
      </span>
    );
  }
  // Default to low risk (positive/green)
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/15 px-3 py-1 text-xs font-semibold uppercase tracking-wider text-emerald-300">
      <span className="size-2 rounded-full bg-emerald-400" />
      Low Risk
    </span>
  );
}

function FindingSeverityBadge({ severity }: { severity: string }) {
  const norm = severity.toLowerCase();
  if (norm === "error" || norm === "critical") {
    return (
      <span className="inline-flex items-center gap-1 rounded border border-red-500/20 bg-red-500/10 px-2 py-0.5 text-[11px] font-medium text-red-300">
        <span className="size-1.5 rounded-full bg-red-400" />
        {severity}
      </span>
    );
  }
  if (norm === "warning") {
    return (
      <span className="inline-flex items-center gap-1 rounded border border-amber-400/20 bg-amber-400/10 px-2 py-0.5 text-[11px] font-medium text-amber-200">
        <span className="size-1.5 rounded-full bg-amber-300" />
        {severity}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded border border-cyan-400/20 bg-cyan-400/10 px-2 py-0.5 text-[11px] font-medium text-cyan-200">
      <span className="size-1.5 rounded-full bg-cyan-300" />
      {severity}
    </span>
  );
}

function getFindingHighlightStyles(severity: string) {
  const norm = severity.toLowerCase();
  if (norm === "error" || norm === "critical") {
    return {
      row: "bg-rose-500/15 border-l-2 border-rose-400 text-rose-100 font-medium",
      num: "text-rose-300 font-semibold",
      badge: "bg-rose-500/25 text-rose-300 border border-rose-500/40",
    };
  }
  if (norm === "warning") {
    return {
      row: "bg-amber-500/15 border-l-2 border-amber-400 text-amber-100 font-medium",
      num: "text-amber-300 font-semibold",
      badge: "bg-amber-500/25 text-amber-200 border border-amber-500/40",
    };
  }
  return {
    row: "bg-cyan-500/15 border-l-2 border-cyan-400 text-cyan-100 font-medium",
    num: "text-cyan-300 font-semibold",
    badge: "bg-cyan-500/25 text-cyan-200 border border-cyan-500/40",
  };
}

export function PRReviewsContent({ repositories }: PRReviewsContentProps) {
  const [selectedRepoId, setSelectedRepoId] = useState<number | "">(
    repositories.length > 0 ? repositories[0].id : "",
  );
  const [prNumberInput, setPrNumberInput] = useState<string>("");
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [reviewResult, setReviewResult] = useState<AIPRReviewResponse | null>(
    null,
  );
  const [activeReviewMeta, setActiveReviewMeta] = useState<{
    repoName: string;
    prNumber: number;
    repoId: number;
  } | null>(null);

  const selectedRepo = repositories.find((r) => r.id === selectedRepoId);

  const handleRunReview = async (overridePr?: number) => {
    const prNum = overridePr !== undefined ? overridePr : parseInt(prNumberInput.trim(), 10);

    if (!selectedRepoId) {
      setErrorMessage("Please select a repository to review.");
      return;
    }

    if (isNaN(prNum) || prNum <= 0) {
      setErrorMessage("Please enter a valid positive Pull Request number (e.g. 12).");
      return;
    }

    const repoObj = repositories.find((r) => r.id === selectedRepoId);
    if (!repoObj) {
      setErrorMessage("Selected repository not found.");
      return;
    }

    setIsLoading(true);
    setErrorMessage(null);

    try {
      const response = await api.aiReviewPullRequest(Number(selectedRepoId), prNum);
      setReviewResult(response);
      setActiveReviewMeta({
        repoName: repoObj.full_name,
        prNumber: prNum,
        repoId: repoObj.id,
      });
    } catch (err) {
      setErrorMessage(
        err instanceof ApiError
          ? err.message
          : "Failed to perform AI Pull Request review. Please try again.",
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-8">
      {/* Header */}
      <div>
        <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-300/70">
          Workspace / PR Reviews
        </p>
        <h2 className="mt-2 text-2xl font-semibold tracking-tight text-zinc-100 sm:text-3xl">
          Pull Request Reviews
        </h2>
        <p className="mt-2 max-w-xl text-sm leading-6 text-zinc-500">
          Run automated senior-engineer AI reviews on GitHub Pull Requests.
          Analyzes only changed files with CodeLens static analysis and Google Gemini.
        </p>
      </div>

      {/* Review Request Form */}
      <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-5 sm:p-6 shadow-[0_12px_40px_-24px_rgba(0,0,0,.8)]">
        <h3 className="text-sm font-medium text-zinc-200 mb-4 flex items-center gap-2">
          <GitPullRequest className="size-4 text-cyan-300" />
          <span>Review a Pull Request</span>
        </h3>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-12 sm:items-end">
          {/* Repository Selector */}
          <div className="sm:col-span-6">
            <label
              htmlFor="pr-repo-select"
              className="block text-xs font-medium text-zinc-400 mb-1.5"
            >
              Repository
            </label>
            <select
              id="pr-repo-select"
              value={selectedRepoId}
              onChange={(e) => {
                const val = e.target.value;
                setSelectedRepoId(val ? Number(val) : "");
                setErrorMessage(null);
              }}
              disabled={isLoading || repositories.length === 0}
              className="w-full rounded-lg border border-white/[0.1] bg-black/40 px-3 py-2 text-sm text-zinc-200 transition focus:border-cyan-300/50 focus:outline-none focus:ring-1 focus:ring-cyan-300/50 disabled:opacity-50"
            >
              {repositories.length === 0 ? (
                <option value="">No repositories connected</option>
              ) : (
                repositories.map((repo) => (
                  <option key={repo.id} value={repo.id} className="bg-[#111315]">
                    {repo.full_name}
                  </option>
                ))
              )}
            </select>
          </div>

          {/* PR Number Input */}
          <div className="sm:col-span-3">
            <label
              htmlFor="pr-number-input"
              className="block text-xs font-medium text-zinc-400 mb-1.5"
            >
              PR Number
            </label>
            <div className="relative">
              <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-xs font-mono text-zinc-500">
                #
              </span>
              <input
                id="pr-number-input"
                type="number"
                min="1"
                step="1"
                placeholder="12"
                value={prNumberInput}
                onChange={(e) => {
                  setPrNumberInput(e.target.value);
                  setErrorMessage(null);
                }}
                disabled={isLoading}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    handleRunReview();
                  }
                }}
                className="w-full rounded-lg border border-white/[0.1] bg-black/40 pl-7 pr-3 py-2 text-sm text-zinc-200 transition placeholder:text-zinc-600 focus:border-cyan-300/50 focus:outline-none focus:ring-1 focus:ring-cyan-300/50 disabled:opacity-50"
              />
            </div>
          </div>

          {/* Submit Button */}
          <div className="sm:col-span-3">
            <Button
              onClick={() => handleRunReview()}
              disabled={isLoading || repositories.length === 0 || !prNumberInput.trim()}
              className="w-full bg-cyan-300 text-black hover:bg-cyan-200 font-medium disabled:opacity-40 h-[38px]"
            >
              {isLoading ? (
                <>
                  <Loader2 className="size-4 animate-spin mr-2" />
                  Reviewing...
                </>
              ) : (
                <>
                  <Sparkles className="size-4 mr-2" />
                  Run AI Review
                </>
              )}
            </Button>
          </div>
        </div>

        {/* Validation or API Error Alert */}
        {errorMessage && (
          <div className="mt-4 rounded-lg border border-rose-500/20 bg-rose-500/10 p-3.5 text-xs text-rose-300 flex items-start justify-between gap-3 animate-in fade-in duration-150">
            <div className="flex items-start gap-2.5">
              <AlertCircle className="size-4 shrink-0 mt-0.5 text-rose-400" />
              <div className="leading-relaxed">{errorMessage}</div>
            </div>
            <button
              onClick={() => setErrorMessage(null)}
              className="text-rose-400/70 hover:text-rose-300 text-xs font-mono"
            >
              Dismiss
            </button>
          </div>
        )}
      </div>

      {/* Loading State Overlay / Card */}
      {isLoading && (
        <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-12 text-center animate-in fade-in duration-200">
          <div className="inline-flex size-12 items-center justify-center rounded-xl bg-purple-500/10 border border-purple-500/20 mb-4">
            <Loader2 className="size-6 animate-spin text-purple-400" />
          </div>
          <h4 className="text-base font-semibold text-zinc-200">
            Analyzing Pull Request #{prNumberInput}...
          </h4>
          <p className="mt-2 text-xs text-zinc-500 max-w-md mx-auto leading-relaxed">
            Fetching changed files from GitHub, executing CodeLens static analyzers,
            and consulting Google Gemini for high-level risk evaluation and recommendations.
          </p>
          <div className="mt-6 flex items-center justify-center gap-2 text-[11px] font-mono text-zinc-600">
            <span className="inline-block size-1.5 rounded-full bg-cyan-400 animate-ping" />
            <span>Processing PR version contents in memory</span>
          </div>
        </div>
      )}

      {/* Review Results */}
      {!isLoading && reviewResult && activeReviewMeta && (
        <div className="space-y-6 animate-in fade-in duration-200">
          {/* Section A: Header Banner */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 rounded-xl border border-white/[0.08] bg-white/[0.035] p-5">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-lg bg-cyan-400/10 border border-cyan-400/20 text-cyan-300">
                <GitPullRequest className="size-5" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="text-lg font-semibold text-zinc-100">
                    PR #{activeReviewMeta.prNumber}
                  </h3>
                  <span className="text-zinc-600 font-mono">·</span>
                  <span className="font-mono text-xs text-zinc-400">
                    {activeReviewMeta.repoName}
                  </span>
                </div>
                <p className="text-xs text-zinc-500 mt-0.5">
                  Static analysis & AI-evaluated review
                </p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <RiskBadge riskLevel={reviewResult.risk_level} />
              <Button
                size="sm"
                variant="outline"
                onClick={() => handleRunReview(activeReviewMeta.prNumber)}
                className="border-white/[0.1] text-zinc-300 hover:bg-white/[0.05] h-8 text-xs"
              >
                <RefreshCw className="size-3.5 mr-1.5" />
                Re-review
              </Button>
            </div>
          </div>

          {/* Section B: Risk Overview */}
          <div className="grid grid-cols-1 md:grid-cols-12 gap-5">
            {/* Risk Card */}
            <div className="md:col-span-4 rounded-xl border border-white/[0.08] bg-white/[0.025] p-5 flex flex-col justify-between">
              <div>
                <span className="text-[10px] font-mono uppercase tracking-[0.18em] text-zinc-500">
                  Risk Level
                </span>
                <div className="mt-2 flex items-center gap-2.5">
                  <RiskBadge riskLevel={reviewResult.risk_level} />
                </div>
              </div>
              <div className="mt-4 pt-4 border-t border-white/[0.06]">
                <div className="text-xs text-zinc-400 leading-relaxed">
                  {reviewResult.summary}
                </div>
              </div>
            </div>

            {/* Overall Assessment */}
            <div className="md:col-span-8 rounded-xl border border-white/[0.08] bg-white/[0.025] p-5">
              <span className="text-[10px] font-mono uppercase tracking-[0.18em] text-zinc-500 flex items-center gap-1.5">
                <Sparkles className="size-3 text-cyan-300" />
                Overall Assessment
              </span>
              <p className="mt-3 text-xs leading-relaxed text-zinc-300 whitespace-pre-wrap">
                {reviewResult.overall_assessment}
              </p>
            </div>
          </div>

          {/* Section C: Key Findings */}
          <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5 sm:p-6 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <ShieldAlert className="size-4 text-purple-400" />
                <h4 className="text-sm font-semibold text-zinc-200">
                  Key Findings
                </h4>
                <span className="rounded-full bg-white/[0.06] border border-white/[0.08] px-2 py-0.5 text-[11px] font-mono text-zinc-400">
                  {reviewResult.key_findings.length}
                </span>
              </div>
            </div>

            {reviewResult.key_findings.length === 0 ? (
              <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-8 text-center">
                <ShieldCheck className="mx-auto size-8 text-emerald-400 mb-2" />
                <p className="text-sm font-medium text-emerald-200">
                  No Key Issues Detected
                </p>
                <p className="text-xs text-zinc-400 mt-1 max-w-md mx-auto">
                  CodeLens static analyzers and the AI reviewer found no critical bugs,
                  vulnerabilities, or code smells in the changed files for this PR.
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {reviewResult.key_findings.map((finding, idx) => (
                  <div
                    key={`${finding.file_path}-${finding.line_number}-${idx}`}
                    className="rounded-lg border border-white/[0.08] bg-black/40 p-4 transition hover:border-white/[0.14]"
                  >
                    {/* Header Row: Severity, Category, File location */}
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.05] pb-3 mb-3">
                      <div className="flex items-center gap-2">
                        <FindingSeverityBadge severity={finding.severity} />
                        <span className="rounded bg-white/[0.06] px-2 py-0.5 text-[11px] font-mono text-zinc-400 uppercase tracking-wider">
                          {finding.category}
                        </span>
                      </div>
                      <div className="flex items-center gap-1.5 font-mono text-xs text-zinc-400">
                        <FileCode2 className="size-3.5 text-zinc-500" />
                        <span className="text-zinc-300">{finding.file_path}</span>
                        {finding.line_number && (
                          <span className="text-cyan-300">
                            :{finding.line_number}
                          </span>
                        )}
                      </div>
                    </div>

                    {/* Issue Description */}
                    <div className="space-y-2.5">
                      <div>
                        <div className="text-[11px] font-medium text-zinc-500 uppercase tracking-wider">
                          Issue
                        </div>
                        <p className="text-xs font-medium text-zinc-200 mt-0.5">
                          {finding.issue}
                        </p>
                      </div>

                      {/* Code Context */}
                      {finding.code_context && (
                        <div className="space-y-1.5 pt-1">
                          <div className="flex items-center justify-between text-[11px] font-medium text-zinc-500 uppercase tracking-wider">
                            <span className="flex items-center gap-1.5 text-zinc-400">
                              <Code2 className="size-3 text-cyan-300" />
                              Code Context
                            </span>
                            {finding.start_line != null && finding.end_line != null && (
                              <span className="font-mono text-[10px] text-zinc-500 lowercase">
                                lines {finding.start_line}–{finding.end_line}
                              </span>
                            )}
                          </div>
                          <div className="overflow-x-auto rounded-lg border border-white/[0.08] bg-[#0c0d0f] p-2.5 font-mono text-xs">
                            <div className="min-w-fit space-y-0.5">
                              {finding.code_context.split("\n").map((line, lineIdx) => {
                                const lineNum = (finding.start_line ?? 1) + lineIdx;
                                const isFindingLine = lineNum === finding.line_number;
                                const highlight = getFindingHighlightStyles(finding.severity);

                                return (
                                  <div
                                    key={lineIdx}
                                    className={`flex items-start gap-3 px-2 py-0.5 rounded transition ${
                                      isFindingLine
                                        ? highlight.row
                                        : "text-zinc-400 hover:bg-white/[0.02]"
                                    }`}
                                  >
                                    <span
                                      className={`w-7 shrink-0 text-right select-none font-mono text-[11px] ${
                                        isFindingLine ? highlight.num : "text-zinc-600"
                                      }`}
                                    >
                                      {lineNum}
                                    </span>
                                    <pre className="flex-1 whitespace-pre font-mono text-[12px] leading-relaxed">
                                      {line || " "}
                                    </pre>
                                    {isFindingLine && (
                                      <span
                                        className={`shrink-0 select-none rounded px-1.5 py-0.2 text-[10px] font-sans font-medium tracking-wide ${highlight.badge}`}
                                      >
                                        ← finding
                                      </span>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        </div>
                      )}

                      {/* Impact */}
                      {finding.impact && (
                        <div className="rounded-md bg-white/[0.02] border border-white/[0.04] p-2.5">
                          <div className="text-[10px] font-semibold text-amber-300/90 uppercase tracking-wider flex items-center gap-1">
                            <AlertTriangle className="size-3" />
                            Why it matters
                          </div>
                          <p className="text-xs text-zinc-300 mt-1 leading-relaxed">
                            {finding.impact}
                          </p>
                        </div>
                      )}

                      {/* Recommendation */}
                      {finding.recommendation && (
                        <div className="rounded-md bg-emerald-500/5 border border-emerald-500/20 p-2.5">
                          <div className="text-[10px] font-semibold text-emerald-300 uppercase tracking-wider flex items-center gap-1">
                            <Check className="size-3" />
                            Recommended Remediation
                          </div>
                          <p className="text-xs text-emerald-100/90 mt-1 leading-relaxed">
                            {finding.recommendation}
                          </p>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Section D: Recommendations */}
          {reviewResult.recommendations &&
            reviewResult.recommendations.length > 0 && (
              <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5 sm:p-6">
                <div className="flex items-center gap-2 mb-4">
                  <Lightbulb className="size-4 text-amber-300" />
                  <h4 className="text-sm font-semibold text-zinc-200">
                    Prioritized Recommendations
                  </h4>
                </div>

                <ul className="space-y-2.5">
                  {reviewResult.recommendations.map((rec, i) => (
                    <li
                      key={i}
                      className="flex items-start gap-3 rounded-lg border border-white/[0.05] bg-white/[0.02] p-3 text-xs text-zinc-300"
                    >
                      <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-cyan-300/10 border border-cyan-300/20 text-[10px] font-mono font-semibold text-cyan-300 mt-0.5">
                        {i + 1}
                      </span>
                      <span className="leading-relaxed">{rec}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
        </div>
      )}

      {/* Initial Empty State before any review */}
      {!isLoading && !reviewResult && (
        <div className="rounded-xl border border-dashed border-white/[0.1] bg-white/[0.015] p-10 text-center">
          <div className="mx-auto flex size-12 items-center justify-center rounded-xl bg-cyan-300/10 border border-cyan-300/20 text-cyan-300 mb-4">
            <GitPullRequest className="size-6" />
          </div>
          <h3 className="text-base font-medium text-zinc-200">
            No Pull Request Review Selected
          </h3>
          <p className="mt-2 text-xs text-zinc-500 max-w-md mx-auto leading-relaxed">
            Select one of your connected GitHub repositories, enter the PR number
            you wish to evaluate, and click <strong>Run AI Review</strong> to trigger
            in-memory static code analysis and Gemini-powered insights.
          </p>

          <div className="mt-8 grid grid-cols-1 sm:grid-cols-3 gap-4 max-w-2xl mx-auto text-left">
            <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-4">
              <span className="text-[10px] font-mono text-cyan-300">01</span>
              <h5 className="text-xs font-semibold text-zinc-300 mt-1">
                Targeted Diff
              </h5>
              <p className="text-[11px] text-zinc-500 mt-1 leading-normal">
                Analyzes only the files added or modified in the pull request.
              </p>
            </div>
            <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-4">
              <span className="text-[10px] font-mono text-cyan-300">02</span>
              <h5 className="text-xs font-semibold text-zinc-300 mt-1">
                Multi-Language
              </h5>
              <p className="text-[11px] text-zinc-500 mt-1 leading-normal">
                AST & pattern analyzers for Python, JavaScript, and TypeScript.
              </p>
            </div>
            <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-4">
              <span className="text-[10px] font-mono text-cyan-300">03</span>
              <h5 className="text-xs font-semibold text-zinc-300 mt-1">
                Senior Review
              </h5>
              <p className="text-[11px] text-zinc-500 mt-1 leading-normal">
                Grounded Gemini assessment focusing on security, bugs, and remediations.
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

