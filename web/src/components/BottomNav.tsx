"use client";

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
];

export function BottomNav() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Ana gezinme"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-white/10 bg-[#09090b]/85 backdrop-blur-xl"
    >
      <div className="mx-auto flex max-w-md items-center justify-around px-4 pt-2 pb-safe">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const isActive =
            href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={`group flex flex-1 flex-col items-center gap-1 rounded-2xl py-1.5 transition-colors ${
                isActive
                  ? "text-emerald-400"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              <span
                className={`flex h-8 w-14 items-center justify-center rounded-full transition-colors ${
                  isActive
                    ? "bg-emerald-500/15 text-emerald-400"
                    : "bg-transparent group-hover:bg-white/5"
                }`}
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
