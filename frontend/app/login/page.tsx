import { redirect } from "next/navigation";
import { BrandMark } from "@/components/icons";
import { BRAND_NAME } from "@/lib/appConfig";
import { getCurrentUser } from "@/lib/auth";
import LoginForm from "./LoginForm";

export const metadata = { title: `Sign in — ${BRAND_NAME}` };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  // Already signed in: nothing left for this page to do.
  const user = await getCurrentUser();
  if (user) redirect("/");

  const { next } = await searchParams;

  return (
    <div className="auth-page">
      <div className="auth-card card">
        <div className="auth-brand">
          <BrandMark size={28} />
          <span>{BRAND_NAME}</span>
        </div>
        <h1 className="auth-title">Sign in</h1>
        <p className="auth-subtitle">
          Data governance, pipelines and operations for the CINQFLOW platform.
        </p>
        <LoginForm next={next} />
      </div>
    </div>
  );
}
