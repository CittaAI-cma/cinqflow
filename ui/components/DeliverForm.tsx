"use client";

import { useMemo, useRef, useState } from "react";
import { useFormStatus } from "react-dom";

/**
 * CF-V1-E3-05 — the upload form, on both doors into the delivery route.
 *
 * The only client component in the intake stack, and it earns that for three
 * things a server render cannot do: drop a file onto the page, say the chosen
 * file's name back to the person, and read the feed's pattern against it
 * before they commit.
 *
 * THE PATTERN CHECK IS ADVICE, NEVER A GATE. It never disables the button and
 * never blocks the post. A browser that refused a file the platform had not
 * seen would be a second door — the one ADR-0011 exists to prevent — except
 * worse, because a file refused here leaves no registry row, no parked copy
 * and no reason anybody can read afterwards. The platform's answer to a file
 * it did not expect is to land it, register it, park it and NAME THE CHECK.
 * The most this can honestly do is tell you what that answer will be.
 */

export interface DeliverableFeed {
  feed_id: string;
  file_format: string;
  file_pattern: string;
  landing_path: string;
}

export function DeliverForm({
  action,
  feeds,
  selected,
  locked = false,
  returnTo,
  today,
  mayDeliver,
}: {
  action: (formData: FormData) => void | Promise<void>;
  feeds: readonly DeliverableFeed[];
  selected: string;
  /** True on a feed's own page, where the feed is the address, not a choice. */
  locked?: boolean;
  returnTo: string;
  /** Supplied by the server so the default date does not depend on the clock
   *  of whichever machine rendered it. */
  today: string;
  mayDeliver: boolean;
}) {
  const [feedId, setFeedId] = useState(selected);
  const [picked, setPicked] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const feed = feeds.find((candidate) => candidate.feed_id === feedId) ?? null;
  const verdict = useMemo(() => nameAgainstPattern(picked?.name, feed), [picked, feed]);

  function take(files: FileList | null) {
    setPicked(files && files.length > 0 ? files[0] : null);
  }

  return (
    <form action={action}>
      <input type="hidden" name="return_to" value={returnTo} />
      {locked ? <input type="hidden" name="feed_id" value={feedId} /> : null}

      {locked ? null : (
        <p className="field">
          <label htmlFor="feed_id">Which feed is this file for?</label>
          <select
            id="feed_id"
            name="feed_id"
            required
            value={feedId}
            onChange={(event) => setFeedId(event.target.value)}
          >
            <option value="">Choose a feed…</option>
            {feeds.map((candidate) => (
              <option key={candidate.feed_id} value={candidate.feed_id}>
                {candidate.feed_id} · {candidate.file_format}
              </option>
            ))}
          </select>
          <span className="note">
            The feed decides where the file lands and which pattern it is matched against. A file
            sent to the wrong feed is not lost — it is parked under that feed with the reason.
          </span>
        </p>
      )}

      <div
        className="dropzone"
        data-dragging={dragging}
        data-picked={picked !== null}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          const dropped = event.dataTransfer.files;
          if (dropped.length === 0 || input.current === null) return;
          // Assigning a FileList is the only way to make a dropped file the
          // input's value, so the form posts exactly what a click would.
          const transfer = new DataTransfer();
          transfer.items.add(dropped[0]);
          input.current.files = transfer.files;
          take(transfer.files);
        }}
      >
        <label htmlFor="file">The file the payer sent</label>
        <input
          id="file"
          name="file"
          type="file"
          ref={input}
          required
          onChange={(event) => take(event.target.files)}
        />
        <span className="note">or drop it here</span>
      </div>

      {picked ? (
        <p className="verdict" data-verdict={verdict.kind}>
          <span className="mono">{picked.name}</span> · {kilobytes(picked.size)}
          <br />
          <span className="note">{verdict.says}</span>
        </p>
      ) : null}

      <p className="field">
        <label htmlFor="business_date">Business date</label>
        <input
          id="business_date"
          name="business_date"
          type="date"
          required
          defaultValue={today}
        />
        <span className="note">
          The period the data is ABOUT, not today. A roster for August delivered in September is
          August&rsquo;s, and only you know that.
        </span>
      </p>

      <details className="field">
        <summary>What the sender declared (optional)</summary>
        <p className="field">
          <label htmlFor="checksum">Checksum</label>
          <input id="checksum" name="checksum" type="text" placeholder="sha256-…" />
          <span className="note">
            Supplied, it is checked BEFORE anything is written. A transfer that arrived damaged is
            the one thing refused outright rather than landed and rejected — the storage pin has no
            delete verb, so damaged bytes could not be taken back out.
          </span>
        </p>
        <p className="field">
          <label htmlFor="declared_row_count">Row count</label>
          <input id="declared_row_count" name="declared_row_count" type="number" min={0} />
          <span className="note">
            What the covering note or trailer said. Recorded with the delivery, so a file that
            parses cleanly at half its promised size is answerable later.
          </span>
        </p>
      </details>

      <Submit mayDeliver={mayDeliver} />
    </form>
  );
}

/**
 * A file upload has latency a person can feel, and a form that looks idle
 * while it works gets clicked again — which at this door means a second
 * delivery, correctly SKIPPED by fingerprint and confusingly reported.
 */
function Submit({ mayDeliver }: { mayDeliver: boolean }) {
  const { pending } = useFormStatus();
  return (
    <p className="inline">
      <button className="primary" type="submit" disabled={pending || !mayDeliver}>
        {pending ? "Delivering…" : "Deliver"}
      </button>
      {mayDeliver ? null : (
        <span className="note">
          Delivering is <span className="mono">edit_feed</span>. Your role can read this screen but
          not put content into the estate.
        </span>
      )}
    </p>
  );
}

type Verdict = { kind: "match" | "mismatch" | "unknown"; says: string };

/**
 * Python's `re.fullmatch` is what `core.landing` uses, so the anchors are
 * added here rather than trusting every registered pattern to carry its own.
 * A pattern JavaScript cannot compile says so instead of guessing — silently
 * reporting "does not match" for a pattern this could not read would send
 * somebody to rename a file that was already correct.
 */
function nameAgainstPattern(name: string | undefined, feed: DeliverableFeed | null): Verdict {
  if (!name || feed === null) {
    return { kind: "unknown", says: "Choose a feed to see whether the name matches its pattern." };
  }
  let pattern: RegExp;
  try {
    pattern = new RegExp(`^(?:${feed.file_pattern})$`);
  } catch {
    return {
      kind: "unknown",
      says: `This browser cannot read the pattern ${feed.file_pattern} — the platform will match it on the server, as it always does.`,
    };
  }
  if (pattern.test(name)) {
    return {
      kind: "match",
      says: `Matches ${feed.feed_id}. It will land in ${feed.landing_path}/incoming/ and be profiled.`,
    };
  }
  return {
    kind: "mismatch",
    says: `Does not match ${feed.file_pattern}. Deliver it anyway if you mean to — it will be registered and parked with the reason, not discarded.`,
  };
}

function kilobytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
