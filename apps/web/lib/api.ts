import type {
  AIPRReviewResponse,
  AnalysisSummaryResponse,
  FindingExplanationResponse,
  FindingFixResponse,
  FindingResponse,
  FindingTestResponse,
  GitHubRepositoryCreate,
  HealthResponse,
  IngestionResponse,
  PRFindingFixRequest,
  PRFindingFixResponse,
  RepositoryResponse,
  SourceFileResponse,
} from "@/types/api";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export class ApiError extends Error {
  status: number;
  data: unknown;
  code?: string;

  constructor(message: string, status: number, data?: unknown, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
    this.code = code;
  }
}

export interface FriendlyError {
  title?: string;
  message: string;
  isQuota: boolean;
}

export function getFriendlyErrorMessage(
  err: unknown,
  defaultMessage = "An unexpected error occurred. Please try again.",
): FriendlyError {
  if (err instanceof ApiError) {
    const rawMsg = typeof err.message === "string" ? err.message : "";
    const isQuota =
      err.status === 429 ||
      err.code === "AI_QUOTA_EXCEEDED" ||
      rawMsg.toLowerCase().includes("quota") ||
      rawMsg.toLowerCase().includes("resource_exhausted") ||
      rawMsg.toLowerCase().includes("rate limit") ||
      rawMsg.toLowerCase().includes("too many requests");

    if (isQuota) {
      return {
        title: "AI quota temporarily exhausted",
        message:
          "CodeLens has reached its current AI usage limit. Static analysis is still available. Please try again later.",
        isQuota: true,
      };
    }

    if (err.status === 502 || err.status === 503) {
      return {
        title: "AI service unavailable",
        message:
          "AI service is currently unavailable. Static analysis is still available. Please try again later.",
        isQuota: false,
      };
    }

    const cleaned = (rawMsg || defaultMessage)
      .replace(/gemini/gi, "AI")
      .replace(/google/gi, "AI");

    return {
      message: cleaned,
      isQuota: false,
    };
  }

  if (err instanceof Error) {
    const cleaned = err.message
      .replace(/gemini/gi, "AI")
      .replace(/google/gi, "AI");
    return {
      message: cleaned,
      isQuota: false,
    };
  }

  return {
    message: defaultMessage,
    isQuota: false,
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  try {
    const response = await fetch(url, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...init?.headers,
      },
    });

    if (!response.ok) {
      let errorDetail = `Request failed with status ${response.status}`;
      let errorCode: string | undefined = undefined;
      let errorData: unknown = undefined;
      try {
        const errorJson = await response.json();
        errorData = errorJson;
        if (errorJson.detail) {
          if (typeof errorJson.detail === "string") {
            errorDetail = errorJson.detail;
          } else if (
            typeof errorJson.detail === "object" &&
            errorJson.detail !== null
          ) {
            errorDetail =
              errorJson.detail.message || JSON.stringify(errorJson.detail);
            errorCode = errorJson.detail.code;
          }
        } else if (errorJson.message) {
          errorDetail = errorJson.message;
        }
        if (errorJson.code) {
          errorCode = errorJson.code;
        }
      } catch {
        // use fallback errorDetail
      }
      throw new ApiError(errorDetail, response.status, errorData, errorCode);
    }

    return (await response.json()) as T;
  } catch (err) {
    if (err instanceof ApiError) {
      throw err;
    }
    throw new ApiError(
      err instanceof Error
        ? err.message
        : "Failed to communicate with CodeLens API",
      0,
    );
  }
}

export const api = {
  getHealth: () => request<HealthResponse>("/health"),
  getDbHealth: () => request<HealthResponse>("/db/health"),
  listRepositories: () => request<RepositoryResponse[]>("/repositories"),
  getRepository: (id: number) =>
    request<RepositoryResponse>(`/repositories/${id}`),
  connectGitHubRepository: (data: GitHubRepositoryCreate) =>
    request<RepositoryResponse>("/repositories/github", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  ingestRepository: (repositoryId: number) =>
    request<IngestionResponse>(`/repositories/${repositoryId}/ingest`, {
      method: "POST",
    }),
  getRepositoryFiles: (repositoryId: number) =>
    request<SourceFileResponse[]>(`/repositories/${repositoryId}/files`),
  analyzeRepository: (repositoryId: number) =>
    request<AnalysisSummaryResponse>(`/repositories/${repositoryId}/analyze`, {
      method: "POST",
    }),
  getRepositoryFindings: (repositoryId: number) =>
    request<FindingResponse[]>(`/repositories/${repositoryId}/findings`),
  deleteRepository: (repositoryId: number) =>
    request<{ status: string; message: string }>(
      `/repositories/${repositoryId}`,
      {
        method: "DELETE",
      },
    ),
  explainFinding: (repositoryId: number, findingId: number) =>
    request<FindingExplanationResponse>(
      `/repositories/${repositoryId}/findings/${findingId}/explain`,
      {
        method: "POST",
      },
    ),
  fixFinding: (repositoryId: number, findingId: number) =>
    request<FindingFixResponse>(
      `/repositories/${repositoryId}/findings/${findingId}/fix`,
      {
        method: "POST",
      },
    ),
  generateTest: (repositoryId: number, findingId: number) =>
    request<FindingTestResponse>(
      `/repositories/${repositoryId}/findings/${findingId}/test`,
      {
        method: "POST",
      },
    ),
  aiReviewPullRequest: (repositoryId: number, pullRequestNumber: number) =>
    request<AIPRReviewResponse>(
      `/repositories/${repositoryId}/pull-requests/${pullRequestNumber}/ai-review`,
      {
        method: "POST",
      },
    ),
  fixPRFinding: (
    repositoryId: number,
    pullRequestNumber: number,
    payload: PRFindingFixRequest,
  ) =>
    request<PRFindingFixResponse>(
      `/repositories/${repositoryId}/pull-requests/${pullRequestNumber}/findings/fix`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
};
