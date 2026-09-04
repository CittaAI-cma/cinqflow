"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import { submitLogin, type LoginState } from "./actions";

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <button type="submit" className="btn-accent auth-submit" disabled={pending}>
      {pending ? "Signing in…" : "Sign in"}
    </button>
  );
}

export default function LoginForm({ next }: { next?: string }) {
  const [state, action] = useActionState<LoginState, FormData>(submitLogin, {});

  return (
    <form action={action} className="auth-form" noValidate>
      <input type="hidden" name="next" value={next ?? ""} />

      <div className="field">
        <label className="field-label" htmlFor="email">
          Email
        </label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          required
          autoFocus
          placeholder="you@cinqcare.com"
        />
      </div>

      <div className="field">
        <label className="field-label" htmlFor="password">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          placeholder="••••••••"
        />
      </div>

      {state.error ? (
        <p className="field-error auth-error" role="alert">
          {state.error}
        </p>
      ) : null}

      <SubmitButton />

      <p className="auth-hint">
        No account? Ask an administrator to create one for you.
      </p>
    </form>
  );
}
