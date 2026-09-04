import ApiUnreachable from "@/components/ui/ApiUnreachable";
import IngestionTable from "@/components/ingestion/IngestionTable";
import { PLATFORM_ENVIRONMENT } from "@/lib/appConfig";
import { listUploads } from "@/lib/api";

/** The register itself, so /data/intake and /data/intake/new can both render it
 *  — the modal route shows this behind the dialog rather than an empty page. */
export default async function IngestionRegister() {
  let uploads: Awaited<ReturnType<typeof listUploads>>["uploads"] = [];
  let unreachable = false;

  try {
    ({ uploads } = await listUploads());
  } catch (err) {
    console.error("listUploads failed:", err);
    unreachable = true;
  }

  if (unreachable) {
    return <ApiUnreachable />;
  }

  return <IngestionTable uploads={uploads} environment={PLATFORM_ENVIRONMENT} />;
}
