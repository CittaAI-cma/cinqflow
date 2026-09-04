"use client";

import { useActionState, useState } from "react";
import { useFormStatus } from "react-dom";
import MedallionTierEditor from "@/components/ingestion/MedallionTierEditor";
import Combobox, { type ComboboxOption } from "@/components/ui/Combobox";
import FileDropzone from "@/components/ui/FileDropzone";
import FormField from "@/components/ui/FormField";
import CollapsibleSection from "@/components/ui/CollapsibleSection";
import { DatabaseIcon, GlobeIcon } from "@/components/icons";
import { submitUpload, type UploadState } from "@/app/actions";
import {
  FLOW_TEMPLATES,
  PIPELINE_TEMPLATES,
  SOURCE_CONNECTIONS,
  TARGET_CONNECTIONS,
  computeConfigs,
  type ConnectionEntry,
} from "@/lib/platformCatalog";

function Footer({ onCancel, ready }: { onCancel: () => void; ready: boolean }) {
  const { pending } = useFormStatus();
  return (
    <div className="modal-footer">
      <button type="button" className="btn-outline" onClick={onCancel} disabled={pending}>
        Cancel
      </button>
      <button type="submit" className="btn-dark" disabled={pending || !ready}>
        {pending ? "Submitting…" : "Submit"}
      </button>
    </div>
  );
}

function connectionOptions(entries: ConnectionEntry[]): ComboboxOption[] {
  return entries.map((entry) => ({
    value: entry.id,
    label: entry.label,
    icon:
      entry.kind === "blob" ? (
        <GlobeIcon size={14} className="conn-glyph blob" />
      ) : (
        <DatabaseIcon size={14} className="conn-glyph warehouse" />
      ),
  }));
}

