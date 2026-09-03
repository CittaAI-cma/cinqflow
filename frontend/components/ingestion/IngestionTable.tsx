"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import Avatar from "@/components/ui/Avatar";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import DataTable, { type Column } from "@/components/ui/DataTable";
import TableToolbar from "@/components/ui/TableToolbar";
import Timestamp from "@/components/ui/Timestamp";
import StatusWord from "@/components/StatusWord";
import {
  ArrowRight,
  DatabaseIcon,
  DownloadIcon,
  EditIcon,
  PackageIcon,
  PlusIcon,
  RefreshIcon,
  TrashIcon,
} from "@/components/icons";
import { deleteUpload, type Upload } from "@/lib/api";
import { uploadStatusWord } from "@/lib/statusWords";
import { useToast } from "@/lib/useToast";

const CSV_HEADERS = [
  "group_name",
  "feed",
  "source_system",
  "environment",
  "business_date",
  "stage",
  "created_by",
  "created_ts",
] as const;

/* Timestamps render through `<Timestamp>` (UTC on the server, local after
   mount). Formatting them inline with `getHours()` here used to hydrate
   differently from the server's UTC container and threw the table's subtree
   away on every load — taking the search box and page state with it. */

export default function IngestionTable({
  uploads,
  environment,
}: {
  uploads: Upload[];
  environment: string;
}) {
  const [query, setQuery] = useState("");
  const router = useRouter();
  const [refreshing, startRefresh] = useTransition();
  const { push } = useToast();
  const [pendingDelete, setPendingDelete] = useState<Upload | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    const { result, error } = await deleteUpload(pendingDelete.upload_id);
    setDeleting(false);
    setPendingDelete(null);
    if (error) {
      push(error, "error");
      return;
    }
    const preserved = result?.preserved_batches.length
      ? ` Bronze for ${result.preserved_batches.length} batch(es) is preserved — append-only by design.`
      : "";
    push(`Deleted "${pendingDelete.filename}". Its fingerprint is free for re-upload.${preserved}`, "success");
    startRefresh(() => router.refresh());
  }

  /** Searching covers the columns on screen and the ones behind them — feed and
   *  source system are in the group glyph's title, not their own column. */
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return uploads;
    return uploads.filter((upload) =>
      [
        upload.filename,
        upload.feed,
        upload.source_system,
        upload.domain,
        upload.uploader,
        upload.business_date,
        uploadStatusWord(upload.status) ?? upload.status,
        environment,
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [uploads, query, environment]);

  const columns: Column<Upload>[] = useMemo(
    () => [
      {
        key: "group",
        header: "Group name",
        // The one column that absorbs slack. Everything else is a bounded
        // value (a date, an email, a status word); the filename is unbounded,
        // so it is the one that truncates rather than the one that pushes
        // Stage and Actions off the right edge.
        width: "34%",
        sortable: true,
        value: (row) => row.filename.toLowerCase(),
        render: (row) => (
          <div className="group-cell">
            <span
              className="group-glyphs"
              title={`${row.source_system} → ${row.feed} (${row.domain})`}
            >
              <span className="glyph source">
                <DatabaseIcon size={13} />
              </span>
              <ArrowRight size={12} className="glyph-arrow" />
              <span className="glyph target">
                <PackageIcon size={13} />
              </span>
            </span>
            {/* The column names a group, so it opens the group; the row's
                edit action opens this specific object. */}
            <Link
              href={`/data/intake/${encodeURIComponent(row.feed)}`}
              className="group-name"
              // Carries the full name too: the cell truncates long filenames
              // so the rest of the columns stay on screen.
              title={`${row.filename} — open group ${row.feed}`}
            >
              {row.filename}
            </Link>
          </div>
        ),
      },
      {
        key: "environment",
        header: "Environment",
        sortable: true,
        value: () => environment,
        render: () => <span className="cell-plain">{environment}</span>,
      },
      {
        key: "updated",
        header: "Last updated",
        sortable: true,
        value: (row) => Date.parse(row.created_ts) || 0,
        render: (row) => (
          <span className="cell-plain">
            <Timestamp value={row.created_ts} />
          </span>
        ),
      },
      {
        key: "createdBy",
        header: "Created by",
        sortable: true,
        value: (row) => row.uploader.toLowerCase(),
        render: (row) => (
          <span className="person-cell">
            <Avatar name={row.uploader} size={22} />
            <span className="cell-plain">{row.uploader}</span>
          </span>
        ),
      },
      {
        key: "stage",
        header: "Stage",
        sortable: true,
        value: (row) => uploadStatusWord(row.status) ?? row.status,
        render: (row) => (
          <span className="stage-pill">
            <StatusWord word={uploadStatusWord(row.status)} raw={row.status} />
          </span>
        ),
      },
      {
        key: "actions",
        header: "Actions",
        width: "110px",
        render: (row) => (
          <div className="row-actions">
            <Link
              href={`/uploads/${row.upload_id}`}
              className="icon-action"
              title="Open this ingestion"
              aria-label={`Open ${row.filename}`}
            >
              <EditIcon size={16} />
            </Link>
            <button
              type="button"
              className="icon-action danger"
              title={`Delete "${row.filename}"`}
              aria-label={`Delete ${row.filename}`}
              onClick={() => setPendingDelete(row)}
            >
              <TrashIcon size={16} />
            </button>
          </div>
        ),
      },
    ],
    [environment],
  );

  function exportCsv() {
    const rows = filtered.map((upload) => [
      upload.filename,
      upload.feed,
      upload.source_system,
      environment,
      upload.business_date,
      uploadStatusWord(upload.status) ?? upload.status,
      upload.uploader,
      upload.created_ts,
    ]);
    const escape = (cell: string) => `"${String(cell).replace(/"/g, '""')}"`;
    const csv = [CSV_HEADERS, ...rows].map((line) => line.map(escape).join(",")).join("\n");

    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `ingestion-${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <>
      <TableToolbar query={query} onQueryChange={setQuery}>
        <button
          type="button"
          className="btn-outline btn-square"
          onClick={() => startRefresh(() => router.refresh())}
          disabled={refreshing}
          title="Reload from the control plane"
          aria-label="Refresh"
        >
          <RefreshIcon size={16} className={refreshing ? "spin" : undefined} />
        </button>
        <button
          type="button"
          className="btn-outline"
          onClick={exportCsv}
          disabled={filtered.length === 0}
          title="Download the listed rows as CSV"
        >
          <DownloadIcon size={15} /> All ({filtered.length})
        </button>
        <Link href="/data/intake/new" className="btn-dark">
          <PlusIcon size={15} /> Add Ingestion
        </Link>
      </TableToolbar>

      <DataTable
        rows={filtered}
        columns={columns}
        rowKey={(row) => row.upload_id}
        initialSort={{ key: "updated", dir: "desc" }}
        emptyMessage={
          query ? `No ingestion matches “${query}”.` : "No ingestion yet. Add one to begin."
        }
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete this ingestion?"
        tone="danger"
        confirmLabel="Delete"
        busy={deleting}
        consequence={
          <>
            This removes <span className="mono">{pendingDelete?.filename}</span> and everything
            recorded against it — profile, interpretation, approvals, and any mapping draft — and
            frees its fingerprint so the same file can be uploaded again. If it already landed to
            Bronze, those rows are preserved: Bronze is append-only by design and this cannot
            remove them, only the reference to them.
          </>
        }
        requireTyped="DELETE"
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </>
  );
}
