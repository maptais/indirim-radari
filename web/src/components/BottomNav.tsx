"use client";

import { useEffect, useState, type MouseEvent } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Flame, SlidersHorizontal } from "lucide-react";

const NAV_ITEMS = [
  {
    href: "/",
    label: "İndirim Akışı",
    icon: Flame,
  },
  {
    href: "/status",
    label: "Sistem Durumu",
    icon: Activity,
  },
  {
    href: "/settings",
    label: "Ayarlar",
    icon: SlidersHorizontal,
  },
] as const;

function matchesHref(path: string, href: string): boolean {
  return href === "/" ? path === "/" : path.startsWith(href);
}

export function BottomNav() {
  const pathname = usePathname();
  const [pendingHref, setPendingHref] = useState<string | null>(null);

  useEffect(() => {
    setPendingHref(null);
  }, [pathname]);

  useEffect(() => {
    if (!pendingHref) return;
    const timer = window.setTimeout(() => setPendingHref(null), 6000);
    return () => window.clearTimeout(timer);
  }, [pendingHref]);

  const effectivePath = pendingHref ?? pathname;

  const handleTabClick = (
    event: MouseEvent<HTMLAnchorElement>,
    href: string
  ) => {
    if (
      event.defaultPrevented ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      event.button !== 0
    ) {
      return;
    }
    if (matchesHref(pathname, href)) {
      setPendingHref(null);
      return;
    }
    setPendingHref(href);
  };

  return (
    <nav
      aria-label="Ana gezinme"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-white/10 bg-[#09090b]/85 backdrop-blur-xl"
    >
      <div className="mx-auto flex max-w-md items-center justify-around px-4 pt-2 pb-safe">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const isActive = matchesHref(effectivePath, href);
          const isTransitioning =
            pendingHref === href && !matchesHref(pathname, href);

          return (
            <Link
              key={href}
              href={href}
              onClick={(event) => handleTabClick(event, href)}
              aria-current={isActive ? "page" : undefined}
              className={`group flex flex-1 flex-col items-center gap-1 rounded-2xl py-1.5 transition-colors duration-150 ${
                isActive
                  ? "text-emerald-400"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              <span
                className={`flex h-8 w-14 items-center justify-center rounded-full transition-all duration-150 ${
                  isActive
                    ? "bg-emerald-500/15 text-emerald-400"
                    : "bg-transparent group-hover:bg-white/5"
                } ${isTransitioning ? "scale-95 animate-pulse" : ""}`}
              >
                <Icon className="h-5 w-5" strokeWidth={isActive ? 2.2 : 1.8} />
              </span>
              <span className="text-[11px] font-medium tracking-tight">
                {label}
              </span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
