# İndirim Radarı — Kişisel iOS PWA & Akıllı İndirim Takip Sistemi

## 1. Proje Özeti
Kullanıcının kendi giyim bedenine, ayakkabı numarasına, kumaş/kalıp tercihlerine ve kategori bazlı fiyat eşiklerine göre 22 markayı otomatik tarayan, sahte indirimleri fiyat hafızasıyla eleyen, ürünleri AI ile tarz açısından puanlayan ve iPhone'a Web Push bildirimi gönderen sıfır maliyetli (Serverless) kişisel PWA uygulaması.

> **Kapsam Dışı:** Tinder tarzı kaydırmalı hızlı eleme ekranı yapılmayacaktır.

---

## 2. Teknoloji Yığını ve Mimari (Ücretsiz Katmanlar)
- **Frontend (iOS PWA):** Next.js (App Router), TypeScript, Tailwind CSS, Web Push API (VAPID) — **Vercel**
- **Veritabanı:** PostgreSQL — **Supabase**
- **Tarayıcı Motoru (Scraper):** Python 3.11+, `curl_cffi` (TLS parmak izi taklidi), `supabase`, `pywebpush`, `google-genai`
- **Zamanlayıcı (Cron):** **GitHub Actions** (2 saatte bir otomatik tarama)
- **AI Puanlama:** **Gemini API** (ürün başlığı/görseli üzerinden 1-10 tarz puanı)

---

## 3. Çekirdek Özellikler
1. **Fiyat Hafızası (Sahte İndirim Dedektörü):** Her ürünün fiyat geçmişi saklanır. Eski fiyata kıyasla gerçekten son 30-60 günün en düşük seviyesinde olan ürünler öne çıkarılır; yapay şişirilmiş indirimler işaretlenir veya elenir.
2. **Tam Beden & Kritik Stok Kontrolü:** Yalnızca kullanıcının üst, alt veya ayakkabı numarası stoktaysa kayıt ve bildirim üretilir. "Son birkaç ürün" bilgisi API'den geliyorsa kritik stok rozeti eklenir.
3. **Negatif Filtre & Kumaş/Kalıp Kuralları:** İstenmeyen anahtar kelimeler (ör. `skinny`, `baskılı`, `logolu`) ve kumaş kuralları (ör. polyester oranı üst sınırı) ayarlar ekranından yönetilir ve tarama sırasında elenir.
4. **Kategori Bazlı İndirim ve Bütçe Eşiği:** Ayakkabı, üst giyim, alt giyim ve dış giyim için ayrı minimum indirim yüzdesi ve maksimum TL fiyatı tanımlanır.
5. **AI Tarz Puanlayıcı:** Filtreleri geçen ürünler Gemini API ile kullanıcının tarz profiline göre 1-10 arası puanlanır. Sadece belirlenen puan eşiğinin üstündeki ürünler için iPhone'a bildirim gönderilir.

---

## 4. Hedef Markalar (22 Mağaza — Ortak Motor Grupları)
- **Inditex Motoru (5):** Zara, Pull&Bear, Bershka, Lefties, Massimo Dutti
- **Eren Perakende Motoru (3):** SuperStep, Occasion, FashFed
- **Mango Motoru (2):** Mango Man, Mango Outlet
- **Trendyol Motoru (2):** Trendyol Milla, Trendyol Man
- **Bağımsız Giyim (4):** H&M, DeFacto, Mavi, Jack & Jones
- **Bağımsız Ayakkabı & Resmi Outlet (6):** In Street, Sneaks Up, Barçın, Sportive, Adidas Outlet, Puma Outlet

---

## 5. Monorepo Klasör Yapısı

    indirim-radari/
    ├── PROJECT_PLAN.md
    ├── .gitignore
    ├── .github/
    │   └── workflows/
    │       └── scrape.yml
    ├── supabase/
    │   ├── schema.sql
    │   └── migrations/
    │       └── 001_add_gender_preference.sql
    ├── scraper/
    │   ├── requirements.txt
    │   ├── .env.example
    │   ├── main.py
    │   ├── run_local.ps1
    │   ├── run_local.bat
    │   ├── core/
    │   │   ├── config.py
    │   │   ├── db.py
    │   │   ├── filters.py
    │   │   ├── price_analyzer.py
    │   │   ├── ai_scorer.py
    │   │   └── notifier.py
    │   └── engines/
    │       ├── base.py
    │       ├── inditex.py
    │       ├── eren.py
    │       ├── mango.py
    │       ├── trendyol.py
    │       └── independents/
    └── web/
        ├── package.json
        ├── .env.example
        ├── public/
        │   ├── manifest.json
        │   └── sw.js
        └── src/

---

## 6. Geliştirme Fazları (To-Do Listesi)

