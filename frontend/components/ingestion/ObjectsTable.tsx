"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import Checkbox from "@/components/ui/Checkbox";
import DataTable, { type Column } from "@/components/ui/DataTable";
import Pagination from "@/components/ui/Pagination";
import RowMenu from "@/components/ui/RowMenu";
import TableToolbar from "@/components/ui/TableToolbar";
import {
  DownloadIcon,
  PlusIcon,
  RefreshIcon,
  SettingsIcon,
  SitemapIcon,
} from "@/components/icons";
import type { Upload } from "@/lib/api";

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}/${pad(date.getDate())}/${date.getFullYear()}`;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(2)} KB`;
  return `${(kb / 1024).toFixed(2)} MB`;
}

/** The objects belonging to one ingest group. Selection drives the export, so
 *  the checkboxes do something rather than just sitting there. */
export default function ObjectsTable({
  group,
  objects,
}: {
  group: string;
  objects: Upload[];
}) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const router = useRouter();
  const [refreshing, startRefresh] = useTransition();

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return objects;
    return objects.filter((object) =>
      [object.filename, object.landing_key, object.uploader, object.business_date]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [objects, query]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, pageCount);
  const visible = filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize);

  const allVisibleSelected =
    visible.length > 0 && visible.every((object) => selected.has(object.upload_id));

  function toggleAllVisible(checked: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      for (const object of visible) {
        if (checked) next.add(object.upload_id);
        else next.delete(object.upload_id);
      }
      return next;
    });
  }

  function toggleOne(id: string, checked: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  /** Exports the selection when there is one, otherwise everything listed. */
  const exportRows = selected.size
    ? filtered.filter((object) => selected.has(object.upload_id))
    : filtered;

  function exportCsv() {
    const header = ["name", "object_path", "size_bytes", "updated", "uploader", "business_date"];
    const rows = exportRows.map((object) => [
      object.filename,
      object.landing_key,
      String(object.size_bytes),
      object.created_ts,
      object.uploader,
      object.business_date,
    ]);
    const escape = (cell: string) => `"${cell.replace(/"/g, '""')}"`;
    const csv = [header, ...rows].map((line) => line.map(escape).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${group}-objects.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  const columns: Column<Upload>[] = useMemo(
    () => [
      {
        key: "select",
        width: "46px",
        header: (
          <Checkbox
            checked={allVisibleSelected}
            onChange={toggleAllVisible}
            label="Select all objects on this page"
          />
        ),
        render: (row) => (
          <Checkbox
            checked={selected.has(row.upload_id)}
            onChange={(checked) => toggleOne(row.upload_id, checked)}
            label={`Select ${row.filename}`}
          />
        ),
      },
      {
        key: "name",
        header: "Name",
        sortable: true,
        // The design marks the grouping key on this column. It is not a choice
        // here — objects group by the feed they were ingested under.
        headerAfter: (
          <span
            className="dt-head-mark"
            aria-disabled="true"
            title="Objects group by ingest group; the grouping key is not configurable"
          >
            <SitemapIcon size={13} />
          </span>
        ),
        value: (row) => row.filename.toLowerCase(),
        render: (row) => (
          <Link href={`/uploads/${row.upload_id}`} className="object-name" title={row.filename}>
            {row.filename}
          </Link>
        ),
      },
      {
        key: "path",
        header: "Object path",
        sortable: true,
        value: (row) => row.landing_key.toLowerCase(),
        render: (row) => (
          <span className="object-path" title={row.landing_key}>
            {row.landing_key}
          </span>
        ),
      },
      {
        key: "size",
        header: "Size",
        width: "104px",
        sortable: true,
        value: (row) => row.size_bytes,
        render: (row) => <span className="cell-plain">{formatSize(row.size_bytes)}</span>,
      },
      {
        key: "updated",
        header: "Updated",
        width: "132px",
        sortable: true,
        value: (row) => Date.parse(row.created_ts) || 0,
        render: (row) => <span className="cell-plain">{formatDate(row.created_ts)}</span>,
      },
      {
        key: "actions",
        header: "Actions",
        width: "92px",
        render: (row) => (
          <RowMenu
            label={`Actions for ${row.filename}`}
            items={[
              { label: "Open object", href: `/uploads/${row.upload_id}` },
              { label: "Map to domain", href: `/mapping/${encodeURIComponent(group)}` },
              {
                label: "Delete object",
                reason: "Uploads are append-only on this build — there is no delete endpoint",
                danger: true,
              },
            ]}
          />
        ),
      },
    ],
    [allVisibleSelected, selected, group, visible],
  );

  return (
    <>
      <TableToolbar query={query} onQueryChange={setQuery} placeholder="Search...">
        {selected.size ? <span className="selection-count">{selected.size} selected</span> : null}
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
          disabled={exportRows.length === 0}
          title={selected.size ? "Download the selected objects" : "Download the listed objects"}
        >
          <DownloadIcon size={15} /> All ({exportRows.length})
        </button>
        <Link href={`/data/intake/new?feed=${encodeURIComponent(group)}`} className="btn-dark">
          <PlusIcon size={15} /> Add Object
        </Link>
        <button
          type="button"
          className="btn-outline btn-square"
          disabled
          title="Group settings are not part of this build"
          aria-label="Group settings"
        >
          <SettingsIcon size={16} />
        </button>
      </TableToolbar>

      <DataTable
        rows={visible}
        columns={columns}
        rowKey={(row) => row.upload_id}
        variant="grid"
        initialSort={{ key: "updated", dir: "desc" }}
        emptyMessage={
          query ? `No object matches “${query}”.` : "No objects in this group yet."
        }
      />

      <Pagination
        page={currentPage}
        pageCount={pageCount}
        pageSize={pageSize}
        totalItems={filtered.length}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
      />
    </>
  );
}
