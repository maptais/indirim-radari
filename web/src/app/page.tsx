import { DealsFeed } from "@/components/DealsFeed";
import {
  DEFAULT_PAGE_SIZE,
  createEmptyProductsResponse,
  queryProductsCatalog,
} from "@/lib/products-server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

async function loadInitialProducts() {
  try {
    const { data, error } = await queryProductsCatalog({
      page: 1,
      pageSize: DEFAULT_PAGE_SIZE,
    });
    if (error) {
      return {
        data,
        error: `Ürünler alınamadı: ${error}`,
      };
    }
    return { data, error: null };
  } catch (err) {
    return {
      data: createEmptyProductsResponse(DEFAULT_PAGE_SIZE),
      error:
        err instanceof Error
          ? err.message
          : "Veritabanına bağlanılamadı.",
    };
  }
}

export default async function HomePage() {
  const { data, error } = await loadInitialProducts();
  return <DealsFeed initialData={data} initialError={error} />;
}
