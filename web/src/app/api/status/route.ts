import { NextResponse } from "next/server";

import { loadSystemStatus } from "@/lib/status-server";

export const dynamic = "force-dynamic";

export async function GET() {
  const { data, error } = await loadSystemStatus();
  if (error) {
    return NextResponse.json(
      { error, ...data },
      { status: 500 }
    );
  }
  return NextResponse.json(data);
}
