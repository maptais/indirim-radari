import "server-only";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let cachedAdminClient: SupabaseClient | null = null;

function normalizeSupabaseUrl(rawUrl: string): string {
  let cleaned = rawUrl.trim().replace(/\/+$/, "");
  if (cleaned.endsWith("/rest/v1")) {
    cleaned = cleaned.slice(0, -"/rest/v1".length);
  }
  return cleaned.replace(/\/+$/, "");
}

export function getSupabaseAdmin(): SupabaseClient {
  if (cachedAdminClient) {
    return cachedAdminClient;
  }

  const rawUrl =
    process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL || "";
  const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY || "";

  if (!rawUrl || !serviceRoleKey) {
    throw new Error(
      "Sunucu Supabase yapılandırması eksik (NEXT_PUBLIC_SUPABASE_URL ve SUPABASE_SERVICE_ROLE_KEY gerekli)."
    );
  }

  cachedAdminClient = createClient(normalizeSupabaseUrl(rawUrl), serviceRoleKey, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
  });

  return cachedAdminClient;
}
