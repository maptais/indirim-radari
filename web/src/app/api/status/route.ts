import { NextRequest, NextResponse } from "next/server";

import { loadSystemStatus } from "@/lib/status-server";

export const dynamic = "force-dynamic";

const NO_CACHE_HEADERS = {
  "Cache-Control": "no-store, no-cache, must-revalidate",
  Pragma: "no-cache",
  Expires: "0",
} as const;

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const bypassCache =
    searchParams.get("refresh") === "1" ||
    searchParams.get("refresh") === "true";

  const { data, error } = await loadSystemStatus({ bypassCache });
  if (error) {
    return NextResponse.json(
      { error, ...data },
      { status: 500, headers: NO_CACHE_HEADERS }
    );
  }
  return NextResponse.json(data, { headers: NO_CACHE_HEADERS });
}
