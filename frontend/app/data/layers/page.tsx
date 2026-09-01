import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { Layer } from "@/lib/types";

/**
 * The medallion spine — six layers, and what is in each one. W3-01.
 *
 * WHY THIS SCREEN EXISTS beside the Data Explorer. The Explorer answers "what
 * data do we have, and where is it" from the FEED's side: which feeds land,
 * where their files go. This answers it from the PLATFORM's side: which layers
 * the architecture names, which of them exist on the plane, and how much is in
 * them. Both were previously only answerable by reading `vocabulary.Layer`
 * beside `all_schemas()` beside a psql session, and a platform whose own
 * structure is legible only from source is the failure this programme exists
 * to end.
 *
 * ALL SIX ARE HERE, INCLUDING THE THREE THAT ARE NOT BUILT. That is the
 * screen's main claim and it is deliberate:
 *
 *   · showing only the built three would say "the spine is complete";
 *   · showing the other three as ordinary empty layers would say "something
 *     is broken";
 *   · showing them as NOT BUILT, with the wave that builds them and the reason
 *     they are empty, is the only rendering that is true.
 *
 * `row_count` is null rather than 0 whenever nothing is on the plane, and the
 * two are rendered differently — "—" against "0 rows". A deployment with no
 * data plane reporting "Bronze: 0 rows" would be a lie that reads like a
 * healthy empty platform.
 */
export default async function MedallionLayers() {
  const layers = await attempt<Layer[]>("/api/layers");
  if (isRefused(layers)) return <RefusalNotice refusal={layers} />;

  const built = layers.filter((l) => l.status === "built");
  const planeIsFitted = built.some((l) => l.row_count !== null);
  const total = built.reduce((sum, l) => sum + (l.row_count ?? 0), 0);

  return (
    <>
      <h1>Medallion Layers</h1>
      <p className="lede">
        Which layers exist, what is in each one, and which are not built yet.
      </p>

      <div className="card">
        <p className="note">
          Data cannot skip a layer, and cannot advance until that layer&rsquo;s gate passes.{" "}
          <strong>
            {built.length} of {layers.length}
          </strong>{" "}
          layers are built and hold{" "}
          {planeIsFitted ? (
            <strong>{total.toLocaleString()} rows</strong>
          ) : (
            <span className="note">no counted rows — no data plane is fitted</span>
          )}
          . The other {layers.length - built.length} are on the spine with the wave that builds
          them, because a map that omitted them would read as a finished one.
        </p>
      </div>

      <ol className="spine">
        {layers.map((layer, index) => (
          <li key={layer.layer} className="card" data-status={layer.status}>
            <div className="spine-head">
              <span className="spine-step" aria-hidden="true">
                {index + 1}
              </span>
              <div className="spine-title">
                {layer.status === "not_built" ? (
                  // No link. A destination that answers "not built" is still
                  // worth opening — the reason lives there — but it must not
                  // look like the built layers beside it.
                  <strong>{layer.label}</strong>
                ) : (
                  <Link className="cited" href={layer.route}>
                    <strong>{layer.label}</strong>
                  </Link>
                )}
                <span className="mono note">
                  {layer.schema_name || "no schema on the plane"}
                </span>
              </div>
              <div className="spine-figures">
                <span className="label">
                  {layer.entry_gate ? `${layer.entry_gate} guards entry` : "arrival"}
                </span>
                <span className="big">
                  {layer.row_count === null ? "—" : layer.row_count.toLocaleString()}
                </span>
                <span className="note">
                  {layer.row_count === null
                    ? "nothing on the plane"
                    : `${layer.row_count === 1 ? "row" : "rows"} in ${layer.table_count} ${
                        layer.table_count === 1 ? "table" : "tables"
                      }`}
                </span>
              </div>
            </div>

            <p className="note">{layer.purpose}</p>

            {layer.status !== "built" && (
              <p className="note" data-absence={layer.status}>
                <span className="tag">
                  {layer.status === "provisioned_empty"
                    ? "Provisioned, empty"
                    : `Not built — Wave ${layer.wave}`}
                </span>{" "}
                {layer.absence_reason}
              </p>
            )}

            {layer.status === "not_built" && (
              <Link className="cited note" href={layer.route}>
                Why this layer is empty
              </Link>
            )}
          </li>
        ))}
      </ol>

      <h2>What this screen does not do</h2>
      <div className="card">
        <p className="note">
          Every column the schema contract flags <span className="mono">is_phi</span> is masked
          before a row leaves the server — for every viewer, including a steward. Masking is not a
          permission tier here: there is one answer for everyone, which is the answer nobody has
          to be trusted with. An unmask ceremony, with its own approval and its own audit row,
          is CF-V4-E14-04.
        </p>
      </div>
    </>
  );
}
