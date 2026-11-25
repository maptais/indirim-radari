import type { Metadata, Viewport } from "next";

import { BottomNav } from "@/components/BottomNav";
import { SwRegistrar } from "@/components/SwRegistrar";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "İndirim Radarı",
    template: "%s · İndirim Radarı",
  },
  description:
    "Kişisel beden, kumaş ve fiyat hafızasıyla gerçek indirimleri yakalayan akıllı iOS PWA takip sistemi.",
  manifest: "/manifest.json",
  applicationName: "İndirim Radarı",
  robots: {
    index: false,
    follow: false,
  },
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "İndirim Radarı",
  },
  formatDetection: {
    telephone: false,
  },
  icons: {
    icon: [
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: [
      { url: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" },
    ],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: "cover",
  themeColor: "#09090b",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="tr" className="dark">
      <head>
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta
          name="apple-mobile-web-app-status-bar-style"
          content="black-translucent"
        />
        <meta name="apple-mobile-web-app-title" content="İndirim Radarı" />
        <meta name="mobile-web-app-capable" content="yes" />
      </head>
      <body className="min-h-dvh bg-[#09090b] text-zinc-100 antialiased selection:bg-emerald-500/30 selection:text-emerald-100">
        <SwRegistrar />
        <div className="mx-auto flex min-h-dvh w-full max-w-5xl flex-col">
          <main className="flex-1 pb-nav-safe">{children}</main>
          <BottomNav />
        </div>
      </body>
    </html>
  );
}
