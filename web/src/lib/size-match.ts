import type { ProductCategory } from "@/types/database";

const ALPHA_SIZES = new Set([
  "XXS",
  "XS",
  "S",
  "M",
  "L",
  "XL",
  "XXL",
  "2XL",
  "3XL",
  "4XL",
  "OS",
  "ONE SIZE",
  "STD",
]);

function normalizeNumericFragment(value: string): Set<string> {
  const out = new Set<string>();
  let cleaned = value
    .trim()
    .replace(/^(?:EU|TR|UK|US|EUR|SIZE|BEDEN|NUMARA|NO)\s*[:\-]?\s*/i, "")
    .replace(/\s*[:\-]?\s*(?:EU|TR|EUR|NUMARA|NO)$/i, "")
    .trim();

  if (!cleaned) return out;

  cleaned = cleaned.replace(/(\d),(\d)/g, "$1.$2").replace(/\s+/g, " ").trim();

  if (/(\d+)(?:\s+2\/3|\s*⅔)(?!\d)/.test(cleaned)) {
    out.add(cleaned.replace(/(\d+)(?:\s+2\/3|\s*⅔)(?!\d)/g, "$1 2/3"));
    cleaned = cleaned.replace(/(\d+)(?:\s+2\/3|\s*⅔)(?!\d)/g, "$1.5");
  } else if (/(\d+)(?:\s+1\/3|\s*⅓)(?!\d)/.test(cleaned)) {
    out.add(cleaned.replace(/(\d+)(?:\s+1\/3|\s*⅓)(?!\d)/g, "$1 1/3"));
    cleaned = cleaned.replace(/(\d+)(?:\s+1\/3|\s*⅓)(?!\d)/g, "$1");
  }

  cleaned = cleaned
    .replace(/(\d+)(?:\s+1\/2|\s*½)(?!\d)/g, "$1.5")
    .replace(/\s+/g, " ")
    .trim();

  if (/^\d+\.0+$/.test(cleaned)) {
    cleaned = cleaned.split(".")[0];
  }

  out.add(cleaned);
  return out;
}

export function normalizeSizeTokens(
  rawSize: string,
  category: ProductCategory | string = "other"
): Set<string> {
  const text = (rawSize || "").trim().toUpperCase();
  if (!text) return new Set();

  const isShoes = category === "shoes";
  const base = text.replace(/\([^)]*\)/g, "").trim();
  const parenParts: string[] = [];
  const parenRe = /\(([^)]*)\)/g;
  let m: RegExpExecArray | null = parenRe.exec(text);
  while (m) {
    if (m[1]) parenParts.push(m[1]);
    m = parenRe.exec(text);
  }

  const candidates = [text, base, ...parenParts];
  const tokens = new Set<string>();

  for (const part of candidates) {
    const stripped = part.trim();
    if (!stripped) continue;

    if (stripped.includes("/") || stripped.includes("|")) {
      const segments = stripped
        .split(/[/|]/)
        .map((s) => s.trim())
        .filter(Boolean);
      const euSegments = segments.filter(
        (seg) => /^(?:EU|TR|EUR)\b/i.test(seg) || /\b(?:EU|TR|EUR)$/i.test(seg)
      );
      if (euSegments.length > 0) {
        for (const seg of euSegments) {
          for (const tok of normalizeNumericFragment(seg)) tokens.add(tok);
        }
        continue;
      }
    }

    const normalizedSet = normalizeNumericFragment(stripped);
    for (const tok of normalizedSet) tokens.add(tok);

    for (const cleaned of Array.from(normalizedSet)) {
      if (isShoes) {
        const rangeMatch = /^(\d{2}(?:\.5)?)\s*[/\-]\s*(\d{2}(?:\.5)?)$/.exec(
          cleaned
        );
        if (rangeMatch) {
          const low = Number(rangeMatch[1]);
          const high = Number(rangeMatch[2]);
          if (low >= 30 && low <= 52 && high >= 30 && high <= 52 && Math.abs(high - low) <= 2) {
            tokens.add(rangeMatch[1]);
            tokens.add(rangeMatch[2]);
            continue;
          }
        }
      }

      if (!isShoes) {
        const waistMatch = /^W?(\d{2,3})\s*[/\-xX]\s*L?\d{2,3}$/.exec(cleaned);
        if (waistMatch) {
          tokens.add(waistMatch[1]);
          continue;
        }
      }

      if (cleaned.includes("-") || cleaned.includes("/")) {
        const parts = cleaned
          .split(/[-/]/)
          .map((p) => p.trim())
          .filter(Boolean);
        if (parts.length > 0 && parts.every((p) => ALPHA_SIZES.has(p))) {
          for (const p of parts) tokens.add(p);
        }
      }
    }
  }

  if (isShoes) {
    const numeric = new Set(
      Array.from(tokens).filter((t) => /^\d+(?:\.5| [12]\/3)?$/.test(t))
    );
    if (numeric.size > 0) return numeric;
  }

  return tokens;
}

export function matchesUserSizes(
  availableSizes: string[],
  targetSizes: string[],
  category: ProductCategory | string = "other"
): boolean {
  if (!availableSizes.length || !targetSizes.length) return false;

  const targetTokens = new Set<string>();
  for (const target of targetSizes) {
    for (const tok of normalizeSizeTokens(target, category)) {
      targetTokens.add(tok);
    }
  }
  if (targetTokens.size === 0) return false;

  for (const size of availableSizes) {
    const sizeTokens = normalizeSizeTokens(size, category);
    for (const tok of sizeTokens) {
      if (targetTokens.has(tok)) return true;
    }
  }
  return false;
}
