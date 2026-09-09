"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  BarChart3,
  Bell,
  ChevronDown,
  ChevronRight,
  CircleCheck,
  Code2,
  FileCode2,
  Filter,
  FolderGit2,
  GitBranch,
  Info,
  LayoutDashboard,
  Loader2,
  Menu,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import type {
  AnalysisSummaryResponse,
  FindingResponse,
  HealthResponse,
  IngestionResponse,
  RepositoryResponse,
  SeverityType,
  SourceFileResponse,
} from "@/types/api";

type Section =
  | "Dashboard"
  | "Repositories"
  | "Findings"
  | "Analysis"
  | "Settings";

interface RepoDetails extends RepositoryResponse {
  findingsCount?: number;
  filesCount?: number;
  lastAnalysis?: AnalysisSummaryResponse | null;
}

function SeverityBadge({ severity }: { severity: SeverityType | string }) {
  const norm = String(severity).toLowerCase();
  if (norm === "error") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md border border-red-400/20 bg-red-400/10 px-2 py-1 text-[11px] font-medium text-red-300">
        <span className="size-1.5 rounded-full bg-red-400" />
        error
      </span>
    );
  }
  if (norm === "warning") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md border border-amber-300/20 bg-amber-300/10 px-2 py-1 text-[11px] font-medium text-amber-200">
        <span className="size-1.5 rounded-full bg-amber-300" />
        warning
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-cyan-300/20 bg-cyan-300/10 px-2 py-1 text-[11px] font-medium text-cyan-200">
      <span className="size-1.5 rounded-full bg-cyan-300" />
      info
    </span>
  );
}

function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = "default",
}: {
  label: string;
  value: string | number;
  hint: string;
  icon: typeof Activity;
  tone?: "default" | "danger" | "warn" | "success";
}) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.035] p-4 shadow-[0_12px_40px_-24px_rgba(0,0,0,.8)]">
      <div className="flex items-start justify-between">
        <div className="text-[12px] text-zinc-500">{label}</div>
        <Icon
          className={`size-4 ${
            tone === "danger"
              ? "text-red-300"
              : tone === "warn"
                ? "text-amber-200"
                : tone === "success"
                  ? "text-emerald-300"
                  : "text-zinc-500"
          }`}
        />
      </div>
      <div className="mt-3 text-2xl font-semibold tracking-tight text-zinc-100">
        {value}
      </div>
      <div className="mt-1 text-[11px] text-zinc-600">{hint}</div>
    </div>
  );
}

const navItems = [
  { label: "Dashboard", icon: LayoutDashboard },
  { label: "Repositories", icon: GitBranch },
  { label: "Findings", icon: ShieldCheck },
  { label: "Analysis", icon: BarChart3 },
];

