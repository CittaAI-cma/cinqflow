"use server";

import { redirect } from "next/navigation";
import { api } from "@/lib/api";
import { Refused } from "@/lib/api";
import type { Feed } from "@/lib/types";

/**
 * CF-V0-E3-01 — register a feed. Six fields, plus a real sample name the
 * pattern is validated against before anything is saved.
 *
 * Created as a DRAFT, always — `create_feed` gives the engine no way to save
 * a feed that is already executable. Nothing here decides whether the
 * pattern is good, whether the schedule is sane, or whether the feed is
 * READY: those are all server-side facts (`FeedRecord.__post_init__`,
 * `readiness_of`), read back and rendered, never re-derived in the browser.
 *
 * `next/navigation`'s `redirect()` throws by design, and MUST be called
 * outside any try/catch that could swallow it — see `deliverFile`'s own
 * comment on the exact same trap: a redirect caught by a handler written for
 * "the server refused" turns a feed that was actually created into a false
 * REFUSED on screen.
 */
type Landing = {
  outcome: "CREATED" | "REFUSED";
  headline: string;
  feed?: string;
};

export async function registerFeed(formData: FormData): Promise<void> {
  const result = await attemptRegistration(formData);
  const parameters = new URLSearchParams({ outcome: result.outcome, headline: result.headline });
  if (result.feed) parameters.set("feed", result.feed);
  redirect(`/data/intake/new?${parameters.toString()}`);
}

async function attemptRegistration(formData: FormData): Promise<Landing> {
  const string = (name: string) => String(formData.get(name) ?? "").trim();
  const number = (name: string) => {
    const raw = string(name);
    return raw ? Number(raw) : undefined;
  };

  const payload = {
    feed_id: string("feed_id"),
    domain: string("domain"),
    source_system: string("source_system"),
    file_format: string("file_format"),
    landing_path: string("landing_path"),
    file_pattern: string("file_pattern"),
    schedule_cron: string("schedule_cron"),
    sample_filename: string("sample_filename"),
    min_size_bytes: number("min_size_bytes"),
    max_size_bytes: number("max_size_bytes"),
  };

  try {
    const feed = await api<Feed>("/api/feeds", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return {
      outcome: "CREATED",
      headline: `${feed.feed_id} is saved as a Draft, version ${feed.version}.`,
      feed: feed.feed_id,
    };
  } catch (error) {
    // A refusal is a DECISION the server made — a pattern that does not
    // match the sample, a required field it rejected, a role that may not
    // create feeds. It is rendered. Anything else is a transport failure
    // that reached nobody, and it is re-thrown to the error boundary rather
    // than dressed up as a decision the platform did not make.
    if (!(error instanceof Refused)) throw error;
    return { outcome: "REFUSED", headline: error.detail };
  }
}
