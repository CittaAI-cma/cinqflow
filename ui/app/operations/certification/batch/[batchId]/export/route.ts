import { NextResponse } from "next/server";
import { rawGet } from "@/lib/api";

/**
 * CF-V2-E13-04 — the evidence document, handed to the browser as a download.
 *
 * A Route Handler rather than a plain `<a href>` to the BFF: the bearer token
 * lives in an HTTP-only cookie on THIS origin (`lib/api.ts`'s `token()`), and
 * the browser holds no credential the BFF, on its own port, would accept.
 * This is the one door a download can go through without handing the token
 * to the page itself.
 *
 * Plain text, on purpose, both here and at the source: byte-comparable, so
 * "identical to the day it was certified" is a string equality a payer can
 * run for themselves.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ batchId: string }> },
) {
  const { batchId } = await params;
  const upstream = await rawGet(
    `/api/operations/batches/${encodeURIComponent(batchId)}/certification/export`,
  );
  if (upstream.status !== 200) {
    return new NextResponse(upstream.text || "That did not work.", { status: upstream.status });
  }
  return new NextResponse(upstream.text, {
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "content-disposition": `attachment; filename="certification-${batchId}.txt"`,
    },
  });
}
