import { NextResponse } from "next/server";

import { loadSystemStatus } from "@/lib/status-server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const NO_CACHE_HEADERS = {
  "Cache-Control": "no-store, no-cache, must-revalidate",
  Pragma: "no-cache",
  Expires: "0",
} as const;

export async function GET() {
  const { data, error } = await loadSystemStatus();
  if (error) {
    return NextResponse.json(
      { error, ...data },
      { status: 500, headers: NO_CACHE_HEADERS }
    );
  }
  return NextResponse.json(data, { headers: NO_CACHE_HEADERS });
}
