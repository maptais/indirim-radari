"use client";

export function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const buffer = new ArrayBuffer(rawData.length);
  const outputArray = new Uint8Array(buffer);
  for (let i = 0; i < rawData.length; i += 1) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

export function isIosDevice(): boolean {
  if (typeof window === "undefined") return false;
  const ua = window.navigator.userAgent || "";
  const isClassicIos = /iPad|iPhone|iPod/.test(ua);
  const isIpadOs =
    window.navigator.platform === "MacIntel" &&
    typeof window.navigator.maxTouchPoints === "number" &&
    window.navigator.maxTouchPoints > 1;
  return isClassicIos || isIpadOs;
}

export function isStandalonePwa(): boolean {
  if (typeof window === "undefined") return false;
  const nav = window.navigator as Navigator & { standalone?: boolean };
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    Boolean(nav.standalone)
  );
}

export function isWebPushSupported(): boolean {
  if (typeof window === "undefined") return false;
  return (
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

export function detectDeviceLabel(): string {
  if (typeof window === "undefined") return "Bilinmeyen Cihaz";
  const ua = window.navigator.userAgent || "";
  if (/iPhone/.test(ua)) return "iPhone PWA";
  if (/iPad/.test(ua)) return "iPad PWA";
  if (/Android/.test(ua)) return "Android PWA";
  if (/Macintosh/.test(ua)) return "macOS Tarayıcı";
  if (/Windows/.test(ua)) return "Windows Tarayıcı";
  return "Web Tarayıcı";
}

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration> {
  if (!("serviceWorker" in navigator)) {
    throw new Error("Tarayıcınız Service Worker desteklemiyor.");
  }
  const registration = await navigator.serviceWorker.register("/sw.js", {
    scope: "/",
    updateViaCache: "none",
  });
  await navigator.serviceWorker.ready;
  return registration;
}

export async function getActivePushSubscription(): Promise<PushSubscription | null> {
  if (!isWebPushSupported()) return null;
  const registration = await navigator.serviceWorker.getRegistration("/");
  if (!registration) return null;
  return registration.pushManager.getSubscription();
}

export async function subscribeDeviceToPush(
  vapidPublicKey: string
): Promise<PushSubscription> {
  if (!isWebPushSupported()) {
    throw new Error("Bu tarayıcıda Web Push bildirimleri desteklenmiyor.");
  }

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error(
      permission === "denied"
        ? "Bildirim izni reddedildi. Cihaz ayarlarından İndirim Radarı bildirimlerine izin verin."
        : "Bildirim izni verilmedi."
    );
  }

  const registration = await registerServiceWorker();
  const existing = await registration.pushManager.getSubscription();
  if (existing) {
    return existing;
  }

  return registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
  });
}