### Faz 1: Proje İskeleti ve Veritabanı Altyapısı
- [x] Monorepo klasör yapısını (`web/`, `scraper/`, `supabase/`, `.github/workflows/`) oluştur.
- [x] Kök dizin için `.gitignore`, `scraper/.env.example` ve `web/.env.example` dosyalarını hazırla.
- [x] `supabase/schema.sql` dosyasını yaz:
  - `products` (marka, dış ürün ID, başlık, kategori, link, görsel, mevcut fiyat, liste fiyatı, indirim oranı, stoktaki bedenler, kritik stok bayrağı, kumaş bilgisi, AI puanı, AI yorumu, son görülme tarihi)
  - `price_history` (product_id, fiyat, tarih — sahte indirim tespiti için)
  - `user_settings` (üst/alt/ayakkabı bedenleri, negatif kelimeler, kumaş kuralları, kategori bazlı min indirim % ve max TL limitleri, tarz profili metni, min AI bildirim puanı)
  - `push_subscriptions` (iOS Web Push endpoint, p256dh, auth anahtarları)
  - `scrape_logs` (marka, çalışma zamanı, durum, çekilen ürün sayısı, hata mesajı)
- [x] `user_settings` tablosu için örnek başlangıç kaydı (seed) ekle.
- [x] `scraper/requirements.txt` ile temel `scraper/core/config.py` ve `scraper/core/db.py` modüllerini oluştur.
- [x] `scraper/engines/base.py` içinde tüm markaların uyacağı standart `ProductItem` veri şemasını ve `BaseScraper` soyut sınıfını yaz.

### Faz 2: İlk Tarayıcı Motoru — Inditex 5'lisi ve Filtre Çekirdeği (MVP)
- [x] `scraper/engines/inditex.py` ortak motorunu `curl_cffi` kullanarak yaz (Zara, Pull&Bear, Bershka, Lefties, Massimo Dutti konfigürasyonlarıyla).
- [x] `scraper/core/filters.py` modülünü yaz (beden eşleşmesi, negatif kelime elemesi, kumaş/polyester kuralı, kategori bazlı indirim/fiyat eşiği).
- [x] `scraper/core/price_analyzer.py` modülünü yaz (ürünün geçmiş en düşük fiyatıyla karşılaştırma, `is_real_discount` ve `lowest_price_days` hesaplama).
- [x] `scraper/main.py` ile tek marka (ör. Pull&Bear) için lokal uçtan uca tarama ve Supabase'e kayıt testini yap.
- [x] Bot koruması/rate-limit için rastgele bekleme (jitter), tarayıcı impersonate ve hata toleransı ekle.

### Faz 3: iOS PWA Arayüzü ve Web Push Bildirimleri
- [x] `web/` altında Next.js (App Router + TypeScript + Tailwind CSS) uygulamasını kur.
- [x] iOS Safari "Ana Ekrana Ekle" tam uyumu için `manifest.json`, Apple touch ikonları, `apple-mobile-web-app-capable` meta etiketleri ve `public/sw.js` Service Worker'ı yapılandır.
- [x] VAPID anahtar yapısını kur; arayüzde "Bildirimleri Aç" butonu ile iPhone Web Push aboneliğini Supabase `push_subscriptions` tablosuna kaydeden API rotasını yaz.
- [x] **Ana Ekran (İndirim Akışı):** Ürün kartları (görsel, marka, eski/yeni fiyat, indirim %, beden/kritik stok rozeti, "Son X günün en düşüğü" etiketi, AI tarz puanı, doğrudan ürün linki) ve marka/kategori filtre çubuğunu yap.
- [x] **Ayarlar Ekranı:** Bedenler, negatif kelimeler, kumaş kuralları, kategori bazlı TL/% eşikleri ve AI tarz profili metnini Supabase `user_settings` üzerinden okuyup güncelleyen formu yap.
- [x] `scraper/core/notifier.py` modülünü (`pywebpush`) yaz ve yeni gerçek indirim yakalandığında iPhone'a test bildirimi gönder.

### Faz 4: AI Tarz Puanlayıcı ve Tam Otomasyon
- [x] `scraper/core/ai_scorer.py` modülünü Gemini API ile yaz (ürün başlığı + görsel URL/verisi + kullanıcının tarz profili -> 1-10 puan ve tek cümlelik gerekçe).
- [x] AI puanlamasını yalnızca beden, negatif filtre, eşik ve gerçek indirim kontrolünden geçen yeni ürünlerde çalıştır.
- [x] `.github/workflows/scrape.yml` dosyasını yaz (2 saatte bir çalışan cron + manuel tetikleme `workflow_dispatch`, GitHub Secrets entegrasyonu).
- [x] Web uygulamasını Vercel'e deploy et (`web/` canlı ortamda yayında, GitHub Actions `workflow_dispatch` doğrulandı).
- [ ] iPhone Safari'den ana ekrana ekleyip canlı uçtan uca Web Push bildirim testini doğrula.

