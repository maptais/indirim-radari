import { NextRequest, NextResponse } from "next/server";

import { getSupabaseAdmin } from "@/lib/supabase-server";

export const dynamic = "force-dynamic";

function isValidKey(value: string | undefined): boolean {
  if (!value) return false;
  const trimmed = value.trim();
  return trimmed.length >= 32 && !trimmed.toLowerCase().startsWith("your-");
}

export async function GET(request: NextRequest) {
  try {
    const vapidPublicKey = (process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY || "").trim();
    const vapidPrivateKey = (process.env.VAPID_PRIVATE_KEY || "").trim();
    const configured = isValidKey(vapidPublicKey) && isValidKey(vapidPrivateKey);

    const supabase = getSupabaseAdmin();
    const { searchParams } = new URL(request.url);
    const endpointParam = (searchParams.get("endpoint") || "").trim();

    const { count, error: countError } = await supabase
      .from("push_subscriptions")
      .select("id", { count: "exact", head: true })
      .eq("is_active", true);

    if (countError) {
      return NextResponse.json(
        { error: "Abonelik durumu kontrol edilemedi.", details: countError.message },
        { status: 500 }
      );
    }

    let isEndpointSubscribed = false;
    if (endpointParam) {
      const { data: match } = await supabase
        .from("push_subscriptions")
        .select("id, is_active")
        .eq("endpoint", endpointParam)
        .eq("is_active", true)
        .maybeSingle();
      isEndpointSubscribed = Boolean(match);
    }

    return NextResponse.json({
      configured,
      vapidPublicKey: configured ? vapidPublicKey : null,
      activeCount: count ?? 0,
      isEndpointSubscribed,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Bilinmeyen sunucu hatası";
    return NextResponse.json(
      { error: "Bildirim durumu alınamadı.", details: message },
      { status: 500 }
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = (await request.json()) as {
      endpoint?: string;
      keys?: { p256dh?: string; auth?: string };
      deviceLabel?: string;
    };

    const endpoint = typeof body?.endpoint === "string" ? body.endpoint.trim() : "";
    const p256dh = typeof body?.keys?.p256dh === "string" ? body.keys.p256dh.trim() : "";
    const auth = typeof body?.keys?.auth === "string" ? body.keys.auth.trim() : "";
    const deviceLabel =
      typeof body?.deviceLabel === "string" ? body.deviceLabel.trim().slice(0, 80) : null;

    if (!endpoint.startsWith("https://") || !p256dh || !auth) {
      return NextResponse.json(
        { error: "Geçersiz Web Push abonelik anahtarları gönderildi." },
        { status: 400 }
      );
    }

    const userAgent = (request.headers.get("user-agent") || "").slice(0, 300) || null;

    const supabase = getSupabaseAdmin();
    const { data, error } = await supabase
      .from("push_subscriptions")
      .upsert(
        {
          endpoint,
          p256dh,
          auth,
          user_agent: userAgent,
          device_label: deviceLabel,
          is_active: true,
        },
        { onConflict: "endpoint" }
      )
      .select("id, endpoint, device_label, is_active, created_at, updated_at")
      .single();

    if (error) {
      return NextResponse.json(
        { error: "Bildirim aboneliği kaydedilemedi.", details: error.message },
        { status: 500 }
      );
    }

    return NextResponse.json(
      {
        message: "Bildirim aboneliğiniz başarıyla aktifleştirildi.",
        subscription: data,
      },
      { status: 201 }
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "Bilinmeyen sunucu hatası";
    return NextResponse.json(
      { error: "Abonelik kaydı sırasında hata oluştu.", details: message },
      { status: 500 }
    );
  }
}

export async function DELETE(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    let endpoint = (searchParams.get("endpoint") || "").trim();

    if (!endpoint) {
      try {
        const body = (await request.json()) as { endpoint?: string };
        endpoint = typeof body?.endpoint === "string" ? body.endpoint.trim() : "";
      } catch {
        // Ignore empty body
      }
    }

    if (!endpoint) {
      return NextResponse.json(
        { error: "Pasifleştirilecek abonelik endpoint bilgisi eksik." },
        { status: 400 }
      );
    }

    const supabase = getSupabaseAdmin();
    const { error } = await supabase
      .from("push_subscriptions")
      .update({ is_active: false })
      .eq("endpoint", endpoint);

    if (error) {
      return NextResponse.json(
        { error: "Abonelik kapatılamadı.", details: error.message },
        { status: 500 }
      );
    }

    return NextResponse.json({
      message: "Bu cihaz için bildirim aboneliği kapatıldı.",
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Bilinmeyen sunucu hatası";
    return NextResponse.json(
      { error: "Abonelik iptali sırasında hata oluştu.", details: message },
      { status: 500 }
    );
  }
}
