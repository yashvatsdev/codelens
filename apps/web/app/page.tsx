"use client";

import { useEffect, useState } from "react";

type BackendStatus = {
  status: string;
  service: string;
};

export default function Home() {
  const [backend, setBackend] = useState<BackendStatus | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetch("http://127.0.0.1:8000/health")
      .then((response) => {
        if (!response.ok) {
          throw new Error("Backend request failed");
        }

        return response.json();
      })
      .then((data) => {
        setBackend(data);
      })
      .catch(() => {
        setError(true);
      });
  }, []);

  return (
    <main className="min-h-screen bg-black text-white flex items-center justify-center">
      <div className="text-center">
        <h1 className="text-5xl font-bold">CodeLens</h1>

        <p className="mt-4 text-gray-400">
          AI-Powered Code Intelligence Platform
        </p>

        <div className="mt-8 rounded-xl border border-gray-800 bg-gray-950 p-6">
          <p className="text-sm text-gray-400">Backend Status</p>

          {backend && (
            <p className="mt-2 text-xl font-semibold text-green-400">
              ● Connected
            </p>
          )}

          {error && (
            <p className="mt-2 text-xl font-semibold text-red-400">
              ● Disconnected
            </p>
          )}

          {!backend && !error && (
            <p className="mt-2 text-xl font-semibold text-yellow-400">
              ● Connecting...
            </p>
          )}
        </div>
      </div>
    </main>
  );
}
