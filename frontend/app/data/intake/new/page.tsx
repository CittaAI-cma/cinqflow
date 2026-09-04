import AddIngestionModal from "@/components/ingestion/AddIngestionModal";
import IngestionRegister from "@/components/ingestion/IngestionRegister";
import {
  DATA_DOMAINS,
  DEFAULT_UPLOADER,
  PLATFORM_ENVIRONMENT,
  PLATFORM_PROJECT,
  SOURCE_SYSTEMS,
} from "@/lib/appConfig";
import { listUploads } from "@/lib/api";

export const dynamic = "force-dynamic";

/** Domains and source systems are seeded (DATA_DOMAINS/SOURCE_SYSTEMS) so a
 *  deployment with no uploads yet still has real choices - a fresh production
 *  environment previously offered zero domain options and no way to tell the
 *  picker's "type to add a custom one" escape hatch existed (see Combobox's
 *  own empty-state fix). Anything already ingested is merged in alongside the
 *  seed. */
async function pickerOptions() {
  try {
    const { uploads } = await listUploads();
    const domains = [
      ...new Set([...DATA_DOMAINS, ...uploads.map((upload) => upload.domain)]),
    ]
      .filter(Boolean)
      .sort();
    const sourceSystems = [
      ...new Set([...SOURCE_SYSTEMS, ...uploads.map((upload) => upload.source_system)]),
    ]
      .filter(Boolean)
      .sort();
    return { domains, sourceSystems };
  } catch {
    return { domains: [...DATA_DOMAINS], sourceSystems: [...SOURCE_SYSTEMS] };
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
