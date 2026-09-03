/** Platform catalog: compute configs, templates and connections.
 *
 *  These are STATIC on this build. The control plane exposes no catalog
 *  endpoints yet, so the lists live here as one swappable module — replace each
 *  export with a fetch when `/api/compute-configs`, `/api/templates` and
 *  `/api/connections` exist, and nothing else in the form has to change. */

export interface CatalogEntry {
  id: string;
  label: string;
}

export type ConnectionKind = "blob" | "warehouse";

export interface ConnectionEntry extends CatalogEntry {
  kind: ConnectionKind;
}

export const COMPUTE_CONFIGS: CatalogEntry[] = [
  { id: "single-node-v2", label: "single-node-v2 (dl-dev-environment)" },
  { id: "small-cluster-v2", label: "small-cluster-v2 (dl-dev-environment)" },
  { id: "job-cluster-v3", label: "job-cluster-v3 (dl-dev-environment)" },
];

export const FLOW_TEMPLATES: CatalogEntry[] = [
  { id: "databricks-validate-archive-ingest-v1", label: "Databricks Validate Archive Ingest Flow (V1.0.0)" },
  { id: "databricks-validate-ingest-v1", label: "Databricks Validate Ingest Flow (V1.0.0)" },
];

export const PIPELINE_TEMPLATES: CatalogEntry[] = [
  { id: "file-to-db-ingestion-v3", label: "File to DB Ingestion V3 (v1.0.0)" },
  { id: "file-to-db-ingestion-v2", label: "File to DB Ingestion V2 (v1.0.0)" },
  { id: "file-to-delta-append-v1", label: "File to Delta Append (v1.0.0)" },
];

export const SOURCE_CONNECTIONS: ConnectionEntry[] = [
  { id: "cinq-landing-blob-storage", label: "cinq-landing-blob-storage", kind: "blob" },
  { id: "cinq-sftp-drop", label: "cinq-sftp-drop", kind: "blob" },
];

export const TARGET_CONNECTIONS: ConnectionEntry[] = [
  { id: "cinqdev-databricks-dev", label: "cinqdev-databricks-dev", kind: "warehouse" },
  { id: "cinqdev-postgres-dev", label: "cinqdev-postgres-dev", kind: "warehouse" },
];
