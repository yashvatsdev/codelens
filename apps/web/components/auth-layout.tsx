import React from "react";

export function AuthLayout({
  children,
  title,
  subtitle,
}: {
  children: React.ReactNode;
  title: string;
  subtitle?: string;
}) {
  return (
    <div className="min-h-screen bg-[#090a0b] flex items-center justify-center px-4">
      <div className="w-full max-w-md cl-fade-up">
        {/* Logo / branding */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center gap-2 mb-2">
            <svg
              className="h-8 w-8 text-cyan-300"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h18" />
            </svg>
            <span className="text-2xl font-bold text-zinc-100 tracking-tight">
              CodeLens
            </span>
          </div>
          <p className="text-sm text-zinc-400">AI-Powered Code Intelligence</p>
        </div>

        {/* Card */}
        <div className="cl-card p-8">
          <h1 className="text-xl font-semibold text-zinc-100 mb-6">{title}</h1>
          {subtitle && <p className="text-sm text-zinc-400 mb-6">{subtitle}</p>}
          {children}
        </div>
      </div>
    </div>
  );
}
