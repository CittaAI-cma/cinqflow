import ApiUnreachable from "@/components/ui/ApiUnreachable";
import { notFound } from "next/navigation";
import GroupPanels from "@/components/ingestion/GroupPanels";
import GroupStageTabs from "@/components/ingestion/GroupStageTabs";
import ObjectsTable from "@/components/ingestion/ObjectsTable";
import { getUpload, listUploads, type Upload, type UploadDetail } from "@/lib/api";
import { groupStage, isStageAdverse } from "@/lib/lifecycleStage";

export const dynamic = "force-dynamic";

/** Promotion state lives in the batch runs, not on the upload row, so telling
 *  "Dq Applied" from "Promoted" costs one detail request per object. Groups are
 *  small; an unusually large one keeps the cheap read and settles for the stage
 *  the upload status alone can prove. */
const DETAIL_BUDGET = 24;

async function loadDetails(objects: Upload[]): Promise<(UploadDetail | null)[]> {
  if (objects.length > DETAIL_BUDGET) return [];
  return Promise.all(
    objects.map((object) => getUpload(object.upload_id).catch(() => null)),
  );
}

export default async function IngestGroupPage({
  params,
}: {
  params: Promise<{ group: string }>;
}) {
  const { group: raw } = await params;
  const group = decodeURIComponent(raw);

  let uploads: Upload[] = [];
  try {
    ({ uploads } = await listUploads());
  } catch (err) {
    console.error("listUploads failed:", err);
    return <ApiUnreachable />;
  }

  const objects = uploads.filter((upload) => upload.feed === group);
  if (objects.length === 0) notFound();

  const stage = groupStage(objects, await loadDetails(objects));

  return (
    <div className="group-view">
      <div className="stage-bar">
        <GroupStageTabs group={group} />
        <div className="stage-meta">
          <span className="stage-meta-label">Stage :</span>
          <span className={`stage-meta-stage${stage && isStageAdverse(stage) ? " adverse" : ""}`}>
            {stage ?? "Unknown"}
          </span>
          <span className="stage-meta-sep">|</span>
          <span className="stage-meta-count">
            {objects.length} {objects.length === 1 ? "Object" : "Objects"}
          </span>
        </div>
      </div>

      <ObjectsTable group={group} objects={objects} />
      <GroupPanels />
    </div>
  );
}
