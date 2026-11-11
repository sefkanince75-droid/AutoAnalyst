# V2 yayın öncesi doğrulama hazırlığı

Bu kayıt, kararlı sürüm ilanı değildir. GitHub'a gönderim kapsamı V2 kaynakları, V2 testleri, resmi paketleme araçları, proje metadata'sı, rehberler ve CI yapılandırmasıdır.

## Kapsam ve veri koruma

- Yerel `main` başlangıçta uzak `main`'den 164 commit ilerideydi; bu geçmiş zaten uzak `v2-completion` branch'indeydi. Yeni çalışma ayrı `codex/v2-pre-release-validation` branch'inde hazırlanır. Main pointer'ı değiştirilmez.
- Kapsam dışı eski uygulama/launcher/spec değişiklikleri ve bunlara ait deneysel testler yerelde korunur; bu PR'ye eklenmez. Hiçbir V2 testi çıkarılmaz.
- Yerel bütün testler: **706 geçti**, iki mevcut pandas/tarih uyarısı; backend branch coverage **%86,78**.
- Gönderilecek temiz snapshot: **623 geçti**, iki mevcut uyarı; backend branch coverage **%86,73**. Bunun **543 testi V2**, 80 testi mevcut legacy referans testidir. Yerel çalışma kopyasındaki 84 ek/değişmiş legacy testine karşılık eski tek testin korunması net 83 fark yaratır. Sayı farkı V2 testi atlanması değildir.
- Kritik domain/preparation/holdout kapsamı yerel tam koşuda **%93,04**. Ruff 153 dosyada, Mypy 22 çekirdek dosyada başarılı; compileall, frozen lock ve staged whitespace kontrolleri başarılı.
- `.gitignore` tmp, workspace/backup, veritabanı, coverage, özel anahtar ve sertifika dosyaları için güçlendirildi. Geçici dosyalar veya kullanıcı çalışma alanları silinmedi.
- Yeni dosya kümesi ve uzak main üzerinde olmayan mevcut V2 geçmişindeki 222 metin blob'u `detect-secrets --no-verify` ile tarandı. Gerçek anahtar/parola bulunmadı. Sekiz snapshot eşleşmesi incelendi: beş SHA-256 ve üç sahte güvenlik testi girdisi. Geçmiş taramasında bulgu yok.
- Plan belgesindeki kişisel mutlak dosya yolları depo içi bağlantılara çevrildi. Yeni commit için GitHub noreply adresi kullanılır; mevcut kullanıcı Git ayarı değiştirilmez.

## Paket ve CI

- Temiz snapshot'tan wheel/sdist ve yeni Windows EXE üretildi. Wheel/sdist içinde workspace, geçici veri, secret veya cache dosyası bulunmadı.
- İzole wheel `python -I` öz testi ve yeni EXE'nin geçersiz uzak HTTP/HTTPS proxy ayarlarıyla öz testi başarılı. Öz test geçici workspace kullanır; gerçek kullanıcı verisini açmaz.
- 97 frozen modül ve desktop giriş noktası kaynakla karşılaştırıldı; wheel Python kaynakları da eşleşti.
- İlk uzak Linux Mypy çalışması Windows sabitine doğrudan erişimi yakaladı. Erişim taşınabilir `getattr` ile düzeltildi; Linux/win32 platform tip kontrolleri ve 26 gerçek veri-worker testi geçti. Mypy artık CI'da iki işletim sisteminde de çalışır.
- CI ve Windows paket doğrulaması branch/PR olaylarında çalışır. Push/PR concurrency grupları aynı kaynak branch'i paylaşarak tekrarlı çalışmaları iptal eder. Workflow token'ı salt okunurdur.
- Lock workflow'u artık otomatik commit/push yapmaz; manuel, salt okunur doğrulama yapar. Paket workflow'u artifact üretir, GitHub Release oluşturmaz; sürüm etiketi tetikleyicisi kaldırıldı.
- PowerShell'de native komut hata kodları açıkça denetlenir. Üç YAML dosyası ayrıştırıldı. İsteğe bağlı actionlint kurulumu upstream binary indirmesi başarısız olduğu için çalışmadı; GitHub Actions'ın gerçek sonuçları ayrıca PR üzerinden izlenir.

## Dış kabul kapıları

1. Beş hedef kullanıcı: ilk beş temel görevde en az %80 yardımsız başarı ve ilk rapora en fazla 10 dakika hedefi. Görev/sorun şablonu PILOT_GOREVLERI.md içindedir; gerçek sonuçlar üretilmez.
2. Bağımsız Python'sız Windows makinesi: açılış, import, analiz, rapor, iptal, yeniden açma ve yedek/restore yolculuğu. Yerel EXE öz testi bunun yerine sayılmaz.
3. Gerçek Ollama uyumlu LLM sunucusu: metadata izni, geçerli/geçersiz yanıt, timeout ve offline fallback kabulü. Mevcut testler sahte sağlayıcı kullanır.
4. Ürün sahibi: lisans, kod imzası ve dağıtım kanalı. Kullanıcı kabulü kapanmadan kararlı sürüm, merge, v2.0.0 etiketi veya Release yoktur.

TAMAMLAMA_RAPORU.md önceki geliştirme kapanışının tarihsel kaydıdır. Bu dosya gönderim öncesi yerel kontrolleri kaydeder; uzak CI'nin güncel durumu PR ve Actions kayıtlarından okunmalıdır.
