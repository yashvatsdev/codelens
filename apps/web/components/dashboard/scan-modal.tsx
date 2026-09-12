"use client";

import { useState } from "react";
import {
  FolderGit2,
  GitBranch,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import type { RepositoryResponse, ScanStatusResponse } from "@/types/api";

interface RepoDetails extends RepositoryResponse {
  findingsCount?: number;
  filesCount?: number;
}

interface ScanModalProps {
  isOpen: boolean;
  onClose: () => void;
  repositories: RepoDetails[];
  activeScans: Record<number, ScanStatusResponse>;
  onScan: (repoId: number, repoName: string) => void;
  onAddRepository: () => void;
}

export function ScanModal({
  isOpen,
  onClose,
  repositories,
  activeScans,
  onScan,
  onAddRepository,
}: ScanModalProps) {
  const [filter, setFilter] = useState("");

  if (!isOpen) return null;

  const filtered = repositories.filter((r) =>
    r.full_name.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-xs">
      <div className="w-full max-w-lg rounded-xl border border-white/[0.1] bg-[#111315] p-6 shadow-2xl">
        <div className="flex items-start justify-between border-b border-white/[0.08] pb-4">
          <div>
            <h2 className="text-base font-semibold text-zinc-100 flex items-center gap-2">
              <RefreshCw className="size-4 text-cyan-400" />
              Scan Repository
            </h2>
            <p className="mt-1 text-xs text-zinc-400">
              Trigger asynchronous background ingestion and static AST analysis.
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-300 transition"
          >
            <X className="size-4" />
          </button>
        </div>

        {repositories.length === 0 ? (
          <div className="py-8 text-center">
            <FolderGit2 className="mx-auto size-8 text-zinc-600 mb-2" />
            <h3 className="text-sm font-medium text-zinc-300">No repositories connected</h3>
            <p className="mt-1 text-xs text-zinc-500 max-w-xs mx-auto">
              You must connect a GitHub repository before you can run a scan.
            </p>
            <Button
              onClick={() => {
                onClose();
                onAddRepository();
              }}
              className="mt-4 bg-cyan-300 text-black hover:bg-cyan-200 text-xs"
            >
              <Plus className="size-3.5 mr-1" />
              Connect Repository
            </Button>
          </div>
        ) : (
          <div className="mt-4 space-y-3">
            {repositories.length > 4 && (
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 size-3.5 text-zinc-500" />
                <input
                  type="text"
                  placeholder="Filter repositories..."
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  className="w-full rounded-lg border border-white/[0.08] bg-black/30 py-1.5 pl-8 pr-3 font-mono text-xs text-zinc-200 placeholder-zinc-600 focus:border-cyan-400 focus:outline-none"
                />
              </div>
            )}

            <div className="max-h-72 overflow-y-auto space-y-2 pr-1">
              {filtered.map((repo) => {
                const scan = activeScans[repo.id];
                const isScanning =
                  scan?.status === "queued" || scan?.status === "running";

                return (
                  <div
                    key={repo.id}
                    className="flex items-center justify-between rounded-lg border border-white/[0.06] bg-white/[0.02] p-3 hover:bg-white/[0.04] transition"
                  >
                    <div className="min-w-0 pr-3">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs font-medium text-zinc-200 truncate">
                          {repo.full_name}
                        </span>
                        <span className="rounded bg-white/[0.05] px-1.5 py-0.5 font-mono text-[10px] text-zinc-500">
                          {repo.default_branch}
                        </span>
                      </div>
                      <div className="mt-1 flex items-center gap-3 text-[11px] text-zinc-500">
                        <span>{repo.filesCount || 0} files stored</span>
                        <span>·</span>
                        <span>{repo.findingsCount || 0} findings</span>
                      </div>
                    </div>

                    <Button
                      size="sm"
                      onClick={() => {
                        onScan(repo.id, repo.full_name);
                        onClose();
                      }}
                      disabled={isScanning}
                      className={
                        isScanning
                          ? "opacity-60"
                          : "bg-cyan-300 text-black hover:bg-cyan-200"
                      }
                    >
                      {isScanning ? (
                        <>
                          <Loader2 className="size-3 animate-spin mr-1.5" />
                          {scan.progress}%
                        </>
                      ) : (
                        <>
                          <RefreshCw className="size-3 mr-1.5" />
                          Scan
                        </>
                      )}
                    </Button>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className="mt-5 flex justify-end border-t border-white/[0.06] pt-4">
          <Button variant="outline" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </div>
  );
}
