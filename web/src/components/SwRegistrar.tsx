"use client";

import { useEffect } from "react";

export function SwRegistrar() {
  useEffect(() => {
    if (typeof window === "undefined" || !("serviceWorker" in navigator)) {
      return;
    }

    let cancelled = false;

    const registerAndUpdate = async () => {
      try {
        const reg = await navigator.serviceWorker.register("/sw.js", {
          scope: "/",
          updateViaCache: "none",
        });
        if (!cancelled) {
          void reg.update().catch(() => {});
        }
      } catch {
        // Ignore SW registration errors in unsupported/private contexts
      }
    };

    void registerAndUpdate();

    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        navigator.serviceWorker
          .getRegistration("/")
          .then((reg) => reg?.update())
          .catch(() => {});
      }
    };

    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, []);

  return null;
}
