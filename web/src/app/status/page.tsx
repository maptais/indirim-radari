import type { Metadata } from "next";

import { SystemStatusView } from "@/components/SystemStatusView";
import { loadSystemStatus } from "@/lib/status-server";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Sistem Durumu",
};

export default async function SystemStatusPage() {
  const { data, error } = await loadSystemStatus();
  return <SystemStatusView initialData={data} initialError={error} />;
}
