"use client";

import { useState, useRef, useEffect } from "react";
import {
  Loader2,
  MessageSquare,
  FileCode2,
  ChevronDown,
  AlertCircle,
  Sparkles,
  Send,
} from "lucide-react";
import { api, getFriendlyErrorMessage } from "@/lib/api";
import type { AskResponse, AskSourceReference, RepositoryResponse } from "@/types/api";

const SUGGESTED_QUESTIONS = [
  "How does authentication work?",
  "Where is database access handled?",
  "How are findings generated?",
  "Where are GitHub repositories ingested?",
  "How does the AI fix generation work?",
];

const MAX_QUESTION_LENGTH = 2000;

function PageHeading({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between cl-fade-up">
      <div>
        <p className="cl-mono-label mb-1.5">{eyebrow}</p>
        <h2 className="text-2xl font-semibold tracking-tight text-zinc-100 sm:text-3xl">
          {title}
        </h2>
        <p className="mt-2 max-w-2xl text-sm text-zinc-400">{description}</p>
      </div>
    </div>
  );
}

function SourceCard({ source }: { source: AskSourceReference }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-zinc-800 bg-zinc-900/50 px-3.5 py-3 transition hover:border-zinc-700">
      <FileCode2 className="mt-0.5 size-4 shrink-0 text-cyan-400" />
      <div className="min-w-0">
        <p className="truncate font-mono text-xs font-medium text-zinc-200">
          {source.file_path}
        </p>
        <p className="mt-0.5 text-[11px] text-zinc-500">
          Lines {source.start_line}–{source.end_line}
        </p>
      </div>
    </div>
  );
}

