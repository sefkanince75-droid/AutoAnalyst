# AutoAnalyst tamamlanma raporu

Tarih: **8 Kasım 2026** · Sürüm: **2.0.0rc1**

## Sonuç

Önceki A/B ve C01–C03 çalışmalarının üzerine, kalan C04–C05, D01–D05 ve E01–E04 geliştirmeleri sırayla uygulandı. Yerelde yapılabilen kod, rehber, ölçüm ve paket doğrulamaları tamamlandı. Gerçek katılımcı pilotu, bağımsız Windows kabulü ve ürün sahibinin dağıtım kararları dış kabul işleri olarak açık kalıyor. Bu nedenle sürüm release candidate durumundadır.

## Tamamlanan geliştirmeler

| Adım | Sonuç |
|---|---|
| C04 | Kaynak bütçeli import/preview alt süreci; bellek/süre/disk takibi, iptal, yeniden bağlanma ve commit önceliği. Streaming fingerprint, sınırlandırılmış distinct sorgusu ve DuckDB spill kontrolü. |
| C05 | TR/EN çevrimdışı yardım; kalite, ikili sınıflandırma, regresyon ve çok sınıflı sentetik örnek projeleri. Beş katılımcı için görev/kayıt protokolü. |
| D01 | Eğitim bölümünde grup/zaman kurallarını koruyan 2–5 fold; fold içinde yeni pipeline; model kartı ve validation permutation importance. |
| D02 | Ortalama, kesin binom oranı, Welch farkı ve bağımsızlık onaylı Pearson güven aralıkları. Küçük örnek ve yöntem varsayımları uyarıları. |
| D03 | Validation üzerinde sigmoid-CV kalibrasyon karşılaştırması; Brier/log-loss/reliability. Eğitim referansına göre yeni veri dağılımı, kategori ve eksiklik değişimi raporu. |
| D04 | Ayrı regresyon modülü; Dummy/Ridge/RandomForest, MAE/RMSE/R²; validation seçimi, kalıcı final kilidi ve yeni veride eşlemeli tahmin. |
| D05 | Ayrı 3–20 sınıflı modül; Dummy/LR/RandomForest, accuracy/macro-weighted F1, sınıf metrikleri ve validation confusion matrix. Final kilidi/tahmin akışı. |
| E01 | Inner/left join; kesin anahtar türü ve kardinalite kontrolü, eşleşmeyen satırlar ve çoğalma bütçesi. Kaynak sütun kimliği ve lineage; migration 0006. |
| E02 | V2 kapalı komut adaptörü; belirsizlikte açıklama, ayrı plan/preview/apply ve mevcut sürüm geçmişinden geri dönüş. Eski bağlamdaki planlar reddedilir. |
| E03 | Varsayılan kapalı Ollama uyumlu JSON planlayıcı; açık metadata gönderme izni, token/süre/byte sınırları, yerel şema doğrulaması ve kural tabanlı fallback. |
| E04 | Kayıtlı sonuçlardan grafik içeren PDF ve gerçek XLSX; TR/EN, tablo kaynağı eşitliği, literal metin hücreleri ve kalıcı rapor cache'i. |

## Doğrulama

- Tam test kümesi: **706 geçti**, 2 mevcut pandas/tarih uyarısı; süre 364.13 sn. Önceki C03 kapanışındaki 623 teste göre **83 ek senaryo**.
- Arka uç branch coverage: **%86,73**; %80 eşik geçti. UI/desktop bu yüzdeye dahil değildir; AppTest ve ayrı öz testlerle denetlenir.
- Kritik domain/preparation/holdout alt kümesi: **%93,04**; %90 eşik geçti. Aynı tam koşunun coverage verisinden hesaplandı.
- Ruff lint ve **153 dosyada** format kontrolü başarılı. Mypy **22 çekirdek dosyada** başarılı. Compileall, frozen lock ve ilgili git diff whitespace kontrolü başarılı.
- Kesin son test günlüğü: `build/release-all-tests.log`; diğer ayrıntılı yerel kanıtlar `build/final-*.log` dosyalarında.

Ek kontroller: yardımdan örnek oluşturma → projenin seçilmesi → 400 satırın kalıcı açılması AppTest ile doğrulandı. Türkçe altı sayfalık PDF rasterize edilip tüm sayfalarda yazı, grafik ve satır sonları görsel olarak incelendi. PDF'de kullanıcı metninin matematik olarak yorumlanmaması ve XLSX'te formül benzeri metinlerin literal kalması test edildi.

Wheel kilitli runtime bağımlılıklarıyla, içerik hash'i içeren ayrı dosya yolundan kaynak ağacından bağımsız kuruldu; `python -I` öz testi başarılı. EXE sürüm ve genişletilmiş öz testi erişilemeyen HTTP/HTTPS proxy'leriyle başarılı. Öz test gerçek analiz worker'ı, regresyon/çok sınıflı eğitim, PDF/XLSX açılışı, snapshot/restore, bakım, CSV/Parquet, ayrı import/preview ve iptalde head korumasını kapsar. Bu, ağın işletim sistemi seviyesinde kapatıldığı veya ayrı makine kabulü yapıldığı anlamına gelmez.

EXE içindeki **97 modül ve desktop giriş noktası** güncel kaynak kodla karşılaştırıldı. Wheel içindeki bütün Python dosyaları ve migration dosyaları kaynakla byte düzeyinde eşleşti. Kanıt: `benchmarks/release-verification.json`.

