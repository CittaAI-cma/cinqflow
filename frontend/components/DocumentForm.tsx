"use client";

import { useRef, useState } from "react";
import { useFormStatus } from "react-dom";

/**
 * CF-V1-E16-06 — upload the companion guide alongside the sample, at the
 * SAME wizard step 1. Deliberately spare next to `DeliverForm`: there is no
 * pattern to check a spec against and no business date a spec is "about" —
 * only a file and, optionally, which domain it documents.
 */
export function DocumentForm({
  action,
  feedId,
  mayUpload,
}: {
  action: (formData: FormData) => void | Promise<void>;
  feedId: string;
  mayUpload: boolean;
}) {
  const [picked, setPicked] = useState<File | null>(null);
  const input = useRef<HTMLInputElement>(null);

  return (
    <form action={action}>
      <input type="hidden" name="feed_id" value={feedId} />
      <p className="field">
        <label htmlFor="doc-file">The payer&rsquo;s companion guide or spec (optional)</label>
        <input
          id="doc-file"
          name="file"
          type="file"
          accept=".pdf,.docx,.md,.txt,.csv"
          ref={input}
          onChange={(event) => setPicked(event.target.files?.[0] ?? null)}
        />
        <span className="note">
          PDF, Word, Markdown, plain text or CSV. Parsed with page anchors kept, so a suggestion
          downstream can cite &ldquo;guide p.14&rdquo; and open straight to it.
        </span>
      </p>

      {picked ? (
        <p className="verdict" data-verdict="match">
          <span className="mono">{picked.name}</span> · {kilobytes(picked.size)}
        </p>
      ) : null}

      <p className="field">
        <label htmlFor="doc-domain">Domain this document describes (optional)</label>
        <input id="doc-domain" name="domain" type="text" placeholder="enrollment, claims…" />
      </p>

      <Submit mayUpload={mayUpload} picked={picked !== null} />
    </form>
  );
}

function Submit({ mayUpload, picked }: { mayUpload: boolean; picked: boolean }) {
  const { pending } = useFormStatus();
  return (
    <p className="inline">
      <button type="submit" disabled={pending || !mayUpload || !picked}>
        {pending ? "Uploading…" : "Upload document"}
      </button>
      {mayUpload ? null : (
        <span className="note">
          Uploading is <span className="mono">edit_feed</span>. Your role can read this screen
          but not add to what grounds it.
        </span>
      )}
    </p>
  );
}

function kilobytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