export default function AddIngestionForm({
  project,
  environment,
  domains,
  sourceSystems,
  uploader,
  initialGroupName = "",
  onCancel,
}: {
  project: string;
  environment: string;
  domains: string[];
  sourceSystems: string[];
  uploader: string;
  initialGroupName?: string;
  onCancel: () => void;
}) {
  const [state, action] = useActionState<UploadState, FormData>(submitUpload, {});

  const [domain, setDomain] = useState("");
  const [groupName, setGroupName] = useState(initialGroupName);
  const [sourceSystem, setSourceSystem] = useState(sourceSystems[0] ?? "");
  const [sourceConnection, setSourceConnection] = useState(SOURCE_CONNECTIONS[0].id);
  const [targetConnection, setTargetConnection] = useState(TARGET_CONNECTIONS[0].id);
  const [hasFile, setHasFile] = useState(false);
  const computeConfigOptions = computeConfigs(environment);

  /** No "No domain" row: the control plane requires a domain to resolve
   *  canonical knowledge, so offering one would only earn a 422. */
  const domainOptions: ComboboxOption[] = domains.map((value) => ({ value, label: value }));
  const sourceSystemOptions: ComboboxOption[] = sourceSystems.map((value) => ({
    value,
    label: value,
  }));

  const ready =
    hasFile && domain.trim() !== "" && groupName.trim() !== "" && sourceSystem !== "";

  return (
    <form action={action} className="ingestion-form">
      <section className="form-section">
        <h3 className="form-section-label">This file</h3>
        <p className="form-section-note">
          Everything here is read by the profiler. Choose carefully.
        </p>
        <div className="form-grid">
          <FormField
            label="File"
            required
            span
            hint="The original is preserved. Fingerprint is computed server-side, so identical bytes are refused rather than duplicated."
          >
            <FileDropzone
              name="file"
              accept=".csv,.xlsx,.xlsm"
              required
              onPicked={(file) => setHasFile(Boolean(file))}
            />
          </FormField>

          <FormField
            label="Source system"
            htmlFor="source_system"
            required
            hint="Recorded against the upload and used to resolve source knowledge"
          >
            <Combobox
              id="source_system"
              name="source_system"
              value={sourceSystem}
              onChange={setSourceSystem}
              options={sourceSystemOptions}
              placeholder="Select a source system..."
              searchPlaceholder="Search source systems"
              allowCustom
            />
          </FormField>

          <FormField label="Business date" htmlFor="business_date" required>
            <input
              id="business_date"
              name="business_date"
              type="date"
              className="text-input"
              defaultValue="2026-06-01"
              required
            />
          </FormField>

          <FormField label="Uploader" htmlFor="uploader" required>
            <input
              id="uploader"
              name="uploader"
              className="text-input"
              defaultValue={uploader}
              required
            />
          </FormField>
        </div>
      </section>

      <section className="form-section">
        <h3 className="form-section-label">Where it belongs</h3>
        <p className="form-section-note">
          Names the Bronze and Silver Raw tables. Cannot be changed after landing.
        </p>
        <div className="form-grid">
          <FormField
            label="Data domain"
            htmlFor="domain"
            required
            hint="Pick an existing domain or type a new one"
          >
            <Combobox
              id="domain"
              name="domain"
              value={domain}
              onChange={setDomain}
              options={domainOptions}
              placeholder="Select a data domain..."
              searchPlaceholder="Select the domain this data belongs to"
              allowCustom
            />
          </FormField>

          <FormField
            label="Group Name"
            htmlFor="feed"
            required
            hint="Becomes the feed this file belongs to"
          >
            <input
              id="feed"
              name="feed"
              className="text-input"
              value={groupName}
              placeholder="e.g., customer data sync"
              onChange={(event) => setGroupName(event.target.value)}
              required
            />
          </FormField>
        </div>
      </section>

      <CollapsibleSection title="How it will run — recorded now, not used yet">
        <p className="form-section-note">
          Everything below is posted with the upload but ignored by the control plane on this
          build. Nothing here affects this upload.
        </p>
        <div className="form-grid">
          <FormField label="Project" htmlFor="project" hint="From platform configuration">
            <select id="project" className="native-select" defaultValue={project} disabled>
              <option value={project}>{project}</option>
            </select>
          </FormField>

          <FormField label="Environment" htmlFor="environment" hint="From platform configuration">
            <select id="environment" className="native-select" defaultValue={environment} disabled>
              <option value={environment}>{environment}</option>
            </select>
          </FormField>

          <FormField
            label="Description"
            htmlFor="description"
            span
            unavailable="Not stored by the control plane on this build"
          >
            <textarea
              id="description"
              className="textarea"
              rows={3}
              placeholder="Not stored by the control plane on this build"
              disabled
            />
          </FormField>

          <FormField label="Compute Config" htmlFor="compute">
            <select
              id="compute"
              name="compute_config"
              className="native-select"
              defaultValue={computeConfigOptions[0].id}
            >
              {computeConfigOptions.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.label}
                </option>
              ))}
            </select>
          </FormField>

          <FormField
            label="Ingestion workflow"
            htmlFor="workflow"
            hint="Profile → interpret → G1 → Bronze → map → preview → G2 → Silver"
          >
            <select
              id="workflow"
              name="workflow"
              className="native-select"
              defaultValue="csv_full_pipeline"
            >
              <option value="csv_full_pipeline">CSV — full pipeline</option>
            </select>
          </FormField>

          <FormField label="Pipeline Template" htmlFor="pipeline">
            <select
              id="pipeline"
              name="pipeline_template"
              className="native-select"
              defaultValue={PIPELINE_TEMPLATES[0].id}
            >
              {PIPELINE_TEMPLATES.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.label}
                </option>
              ))}
            </select>
          </FormField>

          <FormField label="Flow Template" htmlFor="flow">
            <select
              id="flow"
              name="flow_template"
              className="native-select"
              defaultValue={FLOW_TEMPLATES[0].id}
            >
              {FLOW_TEMPLATES.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.label}
                </option>
              ))}
            </select>
          </FormField>

          <FormField label="Source Connection" htmlFor="source_connection">
            <Combobox
              id="source_connection"
              name="source_connection"
              value={sourceConnection}
              onChange={setSourceConnection}
              options={connectionOptions(SOURCE_CONNECTIONS)}
              placeholder="Select a connection..."
              searchPlaceholder="Search connections"
            />
          </FormField>

          <FormField label="Target Connection" htmlFor="target_connection">
            <Combobox
              id="target_connection"
              name="target_connection"
              value={targetConnection}
              onChange={setTargetConnection}
              options={connectionOptions(TARGET_CONNECTIONS)}
              placeholder="Select a connection..."
              searchPlaceholder="Search connections"
            />
          </FormField>
        </div>

        <CollapsibleSection title="Medallion lifecycle">
          <MedallionTierEditor name="medallion_tiers" />
        </CollapsibleSection>
      </CollapsibleSection>

      {state.error ? <p className="alert error">{state.error}</p> : null}

      <Footer onCancel={onCancel} ready={ready} />
    </form>
  );
}
