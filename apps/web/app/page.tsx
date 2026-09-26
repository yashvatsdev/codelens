import CodeLensDashboard from "@/components/codelens-dashboard";
import AuthGuard from "@/components/auth-guard";

export default function Home() {
  return (
    <AuthGuard>
      <CodeLensDashboard />
    </AuthGuard>
  );
}

