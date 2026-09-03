import AddIngestionModal from "@/components/ingestion/AddIngestionModal";
import IngestionRegister from "@/components/ingestion/IngestionRegister";
import {
  DEFAULT_UPLOADER,
  PLATFORM_ENVIRONMENT,
  PLATFORM_PROJECT,
  SOURCE_SYSTEMS,
} from "@/lib/appConfig";
import { listUploads } from "@/lib/api";

export const dynamic = "force-dynamic";

/** Domains and source systems come from what has actually been ingested, so the
 *  pickers offer real values rather than a hardcoded taxonomy. */
async function pickerOptions() {
  try {
    const { uploads } = await listUploads();
    const domains = [...new Set(uploads.map((upload) => upload.domain).filter(Boolean))].sort();
    const sourceSystems = [
      ...new Set([...SOURCE_SYSTEMS, ...uploads.map((upload) => upload.source_system)]),
    ]
      .filter(Boolean)
      .sort();
    return { domains, sourceSystems };
  } catch {
    return { domains: [], sourceSystems: [...SOURCE_SYSTEMS] };
  }
}

export default async function AddIngestionPage({
  searchParams,
}: {
  searchParams: Promise<{ feed?: string }>;
}) {
  const [{ domains, sourceSystems }, { feed }] = await Promise.all([
    pickerOptions(),
    searchParams,
  ]);

  return (
    <>
      <IngestionRegister />
      <AddIngestionModal
        project={PLATFORM_PROJECT}
        environment={PLATFORM_ENVIRONMENT}
        domains={domains}
        sourceSystems={sourceSystems}
        uploader={DEFAULT_UPLOADER}
        initialGroupName={feed ?? ""}
      />
    </>
  );
}
