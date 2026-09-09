export interface RepositoryResponse {
  id: number;
  github_id: string;
  name: string;
  full_name: string;
  owner: string;
  url: string;
  default_branch: string;
  created_at: string;
}

export interface GitHubRepositoryCreate {
  url: string;
  default_branch?: string;
}

export interface IngestionFileResponse {
  path: string;
  sha: string;
  size: number;
}

export interface IngestionResponse {
  owner: string;
  repo: string;
  branch: string;
  total_tree_entries: number;
  files_identified: number;
  files_fetched: number;
  files_skipped: number;
  files_stored: number;
  fetched_files: IngestionFileResponse[];
  errors: string[];
}

export interface SourceFileResponse {
  id: number;
  repository_id: number;
  path: string;
  sha: string;
  size: number;
  created_at: string;
}

export type SeverityType = "error" | "warning" | "info";
export type CategoryType =
  | "syntax"
  | "style"
  | "maintainability"
  | "bug"
  | "complexity";

export interface FindingResponse {
  id: number;
  repository_id: number;
  file_path: string | null;
  line_number: number | null;
  severity: SeverityType | string;
  category: CategoryType | string;
  message: string;
  rule_id: string;
  created_at: string;
}

export interface AnalysisSummaryResponse {
  repository_id: number;
  files_analyzed: number;
  total_findings: number;
  findings: FindingResponse[];
}

export interface HealthResponse {
  status: string;
  service?: string;
  database?: string;
  detail?: string;
}

export interface FindingExplanationResponse {
  finding_id: number;
  explanation: string;
  remediation?: string | null;
}

export interface FindingFixResponse {
  finding_id: number;
  explanation: string;
  original_code: string;
  fixed_code: string;
  diff?: string | null;
  resulting_code?: string | null;
}

export interface FindingTestResponse {
  finding_id: number;
  test_framework: string;
  test_file: string;
  test_code: string;
  explanation: string;
}

