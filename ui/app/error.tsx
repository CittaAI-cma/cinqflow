"use client";

/**
 * A page-segment crash — a network failure reaching the BFF, most often.
 *
 * This covers everything BELOW the root layout. The root layout's own fetches
 * (`/api/me`, `/api/navigation`) are covered separately by `global-error.tsx`,
 * because a Next.js `error.tsx` never catches an error thrown by its own
 * parent layout.
 */
export default function PageError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="refusal">
      <strong>This page could not load</strong>
      <div className="note">
        The platform could not be reached. Nothing here changed on the server — this is a
        transport failure, not a decision.
      </div>
      <div className="note mono">{error.message || "unknown error"}</div>
      <p>
        <button type="button" className="cited" onClick={() => reset()}>
          Try again
        </button>
      </p>
    </div>
  );
}
