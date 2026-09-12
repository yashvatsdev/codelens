"use client";

import { useMemo } from "react";
import {
  Activity,
  CheckCircle2,
  FolderGit2,
  GitBranch,
  Play,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import type { FindingResponse, RepositoryResponse } from "@/types/api";

export interface ActivityItem {
  id: string;
  type: "repo_connected" | "analysis_completed" | "scan_started" | "scan_completed";
  description: string;
  context: string;
  timestamp: string; // ISO string or parsable date
}

interface RecentActivityProps {
  repositories: RepositoryResponse[];
  findings: FindingResponse[];
  sessionActivities?: ActivityItem[];
}

function formatRelativeTime(dateStr: string): string {
  try {
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return "recently";

    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHour / 24);

    if (diffSec < 45) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHour < 24) return `${diffHour}h ago`;
    if (diffDay === 1) return "yesterday";
    if (diffDay < 7) return `${diffDay}d ago`;

    return date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  } catch {
    return "recently";
  }
}

export function RecentActivity({
  repositories,
  findings,
  sessionActivities = [],
}: RecentActivityProps) {
  // Derive real activities from persistent models + session events
  const activities = useMemo(() => {
    const items: ActivityItem[] = [...sessionActivities];

    // 1. Repository connection events from real created_at
    for (const repo of repositories) {
      if (repo.created_at) {
        items.push({
          id: `repo-connected-${repo.id}`,
          type: "repo_connected",
          description: "Repository connected to workspace",
          context: repo.full_name,
          timestamp: repo.created_at,
        });
      }

      // 2. Latest analysis completion for repos that have findings
      const repoFindings = findings.filter((f) => f.repository_id === repo.id);
      if (repoFindings.length > 0) {
        // Find most recent finding timestamp
        const latestFinding = repoFindings.reduce((latest, f) => {
          if (!latest.created_at) return f;
          if (!f.created_at) return latest;
          return new Date(f.created_at) > new Date(latest.created_at) ? f : latest;
        }, repoFindings[0]);

        if (latestFinding.created_at) {
          items.push({
            id: `analysis-completed-${repo.id}`,
            type: "analysis_completed",
            description: `Static analysis completed (${repoFindings.length} findings)`,
            context: repo.full_name,
            timestamp: latestFinding.created_at,
          });
        }
      }
    }

    // Deduplicate by id and sort descending by timestamp
    const unique = Array.from(new Map(items.map((i) => [i.id, i])).values());
    return unique.sort((a, b) => {
      const timeA = new Date(a.timestamp).getTime() || 0;
      const timeB = new Date(b.timestamp).getTime() || 0;
      return timeB - timeA;
    });
  }, [repositories, findings, sessionActivities]);

  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-5 shadow-sm">
      <div className="flex items-center justify-between border-b border-white/[0.06] pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-zinc-100">Recent Activity</h3>
            {activities.length > 0 && (
              <span className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
                {activities.length} {activities.length === 1 ? "Event" : "Events"}
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-zinc-500">
            Real codebase scans, ingestion milestones, and repository events.
          </p>
        </div>
      </div>

      {activities.length === 0 ? (
        // Clean empty state strictly matching prompt specification
        <div className="flex flex-col items-center justify-center py-10 text-center">
          <div className="mb-3 flex size-12 items-center justify-center rounded-xl border border-white/[0.08] bg-black/40">
            <Activity className="size-5 text-zinc-500" />
          </div>
          <h4 className="text-sm font-medium text-zinc-300">No recent activity</h4>
          <p className="mt-1 max-w-xs text-xs text-zinc-500 leading-relaxed">
            Activity will appear here as you scan and review repositories.
          </p>
        </div>
      ) : (
        <div className="mt-4 divide-y divide-white/[0.04]">
          {activities.slice(0, 8).map((activity) => {
            let Icon = Activity;
            let iconColor = "text-cyan-400 bg-cyan-400/10 border-cyan-400/20";

            if (activity.type === "repo_connected") {
              Icon = FolderGit2;
              iconColor = "text-blue-400 bg-blue-400/10 border-blue-400/20";
            } else if (activity.type === "analysis_completed") {
              Icon = ShieldCheck;
              iconColor = "text-emerald-400 bg-emerald-400/10 border-emerald-400/20";
            } else if (activity.type === "scan_started") {
              Icon = Play;
              iconColor = "text-amber-400 bg-amber-400/10 border-amber-400/20";
            } else if (activity.type === "scan_completed") {
              Icon = CheckCircle2;
              iconColor = "text-emerald-400 bg-emerald-400/10 border-emerald-400/20";
            }

            return (
              <div
                key={activity.id}
                className="flex items-center justify-between py-3 first:pt-1 last:pb-1"
              >
                <div className="flex items-center gap-3 min-w-0 pr-4">
                  <div
                    className={`flex size-8 shrink-0 items-center justify-center rounded-lg border ${iconColor}`}
                  >
                    <Icon className="size-4" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-zinc-200 truncate">
                      {activity.description}
                    </p>
                    <p className="mt-0.5 text-[11px] font-mono text-zinc-500 truncate">
                      {activity.context}
                    </p>
                  </div>
                </div>

                <div className="shrink-0 text-right">
                  <span className="text-[11px] font-mono text-zinc-500">
                    {formatRelativeTime(activity.timestamp)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
