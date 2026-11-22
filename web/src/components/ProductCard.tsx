"use client";

import { useState } from "react";
import Image from "next/image";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Flame,
  ShoppingBag,
  ShoppingCart,
  Sparkles,
  Tag,
  TrendingDown,
} from "lucide-react";

import {
  formatPriceTry,
  formatRelativeTimeTr,
  getBrandLabel,
  getCategoryLabel,
  getGenderLabel,
} from "@/lib/constants";
import type { ProductRow } from "@/types/database";

interface ProductCardProps {
  product: ProductRow;
}

export function ProductCard({ product }: ProductCardProps) {
  const [imgFailed, setImgFailed] = useState(false);

  const brandLabel = getBrandLabel(product.brand);
  const categoryLabel = getCategoryLabel(product.category);
  const genderLabel = getGenderLabel(product.gender);
  const hasCartPrice =
    product.cart_price !== null &&
    product.cart_price > 0 &&
    product.cart_price < product.current_price;
  const effectivePrice =
    hasCartPrice && product.cart_price !== null
      ? product.cart_price
      : product.current_price;
  const refOriginalPrice =
    product.original_price !== null && product.original_price > effectivePrice
      ? product.original_price
      : hasCartPrice
        ? product.current_price
        : null;
  const hasOriginalPrice = refOriginalPrice !== null && refOriginalPrice > effectivePrice;
  const effectiveDiscountPercent =
    hasOriginalPrice && refOriginalPrice !== null
      ? Math.min(
          100,
          Math.max(
            0,
            Math.round(((refOriginalPrice - effectivePrice) / refOriginalPrice) * 100)
          )
        )
      : product.discount_percent;

  const hasLowestDays =
    product.is_real_discount &&
    product.lowest_price_days !== null &&
    product.lowest_price_days > 0;

  return (
    <article className="group flex flex-col overflow-hidden rounded-3xl border border-white/10 bg-zinc-900/75 shadow-lg shadow-black/25 transition-all duration-200 hover:border-white/20 hover:bg-zinc-900">
      {/* Product Image */}
      <div className="relative aspect-[4/5] w-full overflow-hidden bg-zinc-950">
        {product.image_url && !imgFailed ? (
          <Image
            src={product.image_url}
            alt={product.title}
            fill
            unoptimized
            sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
            onError={() => setImgFailed(true)}
            className="object-cover object-top transition-transform duration-500 group-hover:scale-[1.03]"
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 bg-gradient-to-br from-zinc-900 to-zinc-950 text-zinc-600">
            <ShoppingBag className="h-10 w-10 stroke-[1.4]" />
            <span className="text-xs font-medium text-zinc-500">
              {brandLabel}
            </span>
          </div>
        )}

        {/* Top gradient scrim for badge readability */}
        <div className="pointer-events-none absolute inset-x-0 top-0 h-20 bg-gradient-to-b from-black/70 via-black/25 to-transparent" />

        {/* Top-left: Discount Badge */}
        <div className="absolute top-3 left-3 flex flex-wrap items-center gap-1.5">
          {effectiveDiscountPercent > 0 && (
            <span className="inline-flex items-center rounded-full bg-emerald-500 px-2.5 py-1 text-xs font-bold tracking-tight text-zinc-950 shadow-md shadow-emerald-950/50">
              -%{effectiveDiscountPercent}
            </span>
          )}
          {product.is_critical_stock && (
            <span className="inline-flex items-center gap-1 rounded-full bg-rose-500/95 px-2.5 py-1 text-[11px] font-semibold text-white shadow-md shadow-rose-950/50">
              <Flame className="h-3 w-3" />
              Kritik Stok
            </span>
          )}
        </div>

        {/* Top-right: AI Style Score Badge */}
        <div className="absolute top-3 right-3">
          {product.ai_score !== null ? (
            <span
              className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold backdrop-blur-md ${
                product.ai_score >= 8
                  ? "bg-emerald-500/90 text-zinc-950"
                  : product.ai_score >= 6
                    ? "bg-amber-400/90 text-zinc-950"
                    : "bg-zinc-800/90 text-zinc-200 ring-1 ring-white/15"
              }`}
            >
              <Sparkles className="h-3 w-3" />
              AI {product.ai_score}/10
            </span>
          ) : (
            <span
              title="Henüz AI tarz puanı hesaplanmadı"
              className="inline-flex items-center gap-1 rounded-full bg-zinc-950/75 px-2.5 py-1 text-[11px] font-medium text-zinc-300 ring-1 ring-white/15 backdrop-blur-md"
            >
              <Sparkles className="h-3 w-3 text-zinc-400" />
              AI —/10
            </span>
          )}
        </div>

        {/* Bottom-left: Real Discount / Lowest Price Tag */}
        <div className="absolute bottom-3 left-3 right-3 flex items-center justify-between gap-2">
          {product.is_real_discount ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-950/85 px-2.5 py-1 text-[11px] font-medium text-emerald-300 ring-1 ring-emerald-400/35 backdrop-blur-md">
              {hasLowestDays ? (
                <>
                  <TrendingDown className="h-3.5 w-3.5 shrink-0" />
                  <span>Son {product.lowest_price_days} günün en düşüğü</span>
                </>
              ) : (
                <>
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                  <span>Gerçek İndirim</span>
                </>
              )}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-950/85 px-2.5 py-1 text-[11px] font-medium text-amber-300 ring-1 ring-amber-400/35 backdrop-blur-md">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              <span>Fiyat Takibinde</span>
            </span>
          )}
        </div>
      </div>

      {/* Card Body */}
      <div className="flex flex-1 flex-col gap-3 p-4">
        {/* Brand & Category & Timestamp */}
        <div className="flex items-center justify-between gap-2 text-[11px]">
          <div className="flex items-center gap-1.5">
            <span className="font-semibold tracking-wide text-emerald-400 uppercase">
              {brandLabel}
            </span>
            <span className="text-zinc-600">•</span>
            <span className="text-zinc-400">{categoryLabel}</span>
            <span className="text-zinc-600">•</span>
            <span className="text-zinc-500">{genderLabel}</span>
          </div>
          <span className="text-zinc-500">
            {formatRelativeTimeTr(product.last_seen_at)}
          </span>
        </div>

        {/* Title */}
        <h3 className="line-clamp-2 text-sm leading-snug font-semibold text-zinc-100">
          {product.title}
        </h3>

        {/* Promo Badge */}
        {product.promo_badge && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="inline-flex items-center gap-1 rounded-full border border-amber-400/40 bg-amber-400/15 px-2.5 py-0.5 text-[11px] font-semibold text-amber-200">
              <Tag className="h-3 w-3 shrink-0 text-amber-300" />
              <span>{product.promo_badge}</span>
            </span>
          </div>
        )}

        {/* Prices */}
        {hasCartPrice && product.cart_price !== null ? (
          <div className="space-y-1.5">
            <div className="inline-flex flex-wrap items-center gap-2 rounded-2xl border border-emerald-500/35 bg-emerald-500/12 px-3 py-1.5">
              <span className="inline-flex items-center gap-1 text-[11px] font-semibold tracking-wide text-emerald-300 uppercase">
                <ShoppingCart className="h-3.5 w-3.5" />
                Sepette:
              </span>
              <span className="text-xl font-bold tracking-tight text-emerald-300">
                {formatPriceTry(product.cart_price)}
              </span>
            </div>
            <div className="flex flex-wrap items-baseline gap-2 text-xs">
              {product.original_price !== null &&
                product.original_price > product.current_price && (
                  <span className="text-zinc-500 line-through">
                    {formatPriceTry(product.original_price)}
                  </span>
                )}
              <span className="text-zinc-400 line-through">
                {formatPriceTry(product.current_price)}
              </span>
              {effectiveDiscountPercent > 0 && (
                <span className="font-semibold text-emerald-400">
                  Toplam -%{effectiveDiscountPercent}
                </span>
              )}
            </div>
          </div>
        ) : (
          <div className="flex items-baseline gap-2.5">
            <span className="text-xl font-bold tracking-tight text-white">
              {formatPriceTry(product.current_price)}
            </span>
            {hasOriginalPrice && (
              <span className="text-sm text-zinc-500 line-through">
                {formatPriceTry(refOriginalPrice)}
              </span>
            )}
          </div>
        )}

        {/* Available Sizes */}
        {product.available_sizes.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-0.5 text-[11px] text-zinc-500">Bedenler:</span>
            {product.available_sizes.slice(0, 7).map((size) => (
              <span
                key={size}
                className="rounded-lg border border-white/10 bg-zinc-950/80 px-2 py-0.5 text-[11px] font-medium text-zinc-200"
              >
                {size}
              </span>
            ))}
            {product.available_sizes.length > 7 && (
              <span className="text-[11px] text-zinc-500">
                +{product.available_sizes.length - 7}
              </span>
            )}
          </div>
        )}

        {/* Fabric or AI Comment (when available) */}
        {(product.fabric_info || product.ai_comment) && (
          <div className="space-y-1.5 rounded-2xl border border-white/5 bg-zinc-950/60 px-3 py-2 text-[11px] text-zinc-400">
            {product.fabric_info && (
              <p className="line-clamp-1">
                <span className="text-zinc-500">Kumaş:</span>{" "}
                {product.fabric_info}
              </p>
            )}
            {product.ai_comment && (
              <p className="line-clamp-2 text-zinc-300">
                <span className="font-medium text-emerald-400">AI Yorum:</span>{" "}
                {product.ai_comment}
              </p>
            )}
          </div>
        )}

        {/* Direct Store Link */}
        <div className="mt-auto pt-1">
          <a
            href={product.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex w-full items-center justify-center gap-2 rounded-2xl bg-white px-4 py-2.5 text-xs font-semibold text-zinc-950 transition hover:bg-emerald-400 active:scale-[0.99]"
          >
            <span>Mağazaya Git</span>
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        </div>
      </div>
    </article>
  );
}
