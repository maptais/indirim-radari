import type { Metadata } from "next";

import { SettingsForm } from "@/components/SettingsForm";
import { loadSettingsPageData } from "@/lib/settings-server";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Ayarlar",
};

export default async function SettingsPage() {
  const { settings, activeSubscriptionCount, error } =
    await loadSettingsPageData();
  return (
    <SettingsForm
      initialSettings={settings}
      initialActivePushCount={activeSubscriptionCount}
      initialError={error}
    />
  );
}
