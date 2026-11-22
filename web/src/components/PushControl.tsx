"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Bell,
  BellOff,
  BellRing,
  CheckCircle2,
  Loader2,
  Send,
  Share,
  Smartphone,
} from "lucide-react";

import {
  detectDeviceLabel,
  getActivePushSubscription,
  isIosDevice,
  isStandalonePwa,
  isWebPushSupported,
  subscribeDeviceToPush,
} from "@/lib/push-client";

interface PushControlProps {
  variant?: "compact" | "card";
}

type PushUiState =
  | "loading"
  | "ios-install-required"
  | "unsupported"
  | "unconfigured"
  | "denied"
  | "unsubscribed"
  | "subscribed";

export function PushControl({ variant = "compact" }: PushControlProps) {
  const [state, setState] = useState<PushUiState>("loading");
  const [vapidPublicKey, setVapidPublicKey] = useState<string | null>(null);
  const [activeCount, setActiveCount] = useState<number>(0);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{
    type: "success" | "error" | "info";
    text: string;
  } | null>(null);

  const syncPushState = useCallback(async () => {
    try {
      const ios = isIosDevice();
      const standalone = isStandalonePwa();

      if (ios && !standalone) {
        setState("ios-install-required");
        return;
      }

      if (!isWebPushSupported()) {
        setState("unsupported");
        return;
      }

      const existingSub = await getActivePushSubscription();
      const endpointQuery = existingSub
        ? `?endpoint=${encodeURIComponent(existingSub.endpoint)}`
        : "";
      const res = await fetch(`/api/push/subscribe${endpointQuery}`, {
        cache: "no-store",
      });
      const data = await res.json();

      setActiveCount(Number(data.activeCount || 0));
      setVapidPublicKey(data.vapidPublicKey || null);

      if (!data.configured || !data.vapidPublicKey) {
        setState("unconfigured");
        return;
      }

      if (Notification.permission === "denied") {
        setState("denied");
        return;
      }

      if (existingSub && data.isEndpointSubscribed) {
        setState("subscribed");
      } else {
        setState("unsubscribed");
      }
    } catch {
      setState("unsupported");
    }
  }, []);

  useEffect(() => {
    void syncPushState();
  }, [syncPushState]);

  const handleEnablePush = async () => {
    if (!vapidPublicKey) return;
    setBusy(true);
    setFeedback(null);

    try {
      const subscription = await subscribeDeviceToPush(vapidPublicKey);
      const subJson = subscription.toJSON();

      const res = await fetch("/api/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint: subJson.endpoint,
          keys: subJson.keys,
          deviceLabel: detectDeviceLabel(),
        }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || "Abonelik kaydedilemedi.");
      }

      setState("subscribed");
      setActiveCount((prev) => Math.max(1, prev + 1));
      setFeedback({
        type: "success",
        text: "Bildirimler açıldı! Gerçek indirimler anında bu cihaza gelecek.",
      });
    } catch (error) {
      const text =
        error instanceof Error ? error.message : "Bildirim açılamadı.";
      setFeedback({ type: "error", text });
      if (typeof Notification !== "undefined" && Notification.permission === "denied") {
        setState("denied");
      }
    } finally {
      setBusy(false);
    }
  };

  const handleDisablePush = async () => {
    setBusy(true);
    setFeedback(null);
    try {
      const existingSub = await getActivePushSubscription();
      if (existingSub) {
        await fetch("/api/push/subscribe", {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ endpoint: existingSub.endpoint }),
        });
        await existingSub.unsubscribe();
      }
      setState("unsubscribed");
      setActiveCount((prev) => Math.max(0, prev - 1));
      setFeedback({
        type: "info",
        text: "Bu cihaz için bildirimler kapatıldı.",
      });
    } catch (error) {
      setFeedback({
        type: "error",
        text: error instanceof Error ? error.message : "Bildirim kapatılamadı.",
      });
    } finally {
      setBusy(false);
    }
  };

  const handleSendTest = async () => {
    setBusy(true);
    setFeedback(null);
    try {
      const existingSub = await getActivePushSubscription();
      const res = await fetch("/api/push/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint: existingSub?.endpoint || undefined,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || "Test bildirimi gönderilemedi.");
      }
      setFeedback({
        type: "success",
        text: data.message || "Test bildirimi gönderildi.",
      });
    } catch (error) {
      setFeedback({
        type: "error",
        text: error instanceof Error ? error.message : "Test başarısız oldu.",
      });
    } finally {
      setBusy(false);
    }
  };

  if (variant === "compact") {
    if (state === "loading") {
      return (
        <div className="inline-flex h-9 items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3 text-xs text-zinc-400">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          <span>Kontrol</span>
        </div>
      );
    }

    if (state === "subscribed") {
      return (
        <button
          type="button"
          onClick={handleSendTest}
          disabled={busy}
          title="Test bildirimi gönder"
          className="inline-flex h-9 items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/15 px-3 text-xs font-medium text-emerald-300 transition hover:bg-emerald-500/25 disabled:opacity-60"
        >
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <BellRing className="h-3.5 w-3.5" />
          )}
          <span>Bildirimler Açık</span>
        </button>
      );
    }

    if (state === "ios-install-required") {
      return (
        <a
          href="/settings#bildirimler"
          className="inline-flex h-9 items-center gap-1.5 rounded-full border border-amber-500/30 bg-amber-500/10 px-3 text-xs font-medium text-amber-300 transition hover:bg-amber-500/20"
        >
          <Smartphone className="h-3.5 w-3.5" />
          <span>Ana Ekrana Ekle</span>
        </a>
      );
    }

    if (state === "unsubscribed") {
      return (
        <button
          type="button"
          onClick={handleEnablePush}
          disabled={busy}
          className="inline-flex h-9 items-center gap-1.5 rounded-full bg-emerald-500 px-3.5 text-xs font-semibold text-zinc-950 shadow-sm shadow-emerald-500/20 transition hover:bg-emerald-400 active:scale-[0.98] disabled:opacity-60"
        >
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Bell className="h-3.5 w-3.5" />
          )}
          <span>Bildirimleri Aç</span>
        </button>
      );
    }

    return (
      <a
        href="/settings#bildirimler"
        className="inline-flex h-9 items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3 text-xs font-medium text-zinc-300 transition hover:bg-white/10"
      >
        <BellOff className="h-3.5 w-3.5 text-zinc-400" />
        <span>Bildirim Ayarı</span>
      </a>
    );
  }

  return (
    <section
      id="bildirimler"
      className="rounded-3xl border border-white/10 bg-zinc-900/70 p-5 shadow-lg shadow-black/20"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <BellRing className="h-4 w-4 text-emerald-400" />
            <h2 className="text-base font-semibold text-zinc-100">
              iPhone & Web Push Bildirimleri
            </h2>
          </div>
          <p className="text-xs leading-relaxed text-zinc-400">
            Gerçek indirim yakalandığında anında bildirim almak için bu cihazı
            abone edin. Aktif kayıtlı cihaz: <strong className="text-zinc-200">{activeCount}</strong>
          </p>
        </div>
        {state === "subscribed" && (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-500/15 px-2.5 py-1 text-[11px] font-medium text-emerald-300">
            <CheckCircle2 className="h-3.5 w-3.5" />
            Aktif
          </span>
        )}
      </div>

      <div className="mt-4">
        {state === "loading" && (
          <div className="flex items-center gap-2 text-xs text-zinc-400">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>Cihaz bildirim durumu kontrol ediliyor...</span>
          </div>
        )}

        {state === "ios-install-required" && (
          <div className="rounded-2xl border border-amber-500/25 bg-amber-500/10 p-3.5 text-xs leading-relaxed text-amber-200">
            <div className="mb-1.5 flex items-center gap-2 font-semibold text-amber-300">
              <Share className="h-4 w-4" />
              <span>iPhone&apos;da Bildirimler İçin Ana Ekrana Ekleyin</span>
            </div>
            iOS Safari&apos;de Web Push bildirimleri yalnızca uygulama ana ekrana
            eklendiğinde çalışır. Alt çubuktaki <strong>Paylaş</strong> simgesine
            dokunun, <strong>&ldquo;Ana Ekrana Ekle&rdquo;</strong> seçeneğini
            seçin ve uygulamayı ana ekrandan açıp <strong>Bildirimleri Aç</strong>{" "}
            butonuna dokunun.
          </div>
        )}

        {state === "unsupported" && (
          <p className="rounded-2xl border border-white/10 bg-zinc-950/60 p-3 text-xs text-zinc-400">
            Bu tarayıcı ortamında Web Push API desteklenmiyor. iOS 16.4+ Safari
            (Ana Ekrana Ekle) veya güncel Chrome/Edge kullanın.
          </p>
        )}

        {state === "unconfigured" && (
          <p className="rounded-2xl border border-rose-500/25 bg-rose-500/10 p-3 text-xs text-rose-200">
            Sunucuda VAPID anahtarları tanımlı değil. <code>.env.local</code>{" "}
            dosyasında <code>NEXT_PUBLIC_VAPID_PUBLIC_KEY</code> ve{" "}
            <code>VAPID_PRIVATE_KEY</code> değerlerini kontrol edin.
          </p>
        )}

        {state === "denied" && (
          <p className="rounded-2xl border border-rose-500/25 bg-rose-500/10 p-3 text-xs text-rose-200">
            Tarayıcı veya iOS ayarlarında bildirim izni reddedilmiş. Cihaz{" "}
            <strong>Ayarlar → Bildirimler → İndirim Radarı</strong> bölümünden
            izin verdikten sonra sayfayı yenileyin.
          </p>
        )}

        {state === "unsubscribed" && (
          <button
            type="button"
            onClick={handleEnablePush}
            disabled={busy}
            className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-emerald-500 px-4 py-3 text-sm font-semibold text-zinc-950 shadow-md shadow-emerald-500/20 transition hover:bg-emerald-400 active:scale-[0.99] disabled:opacity-60 sm:w-auto"
          >
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Bell className="h-4 w-4" />
            )}
            <span>Bildirimleri Aç</span>
          </button>
        )}

        {state === "subscribed" && (
          <div className="flex flex-wrap items-center gap-2.5">
            <button
              type="button"
              onClick={handleSendTest}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-2xl bg-emerald-500/15 px-4 py-2.5 text-xs font-semibold text-emerald-300 ring-1 ring-emerald-500/30 transition hover:bg-emerald-500/25 disabled:opacity-60"
            >
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Send className="h-3.5 w-3.5" />
              )}
              <span>Test Bildirimi Gönder</span>
            </button>
            <button
              type="button"
              onClick={handleDisablePush}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-2xl bg-white/5 px-4 py-2.5 text-xs font-medium text-zinc-300 ring-1 ring-white/10 transition hover:bg-white/10 disabled:opacity-60"
            >
              <BellOff className="h-3.5 w-3.5" />
              <span>Bu Cihazda Kapat</span>
            </button>
          </div>
        )}

        {feedback && (
          <p
            className={`mt-3 rounded-xl px-3 py-2 text-xs ${
              feedback.type === "success"
                ? "bg-emerald-500/10 text-emerald-300 ring-1 ring-emerald-500/25"
                : feedback.type === "error"
                  ? "bg-rose-500/10 text-rose-300 ring-1 ring-rose-500/25"
                  : "bg-white/5 text-zinc-300 ring-1 ring-white/10"
            }`}
          >
            {feedback.text}
          </p>
        )}
      </div>
    </section>
  );
}