export function AskCodeLensContent({
  repositories,
}: {
  repositories: RepositoryResponse[];
}) {
  const [selectedRepoId, setSelectedRepoId] = useState<number | null>(
    repositories.length === 1 ? repositories[0].id : null,
  );
  const [question, setQuestion] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [repoDropdownOpen, setRepoDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // If repos change and only one exists, auto-select it
  useEffect(() => {
    if (repositories.length === 1) {
      setSelectedRepoId(repositories[0].id);
    }
  }, [repositories]);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setRepoDropdownOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const selectedRepo = repositories.find((r) => r.id === selectedRepoId);

  const canSubmit =
    selectedRepoId !== null &&
    question.trim().length > 0 &&
    question.length <= MAX_QUESTION_LENGTH &&
    !isLoading;

  const handleSubmit = async () => {
    if (!canSubmit || !selectedRepoId) return;
    setIsLoading(true);
    setResult(null);
    setErrorMsg(null);
    try {
      const res = await api.askRepository(selectedRepoId, {
        question: question.trim(),
      });
      setResult(res);
    } catch (err) {
      const friendly = getFriendlyErrorMessage(
        err,
        "Ask CodeLens encountered an unexpected error. Please try again.",
      );
      setErrorMsg(friendly.message);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleSuggestion = (q: string) => {
    setQuestion(q);
    setResult(null);
    setErrorMsg(null);
    textareaRef.current?.focus();
  };

  const isInsufficientContext =
    result !== null &&
    result.sources.length === 0 &&
    result.answer
      .toLowerCase()
      .includes("couldn't find enough relevant code");

  return (
    <div className="flex flex-col gap-8 pr-page cl-fade-up">
      <PageHeading
        eyebrow="Workspace / Ask CodeLens"
        title="Ask CodeLens"
        description="Ask questions about your repository code. CodeLens searches your ingested source files and generates grounded answers from the actual code."
      />

      {/* Repository selector */}
      <div className="flex flex-col gap-2 max-w-xl">
        <label className="text-xs font-medium text-zinc-400">
          Repository
        </label>
        {repositories.length === 0 ? (
          <div className="flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/50 px-3.5 py-2.5 text-sm text-zinc-500">
            <AlertCircle className="size-4 shrink-0 text-amber-400" />
            No repositories connected. Add a repository first.
          </div>
        ) : (
          <div className="relative" ref={dropdownRef}>
            <button
              type="button"
              onClick={() => setRepoDropdownOpen((o) => !o)}
              className="flex w-full items-center justify-between gap-3 rounded-lg border border-zinc-800 bg-zinc-900/50 px-3.5 py-2.5 text-left text-sm text-zinc-200 transition hover:border-zinc-700 hover:bg-zinc-900 focus:outline-none focus:ring-2 focus:ring-cyan-400/30"
            >
              <span className="truncate">
                {selectedRepo ? selectedRepo.full_name : "Select a repository…"}
              </span>
              <ChevronDown
                className={`size-4 shrink-0 text-zinc-500 transition-transform ${repoDropdownOpen ? "rotate-180" : ""}`}
              />
            </button>
            {repoDropdownOpen && (
              <div className="absolute z-20 mt-1 w-full overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900 shadow-xl cl-fade-up">
                {repositories.map((repo) => (
                  <button
                    key={repo.id}
                    type="button"
                    onClick={() => {
                      setSelectedRepoId(repo.id);
                      setRepoDropdownOpen(false);
                      setResult(null);
                      setErrorMsg(null);
                    }}
                    className={`flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-sm transition ${
                      repo.id === selectedRepoId
                        ? "bg-zinc-800 text-zinc-100"
                        : "text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-200"
                    }`}
                  >
                    <span className="truncate">{repo.full_name}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Question input */}
      <div className="flex flex-col gap-3 max-w-2xl">
        <label
          htmlFor="ask-question"
          className="text-xs font-medium text-zinc-400"
        >
          Question
        </label>
        <div className="cl-card p-0 overflow-hidden focus-within:ring-2 focus-within:ring-cyan-400/30 transition">
          <textarea
            ref={textareaRef}
            id="ask-question"
            value={question}
            onChange={(e) => {
              setQuestion(e.target.value);
              if (result) setResult(null);
              if (errorMsg) setErrorMsg(null);
            }}
            onKeyDown={handleKeyDown}
            placeholder="Ask anything about your codebase…"
            maxLength={MAX_QUESTION_LENGTH}
            rows={4}
            disabled={isLoading || repositories.length === 0}
            className="w-full resize-none bg-transparent px-4 py-3.5 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none disabled:opacity-50"
          />
          <div className="flex items-center justify-between border-t border-zinc-800/80 px-4 py-2.5">
            <span
              className={`text-[11px] tabular-nums transition ${
                question.length > MAX_QUESTION_LENGTH * 0.9
                  ? "text-amber-400"
                  : "text-zinc-600"
              }`}
            >
              {question.length}/{MAX_QUESTION_LENGTH}
            </span>
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!canSubmit}
              className="flex items-center gap-2 rounded-md bg-cyan-400 px-3.5 py-1.5 text-xs font-semibold text-black transition hover:bg-cyan-300 disabled:cursor-not-allowed disabled:opacity-40 motion-reduce:transition-none"
            >
              {isLoading ? (
                <>
                  <Loader2 className="size-3.5 animate-spin" />
                  Thinking…
                </>
              ) : (
                <>
                  <Send className="size-3.5" />
                  Ask CodeLens
                </>
              )}
            </button>
          </div>
        </div>
        <p className="text-[11px] text-zinc-600">
          Press{" "}
          <kbd className="rounded border border-zinc-700 bg-zinc-800 px-1 py-0.5 font-mono text-[10px] text-zinc-400">
            ⌘ Enter
          </kbd>{" "}
          or{" "}
          <kbd className="rounded border border-zinc-700 bg-zinc-800 px-1 py-0.5 font-mono text-[10px] text-zinc-400">
            Ctrl Enter
          </kbd>{" "}
          to submit
        </p>
      </div>

      {/* Suggested questions */}
      {!result && !isLoading && !errorMsg && (
        <div className="flex flex-col gap-3 max-w-2xl">
          <p className="text-xs font-medium text-zinc-500">
            Suggested questions
          </p>
          <div className="flex flex-wrap gap-2">
            {SUGGESTED_QUESTIONS.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => handleSuggestion(q)}
                disabled={repositories.length === 0}
                className="inline-flex items-center gap-1.5 rounded-full border border-zinc-800 bg-zinc-900/50 px-3 py-1.5 text-xs text-zinc-400 transition hover:border-zinc-700 hover:bg-zinc-900 hover:text-zinc-200 disabled:cursor-not-allowed disabled:opacity-40 motion-reduce:transition-none"
              >
                <MessageSquare className="size-3 shrink-0" />
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Loading state */}
      {isLoading && (
        <div className="flex max-w-2xl items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/50 px-4 py-4 text-sm text-zinc-400 cl-fade-up">
          <Loader2 className="size-4 animate-spin text-cyan-400" />
          Searching your repository code and generating an answer…
        </div>
      )}

      {/* Error state */}
      {errorMsg && !isLoading && (
        <div className="flex max-w-2xl items-start gap-3 rounded-lg border border-red-500/20 bg-red-400/10 px-4 py-4 cl-fade-up">
          <AlertCircle className="mt-0.5 size-4 shrink-0 text-red-400" />
          <div>
            <p className="text-sm font-medium text-red-300">Error</p>
            <p className="mt-0.5 text-sm text-red-400/80">{errorMsg}</p>
          </div>
        </div>
      )}

      {/* Answer */}
      {result && !isLoading && (
        <div className="flex flex-col gap-6 max-w-2xl cl-fade-up">
          {/* Answer card */}
          <div className="cl-card flex flex-col gap-4 p-5">
            <div className="flex items-center gap-2 border-b border-zinc-800/80 pb-3">
              <Sparkles className="size-4 text-cyan-400" />
              <span className="text-xs font-semibold text-zinc-300">
                Answer
              </span>
              {isInsufficientContext && (
                <span className="ml-auto rounded-full border border-amber-400/20 bg-amber-400/10 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                  Insufficient context
                </span>
              )}
            </div>
            <p className="whitespace-pre-wrap text-sm leading-7 text-zinc-200">
              {result.answer}
            </p>
          </div>

          {/* Sources */}
          {result.sources.length > 0 && (
            <div className="flex flex-col gap-3">
              <p className="text-xs font-medium text-zinc-500">
                Sources retrieved
              </p>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {result.sources.map((src, i) => (
                  <SourceCard key={`${src.file_path}-${i}`} source={src} />
                ))}
              </div>
            </div>
          )}

          {/* No sources note */}
          {result.sources.length === 0 && !isInsufficientContext && (
            <p className="text-xs text-zinc-600">
              No specific source files were cited for this answer.
            </p>
          )}
        </div>
      )}

      {/* Empty state — no query yet */}
      {!result && !isLoading && !errorMsg && question.trim() === "" && (
        <div className="flex max-w-2xl flex-col items-center gap-4 rounded-xl border border-dashed border-zinc-800 bg-zinc-900/20 px-8 py-12 text-center cl-fade-up">
          <div className="flex size-12 items-center justify-center rounded-xl bg-cyan-950 ring-1 ring-cyan-400/20">
            <MessageSquare className="size-6 text-cyan-400" />
          </div>
          <div>
            <p className="text-sm font-medium text-zinc-300">
              Ask anything about your code
            </p>
            <p className="mt-1.5 max-w-sm text-xs text-zinc-500">
              CodeLens searches your ingested source files and generates answers
              grounded in the actual repository code. Pick a repository, type
              your question, and press Ask CodeLens.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
