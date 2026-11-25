import { NextRequest, NextResponse } from "next/server";

import { DEFAULT_PAGE_SIZE, queryProductsCatalog } from "@/lib/products-server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const NO_CACHE_HEADERS = {
  "Cache-Control": "no-store, no-cache, must-revalidate",
  Pragma: "no-cache",
  Expires: "0",
} as const;

function parseCsvSizes(raw: string | null): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean);
}

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);

    const gendersParam = searchParams.has("genders")
      ? (searchParams.get("genders") ?? "")
      : (searchParams.get("gender") ?? "settings");

    const topSizes = searchParams.has("topSizes")
      ? parseCsvSizes(searchParams.get("topSizes"))
      : undefined;
    const bottomSizes = searchParams.has("bottomSizes")
      ? parseCsvSizes(searchParams.get("bottomSizes"))
      : undefined;
    const shoeSizes = searchParams.has("shoeSizes")
      ? parseCsvSizes(searchParams.get("shoeSizes"))
      : undefined;

    const onlyMySize =
      searchParams.get("onlyMySize") === "true" ||
      searchParams.get("mySizesOnly") === "true";
    const hideUnknownSize =
      searchParams.get("hideUnknownSize") === "true" ||
      searchParams.get("hideUnknownSizes") === "true";

    const minDiscountRaw = searchParams.get("minDiscount");
    const minDiscount =
      minDiscountRaw !== null && minDiscountRaw !== ""
        ? Number(minDiscountRaw)
        : 0;

    const pageRaw = searchParams.get("page");
    const page = pageRaw !== null && pageRaw !== "" ? Number(pageRaw) : 1;

    const pageSizeRaw = searchParams.get("pageSize");
    const pageSize =
      pageSizeRaw !== null && pageSizeRaw !== ""
        ? Number(pageSizeRaw)
        : DEFAULT_PAGE_SIZE;

    const { data, error } = await queryProductsCatalog({
      brand: searchParams.get("brand") || "all",
      category: searchParams.get("category") || "all",
      genders: gendersParam,
      topSizes,
      bottomSizes,
      shoeSizes,
      onlyMySize,
      hideUnknownSize,
      minDiscount,
      realOnly: searchParams.get("realOnly") === "true",
      cartOnly: searchParams.get("cartOnly") === "true",
      sort: searchParams.get("sort") || "discount",
      q: searchParams.get("q") || "",
      page,
      pageSize,
    });

    if (error) {
      return NextResponse.json(
        { error: "Ürünler veritabanından alınamadı.", details: error },
        { status: 500, headers: NO_CACHE_HEADERS }
      );
    }

    return NextResponse.json(data, { headers: NO_CACHE_HEADERS });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Bilinmeyen sunucu hatası";
    return NextResponse.json(
      { error: "İndirim akışı yüklenirken bir hata oluştu.", details: message },
      { status: 500, headers: NO_CACHE_HEADERS }
    );
  }
}