### Faz 5: Ayakkabı & Outlet Paketi (9 Marka)
- [x] `scraper/engines/eren.py` ortak motorunu yaz (SuperStep, Occasion, FashFed).
- [x] In Street ve Sneaks Up tarayıcı modüllerini ekle.
- [x] Barçın ve Sportive tarayıcı modüllerini ekle.
- [x] Adidas Outlet ve Puma Outlet tarayıcı modüllerini ekle.
- [x] Tüm ayakkabı modüllerini ayakkabı numarası ve bütçe eşikleriyle test et.

### Faz 6: Kalan Giyim Markaları (8 Marka) ve Dayanıklılık
- [x] `scraper/engines/mango.py` ortak motorunu yaz (Mango Man, Mango Outlet).
- [x] `scraper/engines/trendyol.py` ortak motorunu yaz (Trendyol Milla, Trendyol Man).
- [x] Bağımsız giyim modüllerini ekle: H&M, DeFacto, Mavi, Jack & Jones.
- [x] Veri merkezi IP'sini sert bloklayan (Cloudflare/Akamai) markalar tespit edilirse `scrape_logs` üzerinden raporla ve lokal çalıştırma / alternatif çekim bayrağı (`run_mode: cloud | local`) ekle.
- [x] Arayüze son tarama durumlarını gösteren mini "Sistem Durumu" sekmesi ekle.

### Faz 7: Dinamik Cinsiyet Tercihi, Cloud Marka Onarımları ve Yerel Cron
- [x] Veritabanı şemasını ve migration dosyasını (`supabase/migrations/001_add_gender_preference.sql`) `products.gender` ve `user_settings.target_genders` sütunlarıyla güncelle.
- [x] 22 marka motorunun tamamına `gender` (`men`/`women`/`unisex`) etiketi, çocuk/bebek elemesi ve `user_settings.target_genders` bazlı dinamik marka/kategori filtrelemesi ekle (`--gender` CLI desteğiyle).
- [x] Web arayüzünde `/settings` ve `/api/settings` içine çoklu Cinsiyet Tercihi seçicisi, `/api/products` ve ana sayfaya hızlı cinsiyet geçiş filtreleri (`Ayarlarım / Tümü / Erkek / Kadın / Unisex`) ekle.
- [x] Eksik 12 cloud markayı tek tek inceleyip onar (Zara PDP AJAX beden/kumaş çekimi, Bershka `store_id=44109521`, Mango Man `previousPrices` dict şeması & 429 fallback, Mavi `:sale` sıralaması, Jack & Jones `?sorting=highest_discount` + sayfalama, Sneaks Up `?pageIndex` sayfalama + `/sneaks-out`, SuperStep `/indirim/` + `default` sıralama, Puma Outlet tekilleştirme, Trendyol WAF/PDP dayanıklılığı, H&M `deals/deal` + `sale/view-all` uyumu).
- [x] Yerel WAF korumalı markalar (`adidasoutlet` & `mangooutlet`) için Unicode/ASCII-dışı yol uyumlu Windows Task Scheduler çalıştırma betiklerini (`scraper/run_local.ps1`, `scraper/run_local.bat`) oluştur.

---

## 7. Antigravity İçin Çalışma Kuralları
1. **Faz Sırasına Sadık Kal:** Her seferinde yalnızca istenen fazı uygula; kullanıcı onayı olmadan sonraki faza geçme.
2. **İlerleme Takibi:** Bir görevi eksiksiz tamamlayıp doğruladığında bu dosyadaki ilgili `[ ]` kutucuğunu `[x]` olarak güncelle.
3. **Güvenlik:** Supabase URL/Key, Gemini API Key ve VAPID anahtarlarını asla kaynak koda gömme; her zaman ortam değişkenlerinden (`.env`) oku.
4. **Temiz ve Modüler Kod:** Her marka motoru `BaseScraper` arayüzünü uygulamalı ve tek bir markadaki hata diğer markaların taranmasını durdurmamalıdır (`try/except` + `scrape_logs`).
5. **Türkçe Arayüz, İngilizce Kod:** Kod, değişken ve veritabanı isimleri İngilizce; kullanıcı arayüzü ve bildirim metinleri Türkçe olmalıdır.
6. **Otomatik Devir Promptu:** Her fazı tamamladığında, kullanıcının bir sonraki fazı sıfır bağlamlı yeni bir sohbette başlatabilmesi için çıktının en sonuna mutlaka kopyalanabilir kod bloğu içinde hazır 'Sonraki Faz Başlangıç Promptu' ekle. Bu prompt; tamamlanan kritik dosya/mimari özetini, bir sonraki fazın hedeflerini, bilinen teknik notları ve bir sonraki faz sonunda da devir promptu üretme talimatını içermelidir.