export function CodeLensDashboard() {
  const [active, setActive] = useState<Section>("Dashboard");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [severityFilter, setSeverityFilter] = useState<string>("all");
  const [showAdd, setShowAdd] = useState(false);
  const [repoUrl, setRepoUrl] = useState("");
  const [isAddingRepo, setIsAddingRepo] = useState(false);
  const [addRepoError, setAddRepoError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [selectedFinding, setSelectedFinding] =
    useState<FindingResponse | null>(null);

  // Real backend data states
  const [backendHealthy, setBackendHealthy] = useState<boolean | null>(null);
  const [loadingInitial, setLoadingInitial] = useState(true);
  const [repositories, setRepositories] = useState<RepoDetails[]>([]);
  const [allFindings, setAllFindings] = useState<FindingResponse[]>([]);
  const [repoFilesMap, setRepoFilesMap] = useState<
    Record<number, SourceFileResponse[]>
  >({});

  // Action loading states
  const [ingestingRepoId, setIngestingRepoId] = useState<number | null>(null);
  const [analyzingRepoId, setAnalyzingRepoId] = useState<number | null>(null);
  const [deletingRepoId, setDeletingRepoId] = useState<number | null>(null);
  const [repoToDelete, setRepoToDelete] = useState<RepoDetails | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [viewingFilesRepoId, setViewingFilesRepoId] = useState<number | null>(
    null,
  );

  // Load repositories, findings, and health from backend
  const loadData = async () => {
    try {
      const [health, repos] = await Promise.all([
        api.getHealth().catch(() => null),
        api.listRepositories().catch(() => []),
      ]);

      setBackendHealthy(health?.status === "ok");

      // Fetch findings and files for each repository
      const findingsList: FindingResponse[] = [];
      const filesMap: Record<number, SourceFileResponse[]> = {};

      const detailedRepos: RepoDetails[] = await Promise.all(
        repos.map(async (r) => {
          const [findings, files] = await Promise.all([
            api.getRepositoryFindings(r.id).catch(() => []),
            api.getRepositoryFiles(r.id).catch(() => []),
          ]);
          findingsList.push(...findings);
          filesMap[r.id] = files;
          return {
            ...r,
            findingsCount: findings.length,
            filesCount: files.length,
          };
        }),
      );

      setRepositories(detailedRepos);
      setAllFindings(findingsList);
      setRepoFilesMap(filesMap);
    } catch {
      setBackendHealthy(false);
    } finally {
      setLoadingInitial(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  // Connect repository action
  const handleConnectRepo = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!repoUrl.trim() || isAddingRepo) return;

    setIsAddingRepo(true);
    setAddRepoError(null);
    try {
      const created = await api.connectGitHubRepository({
        url: repoUrl.trim(),
      });
      setShowAdd(false);
      setRepoUrl("");
      setNotice(`Repository ${created.full_name} connected successfully!`);
      await loadData();
    } catch (err) {
      setAddRepoError(
        err instanceof ApiError ? err.message : "Failed to connect repository",
      );
    } finally {
      setIsAddingRepo(false);
    }
  };

  // Ingest repository action
  const handleIngest = async (repoId: number, repoName: string) => {
    if (ingestingRepoId !== null) return;
    setIngestingRepoId(repoId);
    try {
      const result = await api.ingestRepository(repoId);
      setNotice(
        `Ingested ${result.files_stored} files from ${repoName} (${result.files_fetched} fetched, ${result.files_skipped} skipped)`,
      );
      await loadData();
    } catch (err) {
      setNotice(
        `Failed to ingest ${repoName}: ${
          err instanceof ApiError ? err.message : "Unknown error"
        }`,
      );
    } finally {
      setIngestingRepoId(null);
    }
  };

  // Analyze repository action
  const handleAnalyze = async (repoId: number, repoName: string) => {
    if (analyzingRepoId !== null) return;
    setAnalyzingRepoId(repoId);
    try {
      const result = await api.analyzeRepository(repoId);
      setNotice(
        `Analysis complete for ${repoName}: ${result.total_findings} findings detected across ${result.files_analyzed} Python files.`,
      );
      await loadData();
    } catch (err) {
      setNotice(
        `Failed to analyze ${repoName}: ${
          err instanceof ApiError ? err.message : "Unknown error"
        }`,
      );
    } finally {
      setAnalyzingRepoId(null);
    }
  };

  // Delete repository action
  const handleDeleteRepo = async () => {
    if (!repoToDelete || deletingRepoId !== null) return;
    const target = repoToDelete;
    setDeletingRepoId(target.id);
    setDeleteError(null);
    try {
      await api.deleteRepository(target.id);
      setRepoToDelete(null);
      setNotice(`Repository ${target.full_name} deleted successfully.`);
      await loadData();
    } catch (err) {
      setDeleteError(
        err instanceof ApiError ? err.message : "Failed to delete repository",
      );
    } finally {
      setDeletingRepoId(null);
    }
  };

  const filteredFindings = useMemo(() => {
    return allFindings.filter((finding) => {
      const matchesQuery =
        !query ||
        `${finding.message} ${finding.file_path} ${finding.rule_id} ${finding.category}`
          .toLowerCase()
          .includes(query.toLowerCase());

      const matchesSeverity =
        severityFilter === "all" ||
        finding.severity.toLowerCase() === severityFilter.toLowerCase();

      return matchesQuery && matchesSeverity;
    });
  }, [allFindings, query, severityFilter]);

  const errorCount = allFindings.filter(
    (f) => f.severity.toLowerCase() === "error",
  ).length;
  const warningCount = allFindings.filter(
    (f) => f.severity.toLowerCase() === "warning",
  ).length;
  const infoCount = allFindings.filter(
    (f) => f.severity.toLowerCase() === "info",
  ).length;

  const go = (section: Section) => {
    setActive(section);
    setMobileOpen(false);
    setNotice("");
  };

  return (
    <div className="min-h-screen bg-[#090a0b] text-zinc-100 selection:bg-cyan-300/30">
      {/* Sidebar Navigation */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-white/[0.07] bg-[#0c0d0f] transition-transform lg:translate-x-0 ${
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex h-16 items-center gap-3 border-b border-white/[0.07] px-5">
          <div className="flex size-7 items-center justify-center rounded-lg bg-cyan-300 text-black">
            <Code2 className="size-4" />
          </div>
          <span className="font-mono text-sm font-semibold tracking-tight">
            codelens<span className="text-cyan-300">.</span>
          </span>
          <button
            onClick={() => setMobileOpen(false)}
            className="ml-auto text-zinc-500 lg:hidden"
            aria-label="Close navigation"
          >
            <X className="size-4" />
          </button>
        </div>
        <div className="flex flex-1 flex-col gap-7 px-3 py-5">
          <div>
            <p className="px-3 pb-2 text-[10px] font-medium uppercase tracking-[0.18em] text-zinc-600">
              Workspace
            </p>
            <nav className="flex flex-col gap-1">
              {navItems.map(({ label, icon: Icon }) => (
                <button
                  key={label}
                  onClick={() => go(label as Section)}
                  className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm transition ${
                    active === label
                      ? "bg-white/[0.09] text-white"
                      : "text-zinc-500 hover:bg-white/[0.04] hover:text-zinc-200"
                  }`}
                >
                  <Icon className="size-4" />
                  {label}
                  {label === "Findings" && allFindings.length > 0 && (
                    <span className="ml-auto rounded bg-red-400/10 px-1.5 py-0.5 text-[10px] text-red-300">
                      {allFindings.length}
                    </span>
                  )}
                </button>
              ))}
            </nav>
          </div>
          <div>
            <p className="px-3 pb-2 text-[10px] font-medium uppercase tracking-[0.18em] text-zinc-600">
              Manage
            </p>
            <button
              onClick={() => go("Settings")}
              className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm ${
                active === "Settings"
                  ? "bg-white/[0.09] text-white"
                  : "text-zinc-500 hover:bg-white/[0.04] hover:text-zinc-200"
              }`}
            >
              <Settings2 className="size-4" />
              Settings
            </button>
          </div>
        </div>
        <div className="border-t border-white/[0.07] p-3">
          <div className="flex items-center gap-3 rounded-lg bg-white/[0.035] p-3">
            <div className="flex size-8 items-center justify-center rounded-full bg-zinc-700 text-xs font-medium">
              CL
            </div>
            <div className="min-w-0">
              <p className="truncate text-xs font-medium">CodeLens Developer</p>
              <p className="truncate text-[11px] text-zinc-600">
                Local Environment
              </p>
            </div>
          </div>
        </div>
      </aside>

      {mobileOpen && (
        <button
          aria-label="Close navigation overlay"
          onClick={() => setMobileOpen(false)}
          className="fixed inset-0 z-30 bg-black/60 lg:hidden"
        />
      )}

      {/* Main Content Area */}
      <div className="lg:pl-60">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-white/[0.07] bg-[#090a0b]/90 px-4 backdrop-blur-md sm:px-8">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setMobileOpen(true)}
              className="text-zinc-400 lg:hidden"
              aria-label="Open navigation"
            >
              <Menu className="size-5" />
            </button>
            <div className="hidden items-center gap-2 text-xs text-zinc-600 sm:flex">
              <span>Workspace</span>
              <ChevronRight className="size-3" />
              <span className="text-zinc-300">{active}</span>
            </div>
            <h1 className="text-sm font-medium text-zinc-200 sm:hidden">
              {active}
            </h1>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 text-[11px] text-zinc-500">
              {backendHealthy === null ? (
                <>
                  <span className="size-1.5 rounded-full bg-yellow-400 animate-pulse" />
                  Checking API...
                </>
              ) : backendHealthy ? (
                <>
                  <span className="size-1.5 rounded-full bg-emerald-400" />
                  API connected
                </>
              ) : (
                <>
                  <span className="size-1.5 rounded-full bg-red-400" />
                  API disconnected
                </>
              )}
            </div>
            <button
              onClick={() => loadData()}
              title="Refresh data"
              className="text-zinc-500 hover:text-zinc-200 transition"
              aria-label="Refresh data"
            >
              <RefreshCw className="size-4" />
            </button>
          </div>
        </header>

        {/* Backend Disconnected Alert */}
        {backendHealthy === false && (
          <div className="bg-red-500/10 border-b border-red-500/20 px-4 py-2.5 text-center text-xs text-red-300">
            <AlertCircle className="inline size-3.5 mr-1.5" />
            FastAPI backend is unreachable at{" "}
            <code>
              {process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"}
            </code>
            . Please ensure the backend service is running.
          </div>
        )}

        <main className="mx-auto max-w-[1400px] p-5 sm:p-8">
          {loadingInitial ? (
            <div className="flex min-h-[400px] items-center justify-center">
              <div className="flex flex-col items-center gap-3 text-zinc-500">
                <Loader2 className="size-6 animate-spin text-cyan-300" />
                <p className="text-sm">Connecting to CodeLens backend...</p>
              </div>
            </div>
          ) : (
            <>
              {active === "Dashboard" && (
                <DashboardContent
                  repositories={repositories}
                  findings={allFindings}
                  errorCount={errorCount}
                  warningCount={warningCount}
                  infoCount={infoCount}
                  onAdd={() => setShowAdd(true)}
                  onFindings={() => go("Findings")}
                  onIngest={handleIngest}
                  onAnalyze={handleAnalyze}
                  ingestingRepoId={ingestingRepoId}
                  analyzingRepoId={analyzingRepoId}
                />
              )}
              {active === "Repositories" && (
                <RepositoriesContent
                  repositories={repositories}
                  onAdd={() => setShowAdd(true)}
                  onIngest={handleIngest}
                  onAnalyze={handleAnalyze}
                  onViewFiles={(id) => setViewingFilesRepoId(id)}
                  onDelete={(repo) => {
                    setRepoToDelete(repo);
                    setDeleteError(null);
                  }}
                  ingestingRepoId={ingestingRepoId}
                  analyzingRepoId={analyzingRepoId}
                  deletingRepoId={deletingRepoId}
                />
              )}
              {active === "Findings" && (
                <FindingsContent
                  query={query}
                  setQuery={setQuery}
                  severityFilter={severityFilter}
                  setSeverityFilter={setSeverityFilter}
                  findings={filteredFindings}
                  repositories={repositories}
                  onSelect={setSelectedFinding}
                />
              )}
              {active === "Analysis" && (
                <AnalysisContent
                  repositories={repositories}
                  onAnalyze={handleAnalyze}
                  analyzingRepoId={analyzingRepoId}
                />
              )}
              {active === "Settings" && (
                <SettingsContent backendHealthy={backendHealthy} />
              )}
            </>
          )}

          {notice && (
            <div className="fixed bottom-5 right-5 z-50 flex items-center gap-3 rounded-lg border border-cyan-300/20 bg-[#111416] px-4 py-3 text-sm text-zinc-200 shadow-2xl">
              <CircleCheck className="size-4 text-emerald-300" />
              {notice}
              <button
                onClick={() => setNotice("")}
                className="text-zinc-500 hover:text-zinc-300"
              >
                <X className="size-3.5" />
              </button>
            </div>
          )}
        </main>
      </div>

      {/* Add Repository Modal */}
      {showAdd && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="w-full max-w-md rounded-xl border border-white/[0.1] bg-[#111315] p-6 shadow-2xl">
            <div className="flex items-start justify-between">
              <div>
                <h2 className="font-medium text-zinc-100">Add repository</h2>
                <p className="mt-1 text-xs text-zinc-500">
                  Connect a public GitHub repository to start analyzing.
                </p>
              </div>
              <button
                onClick={() => {
                  setShowAdd(false);
                  setAddRepoError(null);
                }}
                aria-label="Close dialog"
              >
                <X className="size-4 text-zinc-500 hover:text-zinc-300" />
              </button>
            </div>
            <form onSubmit={handleConnectRepo} className="mt-6">
              <label className="block text-xs text-zinc-400" htmlFor="repo-url">
                GitHub Repository URL
              </label>
              <input
                id="repo-url"
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                placeholder="https://github.com/owner/repository"
                disabled={isAddingRepo}
                className="mt-2 w-full rounded-lg border border-white/[0.1] bg-black/20 px-3 py-2.5 text-sm outline-none placeholder:text-zinc-700 focus:border-cyan-300/60"
              />
              {addRepoError && (
                <p className="mt-2 text-xs text-red-400 flex items-center gap-1.5">
                  <AlertCircle className="size-3.5" />
                  {addRepoError}
                </p>
              )}
              <div className="mt-5 flex justify-end gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    setShowAdd(false);
                    setAddRepoError(null);
                  }}
                  disabled={isAddingRepo}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={!repoUrl.trim() || isAddingRepo}
                  className="bg-cyan-300 text-black hover:bg-cyan-200"
                >
                  {isAddingRepo ? (
                    <>
                      <Loader2 className="size-3.5 animate-spin" />
                      Connecting...
                    </>
                  ) : (
                    "Connect repository"
                  )}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Delete Repository Confirmation Modal */}
      {repoToDelete && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          onClick={() => {
            if (deletingRepoId === null) {
              setRepoToDelete(null);
              setDeleteError(null);
            }
          }}
        >
          <div
            className="w-full max-w-md rounded-xl border border-red-500/20 bg-[#111315] p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-2.5">
                <div className="flex size-8 items-center justify-center rounded-lg bg-red-500/10 text-red-400">
                  <Trash2 className="size-4" />
                </div>
                <div>
                  <h2 className="font-medium text-zinc-100">
                    Delete repository
                  </h2>
                  <p className="mt-0.5 text-xs text-zinc-500">
                    This action cannot be undone.
                  </p>
                </div>
              </div>
              <button
                onClick={() => {
                  if (deletingRepoId === null) {
                    setRepoToDelete(null);
                    setDeleteError(null);
                  }
                }}
                disabled={deletingRepoId !== null}
                className="text-zinc-500 hover:text-zinc-300 disabled:opacity-50"
                aria-label="Close delete dialog"
              >
                <X className="size-4" />
              </button>
            </div>

            <p className="mt-4 text-xs text-zinc-300 leading-relaxed">
              Are you sure you want to delete{" "}
              <span className="font-mono font-medium text-white">
                {repoToDelete.full_name}
              </span>
              ? This will delete the repository and all of its stored source
              files and static analysis findings.
            </p>

            {deleteError && (
              <div className="mt-3 flex items-center gap-1.5 rounded-lg border border-red-500/20 bg-red-500/10 p-2.5 text-xs text-red-400">
                <AlertCircle className="size-3.5 shrink-0" />
                <span>{deleteError}</span>
              </div>
            )}

            <div className="mt-6 flex justify-end gap-2">
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  setRepoToDelete(null);
                  setDeleteError(null);
                }}
                disabled={deletingRepoId !== null}
              >
                Cancel
              </Button>
              <Button
                type="button"
                variant="destructive"
                onClick={handleDeleteRepo}
                disabled={deletingRepoId !== null}
                className="bg-red-500/20 text-red-300 border border-red-500/30 hover:bg-red-500/30"
              >
                {deletingRepoId !== null ? (
                  <>
                    <Loader2 className="size-3.5 animate-spin mr-1.5" />
                    Deleting...
                  </>
                ) : (
                  <>
                    <Trash2 className="size-3.5 mr-1.5" />
                    Delete Repository
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Finding Detail Modal */}
      {selectedFinding && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          onClick={() => setSelectedFinding(null)}
        >
          <div
            className="w-full max-w-lg rounded-xl border border-white/[0.1] bg-[#111315] p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <SeverityBadge severity={selectedFinding.severity} />
                <span className="rounded bg-white/[0.06] px-2 py-0.5 font-mono text-[11px] text-zinc-400">
                  {selectedFinding.rule_id}
                </span>
                <span className="rounded bg-white/[0.04] px-2 py-0.5 text-[11px] text-zinc-500">
                  {selectedFinding.category}
                </span>
              </div>
              <button
                onClick={() => setSelectedFinding(null)}
                aria-label="Close finding"
                className="text-zinc-500 hover:text-zinc-300"
              >
                <X className="size-4" />
              </button>
            </div>
            <h2 className="mt-4 text-base font-medium leading-snug text-zinc-100">
              {selectedFinding.message}
            </h2>
            <div className="mt-5 rounded-lg border border-white/[0.07] bg-black/30 p-3 font-mono text-xs text-zinc-300">
              <span className="text-zinc-500">File: </span>
              {selectedFinding.file_path || "Repository-level"}
              {selectedFinding.line_number && (
                <span className="text-cyan-300/80">
                  {" "}
                  : line {selectedFinding.line_number}
                </span>
              )}
            </div>
            <div className="mt-3 text-[11px] text-zinc-500">
              Detected on{" "}
              {new Date(selectedFinding.created_at).toLocaleString()}
            </div>
            <div className="mt-5 flex justify-end">
              <Button
                onClick={() => setSelectedFinding(null)}
                variant="outline"
              >
                Close
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Stored Files Modal */}
      {viewingFilesRepoId !== null && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          onClick={() => setViewingFilesRepoId(null)}
        >
          <div
            className="w-full max-w-2xl max-h-[80vh] flex flex-col rounded-xl border border-white/[0.1] bg-[#111315] p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between border-b border-white/[0.07] pb-4">
              <div>
                <h2 className="font-medium text-zinc-100">
                  Stored Source Files
                </h2>
                <p className="mt-1 text-xs text-zinc-500">
                  Files retrieved from GitHub and persisted in PostgreSQL.
                </p>
              </div>
              <button
                onClick={() => setViewingFilesRepoId(null)}
                className="text-zinc-500 hover:text-zinc-300"
              >
                <X className="size-4" />
              </button>
            </div>
            <div className="overflow-y-auto mt-4 flex-1">
              {(repoFilesMap[viewingFilesRepoId] || []).length === 0 ? (
                <div className="py-12 text-center text-sm text-zinc-500">
                  <FileCode2 className="mx-auto size-6 text-zinc-600 mb-2" />
                  No source files stored yet. Run Ingest to retrieve files.
                </div>
              ) : (
                <div className="divide-y divide-white/[0.05]">
                  {(repoFilesMap[viewingFilesRepoId] || []).map((file) => (
                    <div
                      key={file.id}
                      className="py-2.5 flex items-center justify-between text-xs"
                    >
                      <div className="font-mono text-zinc-300">{file.path}</div>
                      <div className="text-zinc-600 font-mono">
                        {(file.size / 1024).toFixed(1)} KB
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function PageHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-300/70">
          {eyebrow}
        </p>
        <h2 className="mt-2 text-2xl font-semibold tracking-tight text-zinc-100 sm:text-3xl">
          {title}
        </h2>
        <p className="mt-2 max-w-xl text-sm leading-6 text-zinc-500">
          {description}
        </p>
      </div>
      {action}
    </div>
  );
}

function DashboardContent({
  repositories,
  findings,
  errorCount,
  warningCount,
  infoCount,
  onAdd,
  onFindings,
  onIngest,
  onAnalyze,
  ingestingRepoId,
  analyzingRepoId,
}: {
  repositories: RepoDetails[];
  findings: FindingResponse[];
  errorCount: number;
  warningCount: number;
  infoCount: number;
  onAdd: () => void;
  onFindings: () => void;
  onIngest: (id: number, name: string) => void;
  onAnalyze: (id: number, name: string) => void;
  ingestingRepoId: number | null;
  analyzingRepoId: number | null;
}) {
  return (
    <div className="flex flex-col gap-8">
      <PageHeading
        eyebrow="Overview"
        title="CodeLens Intelligence"
        description="Static security and code health metrics aggregated across your repositories."
        action={
          <Button
            onClick={onAdd}
            className="bg-cyan-300 text-black hover:bg-cyan-200"
          >
            <Plus className="size-4 mr-1.5" />
            Add repository
          </Button>
        }
      />

      {/* Stats Cards */}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Repositories"
          value={repositories.length}
          hint={`${repositories.filter((r) => (r.findingsCount || 0) > 0).length} analyzed`}
          icon={GitBranch}
        />
        <StatCard
          label="Total Findings"
          value={findings.length}
          hint={`${errorCount} errors need attention`}
          icon={ShieldCheck}
          tone={errorCount > 0 ? "danger" : "default"}
        />
        <StatCard
          label="Critical Errors"
          value={errorCount}
          hint="Syntax & critical security issues"
          icon={AlertCircle}
          tone={errorCount > 0 ? "danger" : "default"}
        />
        <StatCard
          label="Warnings & Info"
          value={warningCount + infoCount}
          hint={`${warningCount} warnings, ${infoCount} info notes`}
          icon={Zap}
          tone={warningCount > 0 ? "warn" : "default"}
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-[1.4fr_1fr]">
        {/* Recent Repositories */}
        <section className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-medium text-zinc-100">
                Connected Repositories
              </h3>
              <p className="mt-1 text-xs text-zinc-600">
                Latest ingestion and static analysis status.
              </p>
            </div>
            {repositories.length > 0 && (
              <span className="text-xs text-zinc-500 font-mono">
                {repositories.length} total
              </span>
            )}
          </div>

          <div className="mt-5 flex flex-col gap-1">
            {repositories.length === 0 ? (
              <div className="py-10 text-center text-sm text-zinc-500">
                <FolderGit2 className="mx-auto size-6 text-zinc-600 mb-2" />
                No repositories connected yet. Click &quot;Add repository&quot;
                to begin.
              </div>
            ) : (
              repositories.slice(0, 5).map((repo) => (
                <div
                  key={repo.id}
                  className="flex items-center gap-3 rounded-lg px-2 py-3 hover:bg-white/[0.035] transition"
                >
                  <div className="size-2 rounded-full bg-cyan-400" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="truncate text-sm text-zinc-200">
                        {repo.full_name}
                      </p>
                      <span className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[10px] text-zinc-500">
                        {repo.default_branch}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-zinc-600">
                      {repo.filesCount || 0} source files stored
                    </p>
                  </div>
                  <div className="text-right flex items-center gap-2">
                    <span className="text-xs text-zinc-400">
                      {repo.findingsCount || 0} findings
                    </span>
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() => onAnalyze(repo.id, repo.full_name)}
                      disabled={analyzingRepoId === repo.id}
                    >
                      {analyzingRepoId === repo.id ? (
                        <Loader2 className="size-3 animate-spin" />
                      ) : (
                        "Analyze"
                      )}
                    </Button>
                  </div>
                </div>
              ))
            )}
          </div>
        </section>

        {/* Findings by Severity Breakdown */}
        <section className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-medium text-zinc-100">
                Findings by Severity
              </h3>
              <p className="mt-1 text-xs text-zinc-600">
                Real AST analysis distribution.
              </p>
            </div>
            <button
              onClick={onFindings}
              className="text-xs text-cyan-300 hover:text-cyan-200"
            >
              Explore
            </button>
          </div>

          <div className="mt-7 flex items-end justify-between gap-3 px-2">
            {[
              { label: "Errors", count: errorCount, color: "bg-red-400" },
              { label: "Warnings", count: warningCount, color: "bg-amber-300" },
              { label: "Info", count: infoCount, color: "bg-cyan-300" },
            ].map((item) => {
              const maxCount = Math.max(errorCount, warningCount, infoCount, 1);
              const heightPct = Math.max(
                12,
                Math.round((item.count / maxCount) * 100),
              );
              return (
                <div
                  key={item.label}
                  className="flex flex-1 flex-col items-center gap-2"
                >
                  <div className="text-xs font-mono text-zinc-400">
                    {item.count}
                  </div>
                  <div className="h-32 w-full flex items-end">
                    <div
                      className={`w-full rounded-t-sm ${item.color} transition-all`}
                      style={{ height: `${heightPct}%`, opacity: 0.85 }}
                    />
                  </div>
                  <span className="text-[11px] text-zinc-500">
                    {item.label}
                  </span>
                </div>
              );
            })}
          </div>
        </section>
      </div>

      {/* Needs Attention: Latest findings */}
      <section className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-medium text-zinc-100">
              Recent Findings
            </h3>
            <p className="mt-1 text-xs text-zinc-600">
              Static analysis results detected in stored code.
            </p>
          </div>
          <button
            onClick={onFindings}
            className="text-xs text-cyan-300 hover:text-cyan-200"
          >
            View all findings
          </button>
        </div>

        <div className="mt-5 grid gap-2 md:grid-cols-2">
          {findings.length === 0 ? (
            <div className="col-span-2 py-8 text-center text-sm text-zinc-500">
              <ShieldCheck className="mx-auto size-6 text-emerald-400 mb-2" />
              No findings detected. Run static analysis on a repository to see
              results.
            </div>
          ) : (
            findings.slice(0, 6).map((finding) => (
              <div
                key={finding.id}
                className="flex items-center gap-3 rounded-lg border border-white/[0.06] bg-black/10 p-3"
              >
                <SeverityBadge severity={finding.severity} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs text-zinc-300 font-medium">
                    {finding.message}
                  </p>
                  <p className="mt-1 truncate font-mono text-[10px] text-zinc-500">
                    {finding.file_path || "Repository"}
                    {finding.line_number
                      ? `:${finding.line_number}`
                      : ""} · {finding.rule_id}
                  </p>
                </div>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}

function RepositoriesContent({
  repositories,
  onAdd,
  onIngest,
  onAnalyze,
  onViewFiles,
  onDelete,
  ingestingRepoId,
  analyzingRepoId,
  deletingRepoId,
}: {
  repositories: RepoDetails[];
  onAdd: () => void;
  onIngest: (id: number, name: string) => void;
  onAnalyze: (id: number, name: string) => void;
  onViewFiles: (id: number) => void;
  onDelete: (repo: RepoDetails) => void;
  ingestingRepoId: number | null;
  analyzingRepoId: number | null;
  deletingRepoId: number | null;
}) {
  return (
    <div className="flex flex-col gap-8">
      <PageHeading
        eyebrow="Workspace / Repositories"
        title="Repositories"
        description="Connect your codebases, ingest source files, and run static analysis."
        action={
          <Button
            onClick={onAdd}
            className="bg-cyan-300 text-black hover:bg-cyan-200"
          >
            <Plus className="size-4 mr-1.5" />
            Add repository
          </Button>
        }
      />

      {repositories.length === 0 ? (
        <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-12 text-center">
          <FolderGit2 className="mx-auto size-8 text-zinc-600 mb-3" />
          <h3 className="text-base font-medium text-zinc-300">
            No repositories yet
          </h3>
          <p className="mt-1 text-xs text-zinc-500 max-w-sm mx-auto">
            Add a public GitHub repository URL to ingest files and run the code
            analysis engine.
          </p>
          <Button
            onClick={onAdd}
            className="mt-4 bg-cyan-300 text-black hover:bg-cyan-200"
          >
            Connect first repository
          </Button>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {repositories.map((repo) => (
            <div
              key={repo.id}
              className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5 flex flex-col justify-between"
            >
              <div>
                <div className="flex items-start justify-between">
                  <span className="rounded bg-cyan-300/10 border border-cyan-300/20 px-2 py-0.5 font-mono text-[10px] text-cyan-300">
                    {repo.default_branch}
                  </span>
                  <div className="flex items-center gap-2">
                    <a
                      href={repo.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-zinc-600 hover:text-zinc-400 text-xs flex items-center gap-1"
                      title="View on GitHub"
                    >
                      <GitBranch className="size-3.5" />
                    </a>
                    <button
                      onClick={() => onDelete(repo)}
                      disabled={deletingRepoId === repo.id}
                      className="text-zinc-600 hover:text-rose-400 transition-colors disabled:opacity-50 cursor-pointer"
                      title="Delete repository"
                    >
                      {deletingRepoId === repo.id ? (
                        <Loader2 className="size-3.5 animate-spin text-rose-400" />
                      ) : (
                        <Trash2 className="size-3.5" />
                      )}
                    </button>
                  </div>
                </div>
                <h3 className="mt-4 font-mono text-sm font-semibold text-zinc-100 truncate">
                  {repo.full_name}
                </h3>
                <p className="mt-1 text-xs text-zinc-500 truncate">
                  {repo.url}
                </p>
                <div className="mt-4 flex items-center gap-4 text-xs text-zinc-400">
                  <button
                    onClick={() => onViewFiles(repo.id)}
                    className="hover:text-cyan-300 transition underline underline-offset-4"
                  >
                    {repo.filesCount || 0} files stored
                  </button>
                  <span>{repo.findingsCount || 0} findings</span>
                </div>
              </div>

              <div className="mt-6 flex items-center justify-between border-t border-white/[0.07] pt-4 gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onIngest(repo.id, repo.full_name)}
                  disabled={ingestingRepoId === repo.id}
                >
                  {ingestingRepoId === repo.id ? (
                    <>
                      <Loader2 className="size-3 animate-spin mr-1" />
                      Ingesting...
                    </>
                  ) : (
                    "Ingest Files"
                  )}
                </Button>
                <Button
                  size="sm"
                  onClick={() => onAnalyze(repo.id, repo.full_name)}
                  disabled={analyzingRepoId === repo.id}
                  className="bg-cyan-300 text-black hover:bg-cyan-200"
                >
                  {analyzingRepoId === repo.id ? (
                    <>
                      <Loader2 className="size-3 animate-spin mr-1" />
                      Analyzing...
                    </>
                  ) : (
                    <>
                      <Sparkles className="size-3.5 mr-1" />
                      Analyze
                    </>
                  )}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function FindingsContent({
  query,
  setQuery,
  severityFilter,
  setSeverityFilter,
  findings,
  repositories,
  onSelect,
}: {
  query: string;
  setQuery: (s: string) => void;
  severityFilter: string;
  setSeverityFilter: (s: string) => void;
  findings: FindingResponse[];
  repositories: RepoDetails[];
  onSelect: (f: FindingResponse) => void;
}) {
  const repoMap = useMemo(() => {
    const map = new Map<number, string>();
    repositories.forEach((r) => map.set(r.id, r.name));
    return map;
  }, [repositories]);

  return (
    <div className="flex flex-col gap-8">
      <PageHeading
        eyebrow="Workspace / Findings"
        title="Findings"
        description="Inspect code quality and syntax issues detected across stored repositories."
      />

      {/* Filters Bar */}
      <div className="flex flex-col gap-3 sm:flex-row">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-zinc-600" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search findings by message, rule, or file path..."
            className="h-10 w-full rounded-lg border border-white/[0.09] bg-white/[0.025] pl-9 pr-3 text-sm outline-none placeholder:text-zinc-700 focus:border-cyan-300/50"
          />
        </div>

        {/* Severity filter selector */}
        <div className="flex items-center gap-1 rounded-lg border border-white/[0.09] bg-white/[0.025] p-1 text-xs">
          {["all", "error", "warning", "info"].map((sev) => (
            <button
              key={sev}
              onClick={() => setSeverityFilter(sev)}
              className={`rounded-md px-2.5 py-1 transition capitalize ${
                severityFilter === sev
                  ? "bg-white/[0.1] text-white font-medium"
                  : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {sev}
            </button>
          ))}
        </div>
      </div>

      {/* Findings Table */}
      <div className="overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.02]">
        <div className="hidden grid-cols-[1fr_160px_120px_100px_120px] gap-4 border-b border-white/[0.07] px-5 py-3 text-[10px] uppercase tracking-wider text-zinc-600 md:grid">
          <span>Finding Message</span>
          <span>Rule ID</span>
          <span>Category</span>
          <span>Severity</span>
          <span>Location</span>
        </div>

        {findings.length ? (
          findings.map((finding) => (
            <button
              key={finding.id}
              onClick={() => onSelect(finding)}
              className="grid w-full items-center gap-4 border-b border-white/[0.05] px-5 py-4 text-left transition last:border-0 hover:bg-white/[0.035] md:grid-cols-[1fr_160px_120px_100px_120px]"
            >
              <div className="min-w-0">
                <p className="truncate text-sm text-zinc-200 font-medium">
                  {finding.message}
                </p>
                <p className="mt-1 truncate font-mono text-[10px] text-zinc-500">
                  {repoMap.get(finding.repository_id) ||
                    `Repo #${finding.repository_id}`}
                </p>
              </div>
              <span className="font-mono text-xs text-zinc-400">
                {finding.rule_id}
              </span>
              <span className="text-xs text-zinc-500 capitalize">
                {finding.category}
              </span>
              <span>
                <SeverityBadge severity={finding.severity} />
              </span>
              <span className="truncate font-mono text-[11px] text-zinc-500">
                {finding.file_path
                  ? `${finding.file_path}:${finding.line_number || 1}`
                  : "-"}
              </span>
            </button>
          ))
        ) : (
          <div className="p-12 text-center">
            <Search className="mx-auto size-5 text-zinc-700" />
            <p className="mt-3 text-sm text-zinc-400">
              {allFindingsCount(repositories) === 0
                ? "No findings found. Ingest and analyze a repository to generate findings."
                : "No findings match your search filter."}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function allFindingsCount(repositories: RepoDetails[]) {
  return repositories.reduce((acc, r) => acc + (r.findingsCount || 0), 0);
}

function AnalysisContent({
  repositories,
  onAnalyze,
  analyzingRepoId,
}: {
  repositories: RepoDetails[];
  onAnalyze: (id: number, name: string) => void;
  analyzingRepoId: number | null;
}) {
  return (
    <div className="flex flex-col gap-8">
      <PageHeading
        eyebrow="Workspace / Analysis"
        title="Analysis Engine"
        description="Run the Python AST static code analyzer against stored source files."
      />

      {repositories.length === 0 ? (
        <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-12 text-center text-sm text-zinc-500">
          <Activity className="mx-auto size-6 text-zinc-600 mb-2" />
          No repositories to analyze. Connect a repository first.
        </div>
      ) : (
        <div className="space-y-4">
          {repositories.map((repo) => (
            <div
              key={repo.id}
              className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5 flex items-center justify-between"
            >
              <div>
                <h3 className="font-mono text-sm font-medium text-zinc-200">
                  {repo.full_name}
                </h3>
                <p className="mt-1 text-xs text-zinc-500">
                  {repo.filesCount || 0} stored files ·{" "}
                  {repo.findingsCount || 0} findings recorded
                </p>
              </div>
              <Button
                size="sm"
                onClick={() => onAnalyze(repo.id, repo.full_name)}
                disabled={analyzingRepoId === repo.id}
                className="bg-cyan-300 text-black hover:bg-cyan-200"
              >
                {analyzingRepoId === repo.id ? (
                  <>
                    <Loader2 className="size-3.5 animate-spin mr-1.5" />
                    Analyzing...
                  </>
                ) : (
                  <>
                    <Sparkles className="size-3.5 mr-1.5" />
                    Run Analysis
                  </>
                )}
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SettingsContent({
  backendHealthy,
}: {
  backendHealthy: boolean | null;
}) {
  return (
    <div className="flex flex-col gap-8">
      <PageHeading
        eyebrow="Manage / Settings"
        title="Settings"
        description="Workspace configuration and API connectivity details."
      />

      <div className="max-w-2xl rounded-xl border border-white/[0.08] bg-white/[0.025] p-6 space-y-6">
        <div>
          <h3 className="text-sm font-medium text-zinc-200">
            Backend API Connection
          </h3>
          <p className="mt-1 text-xs text-zinc-500">
            Target URL for FastAPI server endpoints and database health.
          </p>
          <div className="mt-3 flex items-center gap-2 rounded-lg border border-white/[0.08] bg-black/30 px-3 py-2.5 font-mono text-xs text-zinc-400">
            <TerminalSquare className="size-4 text-cyan-300" />
            {process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"}
          </div>
          <div className="mt-2 text-xs flex items-center gap-1.5">
            <span
              className={`size-2 rounded-full ${
                backendHealthy ? "bg-emerald-400" : "bg-red-400"
              }`}
            />
            <span
              className={backendHealthy ? "text-emerald-300" : "text-red-300"}
            >
              {backendHealthy
                ? "Backend server healthy & responsive"
                : "Backend server disconnected"}
            </span>
          </div>
        </div>

        <div className="border-t border-white/[0.07] pt-6">
          <h3 className="text-sm font-medium text-zinc-200">
            Supported Workflow
          </h3>
          <ul className="mt-3 space-y-2 text-xs text-zinc-400 list-disc list-inside">
            <li>Connect public GitHub repositories via URL parsing</li>
            <li>
              Fetch recursive git trees and store source files in PostgreSQL
            </li>
            <li>
              Run AST static analysis (Syntax, Unused Imports, Bare Excepts,
              Long Functions, Comments)
            </li>
            <li>Idempotent re-analysis and structured finding management</li>
          </ul>
        </div>
      </div>
    </div>
  );
}

export default CodeLensDashboard;
