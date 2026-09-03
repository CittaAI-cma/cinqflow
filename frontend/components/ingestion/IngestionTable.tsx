"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import Avatar from "@/components/ui/Avatar";
import DataTable, { type Column } from "@/components/ui/DataTable";
import TableToolbar from "@/components/ui/TableToolbar";
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
import type { Upload } from "@/lib/api";
import { uploadStatusWord } from "@/lib/statusWords";

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

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (part: number) => String(part).padStart(2, "0");
  return (
    `${pad(date.getMonth() + 1)}/${pad(date.getDate())}/${date.getFullYear()} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  );
}

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
              title={`Open group ${row.feed}`}
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
        render: (row) => <span className="cell-plain">{formatTimestamp(row.created_ts)}</span>,
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
            <span
              className="icon-action danger disabled"
              aria-disabled="true"
              title="Delete — uploads are append-only on this build; there is no delete endpoint"
            >
              <TrashIcon size={16} />
            </span>
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
    </>
  );
}
