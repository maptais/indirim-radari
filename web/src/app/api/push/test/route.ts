import { NextRequest, NextResponse } from "next/server";
import webpush from "web-push";

import { getSupabaseAdmin } from "@/lib/supabase-server";

export const dynamic = "force-dynamic";

function isValidKey(value: string | undefined): boolean {
  if (!value) return false;
  const trimmed = value.trim();
  return trimmed.length >= 32 && !trimmed.toLowerCase().startsWith("your-");
}

export async function POST(request: NextRequest) {
  try {
    const publicKey = (process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY || "").trim();
    const privateKey = (process.env.VAPID_PRIVATE_KEY || "").trim();
    const subject =
      (process.env.VAPID_SUBJECT || "mailto:indirim-radari@example.com").trim();

    if (!isValidKey(publicKey) || !isValidKey(privateKey)) {
      return NextResponse.json(
        { error: "Sunucu VAPID anahtarları yapılandırılmamış." },
        { status: 503 }
      );
    }

    let targetEndpoint = "";
    try {
      const body = (await request.json()) as { endpoint?: string };
      targetEndpoint = typeof body?.endpoint === "string" ? body.endpoint.trim() : "";
    } catch {
      // Optional JSON body
    }

    webpush.setVapidDetails(subject, publicKey, privateKey);

    const supabase = getSupabaseAdmin();
    let query = supabase
      .from("push_subscriptions")
      .select("id, endpoint, p256dh, auth")
      .eq("is_active", true);

    if (targetEndpoint) {
      query = query.eq("endpoint", targetEndpoint);
    }

    const { data: subscriptions, error } = await query;

    if (error) {
      return NextResponse.json(
        { error: "Aktif abonelikler okunamadı.", details: error.message },
        { status: 500 }
      );
    }

    if (!subscriptions || subscriptions.length === 0) {
      return NextResponse.json(
        { error: "Aktif bildirim aboneliği bulunamadı. Önce bildirimleri açın." },
        { status: 404 }
      );
    }

    const payload = JSON.stringify({
      title: "İndirim Radarı · Test Bildirimi",
      body: "Bildirim bağlantınız hazır! Gerçek bir indirim yakalandığında anında haber vereceğiz.",
      url: "/",
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      tag: "indirim-radari-test",
    });

    let sent = 0;
    let deactivated = 0;
    let failed = 0;

    for (const sub of subscriptions) {
      try {
        await webpush.sendNotification(
          {
            endpoint: sub.endpoint,
            keys: {
              p256dh: sub.p256dh,
              auth: sub.auth,
            },
          },
          payload,
          { TTL: 3600 }
        );
        sent += 1;
        await supabase
          .from("push_subscriptions")
          .update({ last_notified_at: new Date().toISOString() })
          .eq("id", sub.id);
      } catch (err) {
        const statusCode =
          typeof err === "object" && err !== null && "statusCode" in err
            ? Number((err as { statusCode?: number }).statusCode)
            : null;
        if (statusCode === 404 || statusCode === 410) {
          deactivated += 1;
          await supabase
            .from("push_subscriptions")
            .update({ is_active: false })
            .eq("id", sub.id);
        } else {
          failed += 1;
        }
      }
    }

    return NextResponse.json({
      message:
        sent > 0
          ? `Test bildirimi ${sent} cihaza gönderildi.`
          : "Test bildirimi gönderilemedi.",
      sent,
      deactivated,
      failed,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Bilinmeyen sunucu hatası";
    return NextResponse.json(
      { error: "Test bildirimi gönderilirken hata oluştu.", details: message },
      { status: 500 }
    );
  }
}