## Yerel kaynak ölçümleri

| Sentetik veri | Import | Tepe worker RSS | İki adımlı preview |
|---|---:|---:|---:|
| 25.000 × 8 | 1,98 sn | 269,0 MiB | 0,97 sn |
| 100.000 × 8 | 4,27 sn | 146,9 MiB | 0,94 sn |
| 20.000 × 80 | 6,59 sn | 343,2 MiB | 0,98 sn |
| 1.000.000 × 5 | 26,17 sn | 412,9 MiB | 1,75 sn |

50.000 × 8 fingerprint karşılaştırmasında SHA-256 aynı kaldı; örneklenen süreç tepe RSS **285,9 → 100,7 MiB**, yaklaşık **%64,8 azalma**. Ham ölçümler/donanım/bütçe: `benchmarks/c04-local.json`. Süreler worker monitörü başladıktan itibaren ölçülür; örnekleme ve yerel donanıma bağlıdır. p95 veya başka makineler için SLA değildir.

## Desteklenen sınırlar

- Import/preparation: en fazla 1 milyon satır, 500 kullanıcı sütunu ve 5 milyon hücre; üç sınır birlikte geçerlidir. Worker varsayılanı 1.536 MiB RSS, 120 sn, 1.024 MiB net workspace artışı ve 128 MiB boş disk rezervidir. Küçük senkron işlemler sert worker watchdog'u kullanmaz.
- Join: inner/left, null anahtarlar eşleşmez. Beyan edilen kardinalite, en fazla 10 kat çoğalma ve çıktı satır/hücre bütçesi uygulanır. Kimliği çakışan kendi/geçmiş sürüm join'i desteklenmez.
- Komut asistanı belgelenmiş kapalı grameri kullanır. Planlayıcı keyfi kod/SQL çalıştırmaz; satır tablolarını göndermez ve otomatik Apply yapmaz. Bir istek için token talebi 32–2048, varsayılan süre 15 sn, istek/yanıt 32/64 KiB'dır. Sağlayıcının token uygulaması parasal ücret garantisi değildir.
- Kalibrasyon tanısaldır; kalibre modeli devreye almaz. Drift eşikleri sezgiseldir; model performansı/kavram değişimi ölçülmüş sayılmaz. Yeni regresyon/çok sınıflı modüller için ayrı CV/kalibrasyon özelliği sunulmaz.
- Güven aralıkları örnekleme ve dağılım varsayımlarına bağlıdır. Spearman aralığı yoktur. Tekil istenen karşılaştırmalarda çoklu test düzeltmesi uygulanmaz; sonuçlar nedensel kanıt değildir.
- PDF tablo başına ilk 100 satır/hücre başına 500 karakteri açık notla gösterir; 250 sayfa ve bölüm başına 1 milyon karakter sınırı vardır. XLSX tam kayıtlı tabloları toplam 500.000 hücre/50 grafik ve Excel hücre sınırları içinde verir; aşımda sessiz kesmez. Daha büyük sonuçlarda CSV/JSON; tüm kaynak veride CSV/Parquet kullanılmalıdır.

## Dış kabul için kalan işler

1. **A06:** Python kurulu olmayan ayrı Windows bilgisayarında etkileşimli kabul. Yerel EXE öz testi bu kabulün yerine sayılmadı.
2. **C05:** Beş gerçek hedef kullanıcıyla pilot; temel görevlerde ≥%80 yardımsız başarı ve ilk rapora ≤10 dakika hedefini ölçme. `PILOT_GOREVLERI.md` hazır; katılımcı sonuçları üretilmedi.
3. **E03:** Seçilecek gerçek Ollama uyumlu sunucu/model ile isteğe bağlı kabul. Şema/izin/hata testleri sahte HTTP sağlayıcısıyla yürütüldü; gerçek model çağrısı yapılmadı.
4. **Dağıtım:** Lisans, kod imzası ve dağıtım kanalı ürün sahibi kararı bekliyor. Uzak CI/yayın bu oturumda çalıştırılmadı.

## Üretilen paketler

- `dist/AutoAnalyst.exe` — **153,301,247 bayt**. SHA-256: `c7008425c83b54f9dcf49595c7f256a229a9f542c570f0434668048534c0534f`.

- `dist/autoanalyst-2.0.0rc1-py3-none-any.whl` — **241,629 bayt**. SHA-256: `ab887a147c74f9b3d0abc357837b2c5dcd13862352e4c37ede3dd61d10e51706`.

- `dist/autoanalyst-2.0.0rc1.tar.gz` — **225,940 bayt**. SHA-256: `7080decbad4a3433a133e6e534b32e456abea8698cf533895d980eb4c6d8b91d`.

- `dist/AutoAnalyst-Windows-x64.zip` — **152,183,819 bayt**. SHA-256: `efc86877f2b62ff1d11eafd6ca479030d3b1d093582fe1692aefe57d2e2b1901`.

Kullanım: `KULLANICI_REHBERI.md`. Ayrıntılı teknik kayıt: `DEVELOPMENT.md` ve `UYGULAMA_DURUMU.md`. Dış kabul listesi: `RELEASE_KABUL_LISTESI.md`. Çalışma mevcut yerel değişiklikleri korur; otomatik commit veya yayın yapılmadı.
