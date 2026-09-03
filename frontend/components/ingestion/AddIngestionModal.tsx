"use client";

import { useRouter } from "next/navigation";
import AddIngestionForm from "@/components/ingestion/AddIngestionForm";
import Modal from "@/components/ui/Modal";
import { ShieldIcon } from "@/components/icons";

/** Route-driven: the modal lives at /data/intake/new, so it is linkable and
 *  survives a reload, and closing it navigates back to the register. */
export default function AddIngestionModal({
  project,
  environment,
  domains,
  sourceSystems,
  uploader,
  initialGroupName = "",
}: {
  project: string;
  environment: string;
  domains: string[];
  sourceSystems: string[];
  uploader: string;
  /** Prefilled when arriving from a group's "Add Object". */
  initialGroupName?: string;
}) {
  const router = useRouter();
  const close = () => router.push("/data/intake");

  return (
    <Modal title="Add New Ingestion" badge={<ShieldIcon size={18} />} onClose={close}>
      <AddIngestionForm
        project={project}
        environment={environment}
        domains={domains}
        sourceSystems={sourceSystems}
        uploader={uploader}
        initialGroupName={initialGroupName}
        onCancel={close}
      />
    </Modal>
  );
}
