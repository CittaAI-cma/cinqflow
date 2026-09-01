"use client";

import { useMemo, useState } from "react";
import { useFormStatus } from "react-dom";

/**
 * CF-V0-E3-01 — the registration form. Six fields the engine reads, plus a
 * real sample name the pattern is checked against.
 *
 * THE LIVE PATTERN CHECK IS ADVICE, NEVER A GATE — the same rule
 * `DeliverForm`'s own check follows, and for the same reason: the platform's
 * refusal (`PatternSampleMismatchError`, a side-by-side diff) is the honest
 * one, computed the same way `core.landing` computes it for a real arriving
 * file. Disabling submit on a browser's guess would be a SECOND judge of a
 * question only the server is allowed to answer.
 */

const FORMATS = ["csv", "txt", "tsv", "psv", "xlsx", "xls", "ods"] as const;

export function RegisterFeedForm({
  action,
  mayRegister,
}: {
  action: (formData: FormData) => void | Promise<void>;
  mayRegister: boolean;
}) {
  const [pattern, setPattern] = useState("");
  const [sample, setSample] = useState("");
  const verdict = useMemo(() => patternAgainstSample(pattern, sample), [pattern, sample]);

  return (
    <form action={action}>
      <p className="field">
        <label htmlFor="feed_id">Feed id</label>
        <input
          id="feed_id"
          name="feed_id"
          type="text"
          required
          pattern="[a-z0-9][a-z0-9-]*"
          placeholder="fidelis-upstate-roster"
          className="mono"
        />
        <span className="note">
          Lowercase, hyphenated, permanent — this is the address every citation, batch and log
          line names the feed by.
        </span>
      </p>

      <p className="field">
        <label htmlFor="domain">Domain</label>
        <input id="domain" name="domain" type="text" required placeholder="membership" />
        <span className="note">What kind of data this is — enrollment, claims, ADT.</span>
      </p>

      <p className="field">
        <label htmlFor="source_system">Source system</label>
        <input id="source_system" name="source_system" type="text" required placeholder="fidelis" />
        <span className="note">Who sends it — the payer or upstream system.</span>
      </p>

      <p className="field">
        <label htmlFor="file_format">File format</label>
        <select id="file_format" name="file_format" required defaultValue="">
          <option value="" disabled>
            Choose a format…
          </option>
          {FORMATS.map((format) => (
            <option key={format} value={format}>
              {format}
            </option>
          ))}
        </select>
        <span className="note">The profiler reads exactly these seven shapes today.</span>
      </p>

      <p className="field">
        <label htmlFor="landing_path">Landing path</label>
        <input
          id="landing_path"
          name="landing_path"
          type="text"
          required
          placeholder="landing/fidelis/upstate-roster"
          className="mono"
        />
        <span className="note">Where an accepted file is filed, under the zone the profile names.</span>
      </p>

      <p className="field">
        <label htmlFor="schedule_cron">Schedule</label>
        <input
          id="schedule_cron"
          name="schedule_cron"
          type="text"
          required
          placeholder="0 6 * * 1"
          className="mono"
        />
        <span className="note">A five-field cron expression: when this feed is due.</span>
      </p>

      <p className="field">
        <label htmlFor="file_pattern">File-name pattern</label>
        <input
          id="file_pattern"
          name="file_pattern"
          type="text"
          required
          placeholder="^deidentified_CINQUPSTATE_Member_Roster_\d{2}_\d{2}_\d{4}_\d+\.csv$"
          className="mono"
          value={pattern}
          onChange={(event) => setPattern(event.target.value)}
        />
        <span className="note">
          A regular expression, matched whole against every arriving filename. Incident #1 was a
          leading underscore nobody could see here — check the sample below rather than trusting
          the pattern by eye.
        </span>
      </p>

      <p className="field">
        <label htmlFor="sample_filename">A real sample filename</label>
        <input
          id="sample_filename"
          name="sample_filename"
          type="text"
          required
          placeholder="deidentified_CINQUPSTATE_Member_Roster_03_05_2026_1.csv"
          className="mono"
          value={sample}
          onChange={(event) => setSample(event.target.value)}
        />
        <span className="note">
          One name the payer actually sent. The pattern above is checked against it before
          anything is saved — a pattern that matches nothing is indistinguishable from a feed that
          never arrives.
        </span>
      </p>

      {pattern || sample ? (
        <p className="verdict" data-verdict={verdict.kind}>
          <span className="note">{verdict.says}</span>
        </p>
      ) : null}

      <details className="field">
        <summary>Size bounds (optional)</summary>
        <p className="field">
          <label htmlFor="min_size_bytes">Minimum bytes</label>
          <input id="min_size_bytes" name="min_size_bytes" type="number" min={0} />
        </p>
        <p className="field">
          <label htmlFor="max_size_bytes">Maximum bytes</label>
          <input id="max_size_bytes" name="max_size_bytes" type="number" min={0} />
          <span className="note">
            A named pre-flight check, not a guess — a file far outside these bounds is REJECTED
            with the reason, rather than loaded and discovered wrong three stages later.
          </span>
        </p>
      </details>

      <Submit mayRegister={mayRegister} />
    </form>
  );
}

function Submit({ mayRegister }: { mayRegister: boolean }) {
  const { pending } = useFormStatus();
  return (
    <p className="inline">
      <button className="primary" type="submit" disabled={pending || !mayRegister}>
        {pending ? "Registering…" : "Register feed"}
      </button>
      {mayRegister ? null : (
        <span className="note">
          Registering is <span className="mono">create_feed</span>. Your role can read this screen
          but not add to the registry.
        </span>
      )}
    </p>
  );
}

type Verdict = { kind: "match" | "mismatch" | "unknown"; says: string };

function patternAgainstSample(pattern: string, sample: string): Verdict {
  if (!pattern || !sample) {
    return {
      kind: "unknown",
      says: "Fill in both the pattern and a sample filename to see whether they agree.",
    };
  }
  let compiled: RegExp;
  try {
    compiled = new RegExp(`^(?:${pattern})$`);
  } catch {
    return {
      kind: "unknown",
      says: `This browser cannot read that pattern — the platform will try to compile it on save, as it always does, and refuse it there if it cannot.`,
    };
  }
  if (compiled.test(sample)) {
    return { kind: "match", says: "Matches. This is what the server will find too." };
  }
  return {
    kind: "mismatch",
    says:
      "Does not match — saving this pair will be refused, with a side-by-side diff, before " +
      "anything is written. Fix the pattern or the sample before submitting.",
  };
}
