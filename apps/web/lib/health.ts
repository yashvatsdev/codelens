import type { FindingResponse } from "@/types/api";

export type HealthLabel = "Excellent" | "Good" | "Fair" | "Poor";

export interface CategoryBreakdown {
  category: string;
  count: number;
  percentage: number;
}

export interface HealthScoreResult {
  score: number;
  label: HealthLabel;
  totalFindings: number;
  errorCount: number;
  warningCount: number;
  infoCount: number;
  categories: CategoryBreakdown[];
  deductions: {
    errors: number;
    warnings: number;
    info: number;
  };
}

/**
 * Deterministic CodeLens Health Score (0–100) based on static analysis findings.
 *
 * Scoring Formula:
 * - Base Score: 100 points
 * - Deductions by severity:
 *   - Error:   -15 points per finding (critical security, syntax, and bug risks)
 *   - Warning: -5 points per finding (complexity, maintainability, bad practices)
 *   - Info:    -1 point per finding (style guides, code smell hints)
 * - Clamped strictly between 0 and 100: Math.max(0, Math.min(100, Math.round(score)))
 *
 * Health Labels:
 * - 90 – 100: "Excellent" (High code quality, minimal or no actionable issues)
 * - 75 – 89:  "Good" (Minor warnings or informational items)
 * - 50 – 74:  "Fair" (Moderate issues requiring attention)
 * - 0 – 49:   "Poor" (High concentration of critical errors or severe issues)
 */
export function calculateHealthScore(findings: FindingResponse[]): HealthScoreResult {
  let errorCount = 0;
  let warningCount = 0;
  let infoCount = 0;
  const categoryCounts: Record<string, number> = {};

  for (const f of findings) {
    const sev = (f.severity || "").toLowerCase();
    if (sev === "error") {
      errorCount++;
    } else if (sev === "warning") {
      warningCount++;
    } else {
      infoCount++;
    }

    const cat = (f.category || "other").toLowerCase();
    categoryCounts[cat] = (categoryCounts[cat] || 0) + 1;
  }

  const errorDeduction = errorCount * 15;
  const warningDeduction = warningCount * 5;
  const infoDeduction = infoCount * 1;
  const totalDeductions = errorDeduction + warningDeduction + infoDeduction;

  const rawScore = 100 - totalDeductions;
  const score = Math.max(0, Math.min(100, Math.round(rawScore)));

  let label: HealthLabel = "Excellent";
  if (score < 50) {
    label = "Poor";
  } else if (score < 75) {
    label = "Fair";
  } else if (score < 90) {
    label = "Good";
  } else {
    label = "Excellent";
  }

  const totalFindings = findings.length;
  const categories: CategoryBreakdown[] = Object.entries(categoryCounts)
    .map(([category, count]) => ({
      category,
      count,
      percentage: totalFindings > 0 ? Math.round((count / totalFindings) * 100) : 0,
    }))
    .sort((a, b) => b.count - a.count);

  return {
    score,
    label,
    totalFindings,
    errorCount,
    warningCount,
    infoCount,
    categories,
    deductions: {
      errors: errorDeduction,
      warnings: warningDeduction,
      info: infoDeduction,
    },
  };
}

export function getHealthColor(label: HealthLabel): {
  text: string;
  bg: string;
  border: string;
  stroke: string;
  badge: string;
} {
  switch (label) {
    case "Excellent":
      return {
        text: "text-emerald-400",
        bg: "bg-emerald-950/30",
        border: "border-emerald-500/30",
        stroke: "#34d399",
        badge: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
      };
    case "Good":
      return {
        text: "text-cyan-400",
        bg: "bg-cyan-950/30",
        border: "border-cyan-500/30",
        stroke: "#22d3ee",
        badge: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30",
      };
    case "Fair":
      return {
        text: "text-amber-400",
        bg: "bg-amber-950/30",
        border: "border-amber-500/30",
        stroke: "#fbbf24",
        badge: "bg-amber-500/15 text-amber-300 border-amber-500/30",
      };
    case "Poor":
      return {
        text: "text-rose-400",
        bg: "bg-rose-950/30",
        border: "border-rose-500/30",
        stroke: "#f43f5e",
        badge: "bg-rose-500/15 text-rose-300 border-rose-500/30",
      };
  }
}
