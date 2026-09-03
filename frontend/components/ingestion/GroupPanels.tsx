import { DocumentIcon, LayersIcon, PlusIcon, RefreshIcon, UploadIcon } from "@/components/icons";

/** Group-level configuration surfaces from the design. None of these have a
 *  control-plane endpoint on this build, so each action is inert and says why —
 *  a button that looks live but saves nothing is worse than a disabled one. */
export default function GroupPanels() {
  return (
    <div className="group-panels">
      <section className="group-panel">
        <div className="group-panel-head">
          <div>
            <h3>
              <DocumentIcon size={16} /> Supporting document
            </h3>
            <p>
              Optional. Upload a BRD or mapping document. Its column descriptions and glossary
              synonyms are applied automatically when you Confirm &amp; Profile.
            </p>
          </div>
          <button
            type="button"
            className="btn-outline"
            disabled
            title="No document store on this build"
          >
            <UploadIcon size={15} /> Upload document
          </button>
        </div>
      </section>

      <section className="group-panel">
        <div className="group-panel-head">
          <div>
            <h3>
              <LayersIcon size={16} /> Bronze extensions
            </h3>
            <p>
              Group-level derived columns for every ingest object. Excluded from layout drift.
            </p>
          </div>
          <div className="group-panel-actions">
            <button
              type="button"
              className="btn-outline"
              disabled
              title="Derived columns are not configurable on this build"
            >
              Add file_date preset
            </button>
            <button
              type="button"
              className="btn-outline"
              disabled
              title="Derived columns are not configurable on this build"
            >
              <PlusIcon size={15} /> Add column
            </button>
            <button
              type="button"
              className="btn-accent"
              disabled
              title="Nothing to sync — the catalog is read-only on this build"
            >
              Save &amp; sync catalog
            </button>
          </div>
        </div>
        <p className="group-panel-empty">No bronze extension columns defined.</p>
      </section>

      <div className="group-panel-footer">
        <button
          type="button"
          className="btn-warn"
          disabled
          title="Profiling runs once per upload; there is no re-profile endpoint yet"
        >
          <RefreshIcon size={15} /> Rediscover layout
        </button>
      </div>
    </div>
  );
}
