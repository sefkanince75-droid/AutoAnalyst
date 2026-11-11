# AutoAnalyst uygulama ilerlemesi

Son güncelleme: **8 Kasım 2026**. Referans: `PROJE_INCELEME_VE_YOL_HARITASI.md`.

## İlk uygulama dilimi

| Paket | Durum | Uygulanan değişiklik |
|---|---|---|
| A01 | Tamamlandı | V2/legacy sınırı ve resmi komutlar `DEVELOPMENT.md` içinde; Ruff import/format hatası giderildi. Önceden mevcut legacy değişiklikleri korunuyor. |
| A02 | Tamamlandı | Önizleme proje/veri seti/sürüm/head revision/işlem/parametre bağlamına bağlandı. Bağlam değişince seçim temizleniyor. Apply servisinde beklenen proje, taban sürüm ve reçete hash'i kontrolleri eklendi. Hedef veri seti ekranda belirtiliyor. |
| A03 | Tamamlandı | Rapor proje/sonuç/biçim/dil/şablon/seçenek bağlamına bağlandı. İndirme öncesinde raporun kök sonucu ve artifact sahipliği tekrar kontrol ediliyor; dosya adı doğrulanan rapordan geliyor. |
| A04 | Tamamlandı | CSV önce ayrıştırılıyor; CSV/XLSX başarısız aktarımında yalnızca o denemeye ait kayıtsız nesneler temizleniyor. Başarılı commit'ten sonraki hata kayıtlı dosyaları silmiyor. UI, TR/EN açıklama ve veriyi içermeyen teşhis numarası gösteriyor. XLSX kaynakları bağlam yöneticisiyle kapatılıyor. |
| A05 | Uygulandı ve yerelde doğrulandı | Paket içi UI başlangıcı, `autoanalyst` kullanıcı komutu, tek sürüm kaynağı ve ortak Windows build komutu eklendi. Yerel build ve release CI aynı aracı kullanıyor. Wheel ve EXE üretildi. |
| A06 | Otomatik yolculuklar tamamlandı; bağımsız makine kabulü bekliyor | Gerçek worker ile import→profil→rapor→yeniden açılış, iptal ve beklenmedik süreç sonlanması testleri eklendi. Kurulu wheel ve offline EXE öz testleri başarılı. Python kurulu olmayan ayrı Windows makinesinde etkileşimli kabul testi henüz yapılmadı. |

Gerçek alt süreç testinde ek bir hata bulundu ve düzeltildi: worker zorla durdurulduğunda, kalıcı iptal isteğine rağmen çalışma `failed` oluyordu. Kurtarma artık terminal manifest yoksa ve kalıcı iptal isteği varsa `cancelled` olarak tamamlıyor. Beklenmedik süreç sonlanması `failed` kalıyor; varsa worker'ın tamamlanmış sonuç manifesti önceliğini koruyor.

## Doğrulama kanıtları

- Tam test kümesi: **370 geçti**, iki mevcut pandas/tarih uyarısı. Başlangıçtaki 327 teste göre **43 yeni test senaryosu**.
- Branch coverage içeren arka uç kapsamı: **%84,69**; %80 eşik geçti. Başlangıç ölçümü %83,36 idi. UI/desktop bu yüzdeye dahil değildir.
- Tam koşunun coverage verisinden kritik domain/preparation/holdout alt kümesi: **%92,05**. CI'ın ayrı Linux testi bu yerel ölçümün yerine geçirilmedi.
- Ruff lint: başarılı. Ruff format: **99 dosya uygun**.
- Mypy: CI'da tanımlanan **14 çekirdek dosyada sorun yok**.
- Compileall ve `git diff --check`: başarılı.
- `uv lock --check --offline`: başarılı; sayısal bağımlılık sürümleri değiştirilmedi. Paket sürümü kilitte dynamic olarak işaretlendi.
- Wheel ve sdist üretimi başarılı. Wheel, kilit dosyasından export edilen runtime bağımlılıklarıyla izole ortama kuruldu; `python -I -m autoanalyst.desktop --self-test` başarılı. Kaynak dizinindeki `app.py` kullanılmıyor.
- Windows portable EXE üretimi başarılı: `dist/AutoAnalyst.exe`, yaklaşık **151,3 MB** (ondalık).
- EXE `--version`: **2.0.0rc1**. Windows dosya metadata sürümü de **2.0.0rc1** olarak doğrulandı.
- EXE `--self-test`: HTTP/HTTPS proxy'leri erişilemeyen loopback adresine ayarlanmışken başarılı. Geçici çalışma alanında gerçek worker, profil, HTML rapor ve yeniden açılış doğrulandı.
- EXE içindeki derlenmiş recovery modülünün yeni iptal mantığını içerdiği ayrıca kontrol edildi.

Testler geçici çalışma alanlarında yapıldı. EXE/izole wheel testleri bu bilgisayarda çalıştırıldı; ayrı ve Python'sız Windows ortamında UI kabulü, kod imzası ve kullanıcı pilotu bu dilimde doğrulanmadı. Uzak CI çalıştırması veya release yayımlaması yapılmadı.

## Kullanım ve build

```powershell
uv run --frozen autoanalyst
# Resmi V2 portable build:
uv run --frozen --group desktop python tools/build_release.py
.\dist\AutoAnalyst.exe --self-test
```

Detaylar: `DEVELOPMENT.md`. Kaynak ağaçtaki eski `AutoAnalyst.spec`, `desktop_launcher.py` ve `build_desktop.bat` korunmuştur; resmi V2 release yolu `tools/build_release.py` olarak belgelenmiştir.

## B01 — İçe aktarma sihirbazı ve satır tarayıcı

**Kod ve test durumu: tamamlandı.**

- CSV için virgül, noktalı virgül, sekme ve dikey çizgi ayırıcıları; `utf-8-sig`, `utf-8`, `cp1254`, `iso8859-9` ve `utf-16` kodlamaları seçilebiliyor.
- İçe aktarmadan önce en fazla 100 satır ve örnek sütun türleri gösteriliyor. Başka satır olup olmadığını anlamak için okuyucu en fazla 101 veri satırı istiyor. Örnek veri sürümü veya artifact oluşturmuyor.
- **Dosya/ayar → önizleme → içe aktarma** akışı var. Dosyanın içeriği, hedef veri seti, ayırıcı, kodlama veya XLSX sayfası değiştiğinde eski örnek geçersizleşiyor ve yeni önizleme gerekiyor.
- XLSX'te yalnızca seçili sayfa önizleniyor ve içe aktarılıyor. Önizleme örneği kesin tam-dosya türü olarak sunulmuyor; import tam dosyayı tekrar doğruluyor. İlk 100 satırdan sonraki bozuk CSV satırının import'u durdurduğu test edildi.
- UTF-8 BOM başlıkları temizleniyor. Tek sütun algılandığında ayırıcıyı kontrol etme önerisi gösteriliyor; geçerli tek sütunlu dosyalar engellenmiyor.
- Veri sekmesi 25, 50, 100, 250 veya 500 satırlık sayfalar gösteriyor. Kullanıcı en fazla 50 sütun seçebiliyor; başlangıçta ilk 20 sütun seçiliyor.
- `TableAccess.page`, katalogdaki sütunları ve parametrik `LIMIT/OFFSET` sorgusunu kullanıyor. Sayfa sırası sabit; satır numaraları ve toplam/sayfa bilgisi gösteriliyor. Sayfa girdisi sürüm ve sayfa boyutuna bağlı.
- Veri seti/sürüm değişiminde eski sayfa uygulanmıyor. Tarayıcı testi, bütün sütunu okuyan metodu yasaklayarak yalnızca sayfalı yolun kullanıldığını doğruluyor.
- İçe aktarma ve satır tarayıcı ekran kodu ayrı UI modüllerine taşındı. Sütun bilgi alanında kullanıcı adları ve türler var; fiziksel depolama kimlikleri gösterilmiyor.
- UI testleri Windows'ta aynı zaman damgasıyla oluşturulan projelerin rastgele UUID sırasına bağımlı kalmayacak şekilde hedef projeyi açıkça seçiyor.

### B01 doğrulama sonuçları

- Tam test kümesi: **398 geçti**, iki mevcut pandas/tarih uyarısı. Bu dilimde **28 yeni senaryo** eklendi.
- Arka uç branch coverage: **%84,93**. UI/desktop bu yüzdeye dahil değildir; kullanıcı akışları AppTest ile ayrıca doğrulandı.
- Ruff lint/format başarılı; **103 dosya** biçim kontrolünü geçti. Mypy çekirdek kapsamındaki 14 dosyada sorun yok. Compileall ve ilgili `git diff --check` başarılı.
- Wheel/sdist yeniden üretildi. Kilitli bağımlılıklarla izole kurulan wheel, gerçek worker/rapor/yeniden açılış öz testini geçti.
- Windows EXE yeniden üretildi: `dist/AutoAnalyst.exe` (**151.322.471 bayt**). `--version` ve proxy'lerle ağ erişimi engellenmiş `--self-test` başarılı. Yeni import, satır tarayıcı ve preview modüllerinin EXE arşivinde bulunduğu ayrıca doğrulandı.

Sayfalama, Python'a alınan tabloyu sınırlar; Parquet bütünlük doğrulaması ve DuckDB sorgusunun disk/CPU maliyetini kaldırmaz. Büyük veri performans hedefleri C04 kapsamında ölçülecek. Ayrı ve Python kurulu olmayan Windows makinesindeki etkileşimli kabul kontrolü hâlâ bekliyor.

## B03 — Ortak TR/EN metin kataloğu ve tipli alanlar

**Kod ve yerel doğrulama tamamlandı: 23 Ekim 2026.**

- UI, içe aktarma açıklamaları ve HTML/Markdown raporları ortak `localization.py` kataloğunu kullanıyor. İşlem adları, başlıklar, ölçütler, durumlar, bilinen bulgular ve hatalar TR/EN açıklanıyor. Kullanıcı metinleri çevrilmiyor; teknik yöntem/kaynak JSON bilgisi ve JSON/CSV anahtarları korunuyor.
- İlk açılışta proje varsayılan dili kullanılıyor. Açık kullanıcı tercihi yoksa proje değişimi dili de değiştiriyor. Açık dil tercihi sonraki proje geçişlerinde korunuyor. Dil değişimi mevcut form değerlerini ve geçerli hazırlama önizlemesini kaybettirmiyor.
- Hazırlama filtreleri ve sabit doldurma alanları tam sayı, ondalık sayı, mantıksal değer ve tarih/saat türlerine göre oluşturuluyor. Boole değerleri metin yerine gerçek `bool`, sayılar sayısal değer olarak reçeteye gidiyor. Tarih/saat ISO biçiminde kaydediliyor; saat dilimli alanlar UTC olarak açıkça belirtiliyor.
- JavaScript güvenli tam sayı sınırını aşan değerler için tam hassasiyetli metin girişi var. Tam sayı biçimi ve fiziksel tür sınırları doğrulanıyor; `2**53 + 1` değerinin yuvarlanmadan tam bir satırı eşlediği test edildi.
- Filtre işleçleri türe uygun seçeneklerle sınırlı. Farklı türlerden sütunlara tek sabit doldurma ve sayısal olmayan sütunlara ortalama/medyan doldurma önizlemeden önce engelleniyor. Geçersiz giriş önceki önizlemeyi temizliyor.
- Sayısal analiz seçimleri sayısal sütunlarla sınırlandı. Welch testi bağımsız gözlem onayı olmadan başlatılamıyor; eksik kalan `independence_confirmed` parametresi artık arka uca gönderiliyor. Yeni veride puanlama için son değerlendirmesi tamamlanmış model gerekiyor.
- Tarih/saat sütununda mod ile doldurma sırasında öğrenilen Python tarih değerinin reçeteye yazılamaması düzeltildi; çözümlenmiş değer ISO olarak kayıtlı ve tekrar üretilebilir kalıyor.
- HTML/Markdown rapor sunumu yerelleştirildi. Rapor şablonu revizyonu artırıldı; yeni sunum yeni artifact üretiyor, eski rapor değişmiyor. HTML ve tablo metinleri kaçışlanıyor; sayısal sonuçlar tekrar hesaplanmıyor.

### B03 doğrulama sonuçları

- Tam test kümesi: **416 geçti**, iki mevcut pandas/tarih uyarısı; B01 sonrasına göre **18 yeni senaryo**. Tipli filtre/doldurma testleri reçete parametrelerini ve uygulanan veriyi doğruluyor.
- Arka uç branch coverage: **%84,95**, %80 eşik başarılı. UI/desktop yüzdeye dahil değildir; AppTest akışları ayrıca geçti.
- Ruff lint/format başarılı; **106 dosya** biçim kontrolünü geçti. Mypy 14 çekirdek dosyada sorun bulmadı. Compileall, `git diff --check` ve `uv lock --check --offline` başarılı.
- Wheel/sdist yeniden üretildi. Kilitli runtime bağımlılıklarıyla kaynak ağacından izole kurulan wheel, gerçek worker/rapor/yeniden açılış öz testini geçti.
- Windows EXE yeniden üretildi: `dist/AutoAnalyst.exe` (**151.346.593 bayt**). `--version` ve erişilemeyen HTTP/HTTPS proxy'leriyle `--self-test` başarılı. Ortak dil kataloğu ve tipli giriş modüllerinin EXE arşivinde bulunduğu doğrulandı. Ayrı Windows makinesi ve uzak CI bu doğrulamanın kapsamında değildir.

## B02 — Kayıtlı grafikler ve özet sonuç düzeni

**Kod ve yerel doğrulama tamamlandı: 23 Ekim 2026.**

- Sonuç ekranı ve HTML raporları ortak SVG histogram çizicisini kullanıyor. Kayıtlı `ChartSpec` tablo/alan bağlantıları çözülüyor; çubuk aralıkları ve frekansları kayıtlı `ResultTable` içeriğinden geliyor. Veri seti tekrar okunmuyor, histogram veya analiz yeniden hesaplanmıyor.
- Sütun başlıkları kayıtlı profil tablosundan alınıyor. Veri seti sonradan değiştirilse ve uygulama yeniden açılsa da eski sonucun grafiği aynı kalıyor. TR/EN başlıklar, eksenler, çubuk açıklamaları ve kaynak bilgisi ortak katalogdan geliyor.
- HTML grafikler betik/dış kaynak gerektirmiyor; SVG doğrudan dosyada bulunuyor. Erişilebilir başlık/açıklama, çubuklarda tam aralık/sayı bilgisi ve kaynak tablo bağlantısı var. Yerel tarayıcıda Türkçe örnek rapor görsel olarak kontrol edildi.
- Desteklenmeyen grafik türü, eksik tablo, boş veri, geçersiz alan/değer, çakışan/sırasız aralıklar veya 1000 aralık sınırının aşılması halinde açıklama gösteriliyor. Tablolar ve diğer rapor bölümleri erişilebilir kalıyor. Veri sessizce budanmıyor veya yeniden örneklenmiyor.
- Sonuç ekranında en fazla dört kayıtlı genel/sonlu ölçüt için özet kartı, bulgular, grafikler ve açılır ölçüt/tablo detayları var. HTML aynı özet seçimini kullanıyor. Kullanıcı verileri ve sayısal sonuçlar korunuyor.
- Rapor şablonu `presentation-3` revizyonuna taşındı; eski raporların üzerine yazılmıyor. Paket öz testi artık gerçek worker'ın ürettiği SVG'nin HTML içinde birebir bulunduğunu da denetliyor.
- Bu paket mevcut motorun ürettiği histogramları görünür kılıyor. Yeni grafik türü/analiz üretimi, Markdown/PDF/XLSX içine görsel ekleme bu paketin kapsamında değildir.

### B02 doğrulama sonuçları

- Tam test kümesi: **437 geçti**, iki mevcut pandas/tarih uyarısı; B03 sonrasına göre **21 yeni senaryo**. Aralık/sayı eşitliği, TR/EN, boş/geçersiz/desteklenmeyen grafikler, sıfır frekans, uç sonlu aralıklar ve veri değişimi sonrası UI/HTML/yeniden açılış doğrulandı.
- Arka uç branch coverage: **%85,30**, %80 eşik başarılı. UI/desktop bu yüzdeye dahil değildir; sonuç ekranı AppTest ile ayrıca doğrulandı.
- Ruff lint/format başarılı; **109 dosya** biçim kontrolünü geçti. Mypy 14 çekirdek dosyada sorun bulmadı. Compileall, ilgili `git diff --check` ve `uv lock --check --offline` başarılı.
- Wheel/sdist yeniden üretildi. Kilitli runtime bağımlılıklarıyla kaynak ağacından izole kurulan wheel, grafiklerin HTML'e gömülmesini de denetleyen gerçek worker/rapor/yeniden açılış öz testini geçti.
- Windows EXE yeniden üretildi: `dist/AutoAnalyst.exe` (**151.357.561 bayt**). `--version` ve erişilemeyen HTTP/HTTPS proxy'leriyle genişletilmiş `--self-test` başarılı. Ortak grafik çizicisi ve sonuç ekranı modüllerinin EXE içinde bulunduğu doğrulandı. Ayrı Windows makinesi ve uzak CI bu yerel doğrulamanın kapsamında değildir.

## B04 — Otomatik ilerleme ve yeniden açılışta aktif iş takibi

**Kod ve yerel doğrulama tamamlandı: 23 Ekim 2026.**

- Yeni tarayıcı oturumu aktif analizleri kalıcı çalışma kayıtlarından buluyor. Başlatan oturumun `active_run_id` değerine bağımlılık kaldırıldı. Ağır worker projeler arasında ortak olduğundan diğer projedeki iş de proje adı ve çalışma kimliğiyle belirtiliyor; birden fazla bekleyen kayıt arasından izlenecek iş seçilebiliyor.
- İzleme paneli aktifken saniyede bir `st.fragment` ile yenileniyor. Diğer formları her saniye yeniden çalıştırmıyor. Analiz başlatıldığında izleme kuruluyor; tamamlanma/iptal/hata algılandığında tam sayfa bir kez yenilenerek sonuç/bildirim gösteriliyor. Aktif iş kalmayınca zamanlayıcı kurulmuyor.
- Geçen süre, mevcut adımın ilerlemesi ve son güncellemenin yaşı gösteriliyor. Yüzdenin yalnızca mevcut adıma ait olduğu açıkça belirtiliyor; kalan süre veya tüm analizin yüzdesi tahmin edilmiyor. 15 saniye yeni ilerleme gelmezse işlem hâlâ sürüyor olabileceği açıklanıyor.
- İlerleme dosyası en fazla 64 KiB olarak okunuyor; manifest/çalışma kimliği, UTC zamanı ve ilerleme aralığı doğrulanıyor. Bozuk/eksik/geçersiz dosya çalışma durumunu değiştirmiyor. Ham payload gösterilmiyor veya kopyalanmıyor; yalnızca ilerleme ve izinli model kimliği kullanılıyor.
- Açılışta ve izleme sırasında gözlenen ilerleme, uygulama tarafından olay tablosuna kaydediliyor. Aynı adım/değer/model tekrar kaydedilmiyor. Worker'ın SQLite yazmama sınırı korundu. Bu, tüm ara worker mesajlarının kayıpsız günlüğü değildir; gözlenen ilerleme örnekleridir.
- Aktif panel ve Geçmiş sekmesinde son 100 olayın UTC zamanı, çevrilmiş tür/adım, ilerleme ve güvenli ayrıntısı gösteriliyor. Serviste limitli olay sorgusu eklendi; geçmiş veri bütünüyle UI'a taşınmıyor.
- Profil için veri yükleme/sütun ilerlemesi, karşılaştırma ve hazırlamada işlem sınırları eklendi. Eğitimde mevcut model adımları kullanılıyor. İptal sahiplik denetimli mevcut koordinatör üzerinden çalışıyor.
- Yeni analiz engeli kalıcı aktif kayıtları kullanıyor. Worker alanı dolu olduğunda başlatılamayan ikinci iş artık sahipsiz `pending` kaydı bırakmıyor; `failed` oluyor ve diğer işin lease'i korunuyor.
- Birden fazla oturumun aynı sonucu yayımlama yarışı düzeltildi: yazma transaction'ı içinde durum tekrar denetleniyor; tek sonuç ve tek tamamlanma olayı kaydediliyor. İptal/hata terminal geçişlerini diğer oturumun tamamlamış olması tekrar geçiş hatasına yol açmıyor.
- Yerel tarayıcıda kontrollü ilerleme denemesi yapıldı: tıklamadan %25 → %75 güncelleme, sayfa yenilemesinden sonra aynı çalışma/ilerleme ve otomatik tamamlanma bildirimi doğrulandı. Deneme ayrı geçici çalışma alanındaydı; mevcut kullanıcı verisi kullanılmadı.

### B04 doğrulama sonuçları

- Son kodla tam test kümesi: **462 geçti**, iki mevcut pandas/tarih uyarısı; B02 sonrasına göre **25 yeni senaryo**. Tekrarlanan gözlem, kayıt sınırı, bozuk/kimliği farklı/büyük ilerleme dosyası, oturum yenileme, iptal, çapraz proje takibi, başlatma çakışması ve eşzamanlı sonuç yayımı doğrulandı.
- Arka uç branch coverage: **%85,93**, %80 eşik başarılı. UI/desktop bu yüzdeye dahil değildir; AppTest ve yerel tarayıcı kontrolü ayrıca yapıldı.
- Ruff lint/format başarılı; **112 dosya** biçim kontrolünü geçti. Mypy 14 çekirdek dosyada sorun bulmadı. Compileall, ilgili `git diff --check` ve `uv lock --check --offline` başarılı.
- Wheel/sdist yeniden üretildi. Aynı sürüm/yolda tekrar build edilen wheel için eski `uv run --with` ortamının kullanılabildiği saptandı; wheel SHA-256 içeren bir kaynak dizinine kopyalanarak izole doğrulama tekrarlandı. Kurulu modülde son kurtarma revizyonu ve gerçek worker/rapor/yeniden açılış öz testi başarılı. CI ve geliştirme kılavuzundaki wheel doğrulaması da içerik adresli kaynak yolu kullanacak şekilde güncellendi.
- Windows EXE yeniden üretildi: `dist/AutoAnalyst.exe` (**151.367.897 bayt**). `--version` ve erişilemeyen HTTP/HTTPS proxy'leriyle `--self-test` başarılı. İzleme, ilerleme, kurtarma, koordinatör, çalışma deposu ve UI modüllerinin derlenmiş kodları güncel kaynakla birebir eşleşti. Ayrı Windows makinesi ve uzak CI bu yerel doğrulamanın kapsamında değildir.

## B05 — Proje yönetimi ve sürüm geri dönüşü

**Kod ve yerel doğrulama tamamlandı: 23 Ekim 2026.**

- Yan panelden proje yeniden adlandırılabiliyor. Proje kimliği, veri setleri ve kayıtlı sonuç/raporlar korunuyor; project revision artıyor. Aynı adlı projeler kısa kimlikleriyle ayırt ediliyor. Eski oturumdan gönderilen metadata işlemi başka oturumun yeni revizyonunu ezemiyor.
- Proje arşivleme ve geri getirme eklendi. Arşivleme projeyi aktif listeden gizliyor; hiçbir veri, sürüm, sonuç veya rapor silinmiyor. Son proje arşivlense bile geri getirme alanına erişiliyor. Arşivlenen projenin bekleyen/çalışan işi varsa işlem transaction içinde reddediliyor. İşlem mevcut `archived_at` alanını kullanıyor; migration gerekmiyor.
- Geçmiş sekmesinde seçili veri setinin diğer kayıtlı sürümleri seçilebiliyor. Güncel ve hedef sürüm kimlikleri/boyutları ile hedefin en fazla 25 satır/20 sütunluk doğrulanmış örneği gösteriliyor. İnceleme sürüm veya artifact oluşturmuyor.
- Kullanıcının uyguladığı geri dönüş yalnızca güncel sürüm işaretçisini değiştiriyor, monoton head revision artırıyor ve `restore_version` olayını kaydediyor. Veri dosyaları, sürüm kayıtları, reçete/lineage, sonuçlar ve model kilitleri değişmiyor. Daha yeni kayıtlı sürüme tekrar geçilebiliyor.
- Geri dönüş servisi proje/veri seti sahipliğini, beklenen head/revision'ı ve hedef artifact bütünlüğünü kontrol ediyor. Katalog bu bağlamı yazma transaction'ında tekrar denetliyor; inceleme ile uygulama arasındaki ABA değişimi de reddediliyor. Bozuk/eksik hedef veri mevcut head'i değiştirmiyor.
- Proje/veri seti/hedef/head/revision değişince geri dönüş incelemesi geçersiz oluyor. Başarılı geri dönüş hazırlama ve geri dönüş önizlemelerini temizliyor. Eski hazırlama önizlemesi head aynı sürüme dönse bile revision nedeniyle uygulanamıyor. Çalışan analizler başladıkları immutable sürümü kullanmaya devam ediyor.
- Yerel tarayıcıda Türkçe sürüm karşılaştırması, örnek tablo ve uygulama alanı görsel olarak kontrol edildi. Deneme ayrı geçici çalışma alanında yapıldı.

### B05 doğrulama sonuçları

- Tam test kümesi: **488 geçti**, iki mevcut pandas/tarih uyarısı; B04 sonrasına göre **26 yeni senaryo**. Rename/archive/restore revizyonları, aktif iş engeli, aynı adlı projeler, son projenin geri getirilmesi, stale oturum, restore/redo, artifact bütünlüğü, inceleme sınırları, yanlış proje/veri seti ve ABA değişimleri doğrulandı.
- Arka uç branch coverage: **%86,03**, %80 eşik başarılı. UI/desktop bu yüzdeye dahil değildir; AppTest ve yerel tarayıcı kontrolü ayrıca yapıldı.
- Ruff lint/format başarılı; **115 dosya** biçim kontrolünü geçti. Mypy 14 çekirdek dosyada sorun bulmadı. Compileall, ilgili `git diff --check` ve `uv lock --check --offline` başarılı.
- Wheel/sdist yeniden üretildi. SHA-256 içeren kaynak yolundan izole kurulan wheel'de B05 servis revizyonu doğrulandı; gerçek worker/rapor/yeniden açılış öz testi başarılı.
- Windows EXE yeniden üretildi: `dist/AutoAnalyst.exe` (**151.379.500 bayt**). `--version` ve erişilemeyen HTTP/HTTPS proxy'leriyle `--self-test` başarılı. Proje/veri seti servisleri, katalog, yeni yönetim/geri dönüş UI modülleri ve dil kataloğunun derlenmiş kodları güncel kaynakla eşleşti. Ayrı Windows makinesi ve uzak CI bu yerel doğrulamanın kapsamında değildir.

## B06 — Çok adımlı reçete, satır ekleme ve değer değiştirme

**Kod ve yerel doğrulama tamamlandı: 24 Ekim 2026.**

- Hazırlama ekranı ayrı modüle taşındı. On kapalı işlemin tamamına erişiliyor; tek adımlı akış korunuyor. Çok adımlı modda en fazla 20 adım eklenip düzenlenebiliyor, yukarı/aşağı taşınabiliyor veya silinebiliyor. Reçete sıralı olarak önizlenip tek yeni sürüm olarak uygulanıyor.
- Taslak, özgün proje/veri seti/sürüm/head revision bağlamına bağlı. Dil değişiminde korunuyor; temel bağlam, adım sırası veya parametre değişince eski önizleme geçersiz oluyor. Taslakta listelenmeyen form değişiklikleri reçeteye dahil edilmiyor; adımı ekleme/kaydetme ayrımı ekranda açıklanıyor.
- Sütun yeniden adlandırma, kaldırma ve tür dönüşümü sonraki adımların form alanlarına yansıyor. Düzenleme seçili adımdan önceki şemayla açılıyor. Geçici şema görünümü tam veri motor doğrulamasının yerine geçmiyor; bozuk sıra düzenlenmeden devam edilemiyor.
- Tam eşleşmeli, tür koruyan değer değiştirme formu eklendi. En fazla 20 eşleştirme çifti ve açık eksik değer seçimi var. Aynı adımın eşleştirmeleri zincirlenmiyor; tekrar eden kaynak değerleri reddediliyor.
- Satır ekleme formu yalnızca aynı projedeki kimlik/sıra/tür uyumlu kayıtlı sürümleri listeliyor. Seçilen kaynak sırası korunup lineage'a yazılıyor. Ayrı import'larda aynı başlık olması, mevcut motor sözleşmesinde sütun kimliklerini eşitlemediği için yeterli sayılmıyor; bu sınırlama ekranda açık.
- Aynı girdi sürümünün reçete içinde tekrar eklenmesi engellendi. Filtre sonrası append iç sıra numarası çakışması düzeltildi; yeni sıra mevcut maksimumun ardından başlıyor. Aynı kaynak sonraki reçetede tekrar eklendiğinde yeni satır kimlikleri taban sürümüne göre türetilerek mevcut satırlarla çakışmıyor.
- Önizlemede adım bazlı tam veri etki sayıları, uyarılar ve özgün/son tablonun ilk 25 satır/en fazla 20 sütunluk örnekleri gösteriliyor. Aday örnek okunmadan sahiplik/bütünlük/süre kontrolü yapılıyor. Bu örnekler birebir satır karşılaştırması olarak sunulmuyor.
- Motor hataları adım konumuyla yerelleştiriliyor. Son adım başarısızsa önceki adımlar kısmi sürüm olarak yayımlanmıyor. Başarılı uygulama taslağı ve eski önizlemeyi temizliyor; veri sürümü ve reçete/lineage kalıcı kalıyor.
- Yerel tarayıcıda Türkçe iki adımlı filtre → yeniden adlandırma taslağı, 3 → 1 satır tam veri özeti, adım etkileri ve önce/sonra örnekleri görsel olarak doğrulandı. Kontrol ayrı geçici çalışma alanında yapıldı.

### B06 doğrulama sonuçları

- Tam test kümesi: **514 geçti**, iki mevcut pandas/tarih uyarısı; B05 sonrasına göre **26 yeni senaryo**. Adım sırası/düzenleme/silme, tür/isim değişimi, dil ve temel bağlam değişimleri, restore ABA, append sıra/kimlik/lineage, tekrarlı kaynak, eşzamanlı değiştirme, aday örnek sınırları/bütünlük/süre ve son adım hatasında veri koruma doğrulandı.
- Arka uç branch coverage: **%86,27**, %80 eşik başarılı. UI/desktop bu yüzdeye dahil değildir; AppTest ve yerel tarayıcı kontrolü ayrıca yapıldı.
- Ruff lint/format başarılı; **118 dosya** biçim kontrolünü geçti. Mypy 14 çekirdek dosyada sorun bulmadı. Compileall, ilgili `git diff --check` ve `uv lock --check --offline` başarılı.
- Wheel/sdist yeniden üretildi. SHA-256 içeren kaynak yolundan izole kurulan wheel'de B06 servis/UI revizyonu doğrulandı; gerçek worker/rapor/yeniden açılış öz testi başarılı.
- Windows EXE yeniden üretildi: `dist/AutoAnalyst.exe` (**151.399.444 bayt**). `--version` ve erişilemeyen HTTP/HTTPS proxy'leriyle `--self-test` başarılı. Hazırlama servisi/motoru, reçete düzenleyicisi, hazırlama UI'ı, ana UI, proje yönetimi ve dil kataloğunun derlenmiş kodları güncel kaynakla eşleşti. Ayrı Windows makinesi ve uzak CI bu yerel doğrulamanın kapsamında değildir.

## C01 — Yedekleme/geri yükleme ve uyumluluk sınırı

**Kod ve yerel doğrulama tamamlandı: 24 Ekim 2026.**

- Yan panelde proje olmasa da çalışma alanı/yedekleme kontrolleri görünüyor. Tüm workspace; arşivlenmiş projeler, sürümler, sonuçlar, raporlar ve modellerle diske yedekleniyor. Staging/log dosyaları dışarıda tutuluyor. 256 MiB'a kadar tarayıcıdan indirme sunuluyor; daha büyük çıktı disk yolunda kalıyor.
- Geri yükleme dosya yükleme → bütünlük/uyumluluk incelemesi → açık kaynak güveni onayı → ayrı yeni/boş hedef akışını kullanıyor. Kaynak byte'ları, hedef veya mevcut workspace değişirse eski inceleme uygulanamıyor. Mevcut workspace ve alt klasörleri UI'da hedef seçilemiyor; dolu hedef veya var olan yedek dosyası korunuyor.
- Servis düzeltildi: doğrulama ve bilinen eski şema yükseltmeleri hedefe taşımadan önce özel geçici klasörde tamamlanıyor. Uyumsuz/bozuk yedek yayımlanmıyor. Yayım anında ortaya çıkan hedef dosyası da ezilmiyor. Hata temizliği yalnızca oluşturulan geçici klasörü kapsıyor.
- ZIP yolları, case/duplicate çakışmaları, Windows alias/ADS/reserved adları, symlink/şifreleme/sıkıştırma biçimi, entry sayısı ve boyutlar doğrulanıyor. Katalog/artifact hash'leri ile katalog–manifest dosya kümesi birebir eşleştiriliyor. Limitler: 2 GiB arşiv, 8 GiB açılmış içerik, 256 MiB katalog, 8 MiB manifest, 100.000 giriş.
- Katalog önce salt okunur ve trusted_schema kapalı inceleniyor. Bilinen migration checksum'ları/SQL şeması, bütünlük/foreign-key durumu, proje biçimi/dili ve tanınan domain payload'ları kontrol ediliyor. Bilinmeyen şema/trigger/view, future migration ve desteklenmeyen uygulama ailesi reddediliyor. Bilinen eski şema hedef yayımlanmadan yükseltiliyor.
- Geri yükleme servisi artık `trusted=True` ister. Dosya hash'inin kaynağın kimliğini kanıtlamadığı ve modellerin daha sonra yüklendiğinde kod çalıştırabileceği ekranda belirtiliyor. İnceleme ve geri yükleme model deserialize etmiyor; model kütüphane sürüm uyumluluğu bu aşamada garanti edilmiyor.
- Geri yüklenen workspace ayrı düğmeyle bu tarayıcı oturumunda açılabiliyor; özgün alana dönüş var. Süreç geneli AUTOANALYST_WORKSPACE ve diğer oturumlar değişmiyor. Proje/veri/önizleme/rapor seçim state'i geçişte temizleniyor. Aktif işler bitirilmeden yedekleme, geri yükleme veya geçiş yapılmıyor.
- Gerçek modelin yedeklenip geri yüklenmesinde rapor byte'ları, sonuçlar, arşiv durumu ve final holdout kilidi korundu; geri yüklenen modelle puanlama tamamlandı. Paket öz testi de gerçek worker/rapor yolculuğuna yedekleme–inceleme–geri yükleme kontrolü ekleyecek şekilde genişletildi.
- Yerel tarayıcıda Türkçe panelin görünümü, örnek çalışma alanından diske yedek oluşturma ve indirme alanı görsel olarak kontrol edildi. Kontrol ayrı geçici çalışma alanındaydı; kullanıcı verisi kullanılmadı.

### C01 doğrulama sonuçları

- Tam test kümesi: **551 geçti**, iki mevcut pandas/tarih uyarısı; B06 sonrasına göre **37 yeni senaryo**. Güven onayı, eski/bilinmeyen şema, bozuk hash/payload, ZIP yol/entry/boyut sınırları, mevcut/race hedef koruması, aktif iş engeli, model deserialize etmeyen inceleme, UI inceleme bağlamı ve oturum bazlı workspace geçişi doğrulandı.
- Arka uç branch coverage: **%86,12**, %80 eşik başarılı. UI/desktop bu yüzdeye dahil değildir; AppTest ve yerel tarayıcı kontrolü ayrıca yapıldı.
- Ruff lint/format başarılı; **120 dosya** biçim kontrolünü geçti. Mypy 14 çekirdek dosyada sorun bulmadı. Compileall, ilgili `git diff --check` ve `uv lock --check --offline` başarılı.
- Wheel/sdist yeniden üretildi. SHA-256 içeren kaynak yolundan izole kurulan wheel'de C01 servis/UI revizyonu doğrulandı; yedekleme/inceleme/geri yükleme eklenen gerçek worker/rapor öz testi başarılı.
- Windows EXE yeniden üretildi: `dist/AutoAnalyst.exe` (**151.426.079 bayt**). `--version` ve erişilemeyen HTTP/HTTPS proxy'leriyle genişletilmiş `--self-test` başarılı. Snapshot servisi, yedekleme UI'ı, ana UI, dil kataloğu ve öz testin derlenmiş kodları güncel kaynakla eşleşti. Ayrı Windows makinesi ve uzak CI bu yerel doğrulamanın kapsamında değildir.

## C02 — Depolama görünümü ve geri alınabilir bakım

**Kod ve yerel doğrulama tamamlandı: 24 Ekim 2026.**

- Yan panelde proje olmasa da erişilen, açık düğmeyle çalışan depolama taraması eklendi. Katalog, kayıtlı dosya, kayıtsız üretilmiş dosya, staging, günlük, karantina ve diğer dosyaların boyutları gösteriliyor. Tarama anlık görünüm olarak belirtiliyor; symlink/junction izlenmiyor.
- Bütün katalog artifact kayıtları korunuyor; arşivli projeler, eski sürümler, süresi dolmuş önizlemeler, rapor/model ve holdout referansları budanmıyor. Yalnızca uygulamanın UUID nesne/staging adlandırmasına uyan, katalogda kayıtlı olmayan ve en az 24 saattir değişmeyen dosyalar aday.
- İnceleme → açık dosya seçimi → karantina → geri getirme akışı var. Boş seçimde taşıma kapalı. Her işlem en fazla 1.000 dosya; kayıtlı veya bilinmeyen dosyalar taşınamıyor. Karantina kalıcı silme yapmaz ve disk alanı boşaltmaz; bu sınır ekranda ve belgelerde açık.
- Taşıma/geri getirme OS dosya kilidi ve SQLite yazma transaction'ıyla korunuyor. CSV/XLSX import, hazırlama önizlemesi ve rapor üretimi dosya üretiminden katalog kaydına kadar PID/create_time işaretçisi tutuyor. Aktif/bozuk işaretçi, pending/running iş veya mevcut worker lease işlemi engelliyor; PID tekrar kullanımı ayırt ediliyor.
- İnceleme sonrası dosya/root/ref değişimleri hash, boyut, mtime, inode, yaş ve yol sınırıyla tekrar denetleniyor. Referanslar case farklarına karşı muhafazakâr karşılaştırılıyor. Link/junction, traversal ve ADS yolları reddediliyor. Yeni referans eklenmiş dosya taşınmıyor.
- Taşımalardan önce manifest fsync ediliyor. Yarıda kalmış karantina ve geri yükleme kurtarılabiliyor. Geri getirme mevcut hedefi ezmeyen hard-link yayımı kullanıyor; iki bağlantının da kaldığı kesinti tekrar denemeyle çözülebiliyor. Tamamlanan işlemin boş manifesti denetim izi olarak tutuluyor.
- Paket öz testine ayrı geçici restore alanında gerçek karantina/geri alma ve dosya byte eşitliği eklendi. Türkçe panel ve kategori tablosu yerel tarayıcıda ayrı geçici örnek workspace ile kontrol edildi. Kullanıcı workspace'inde bakım yapılmadı.

### C02 doğrulama sonuçları

- Tam test kümesi: **580 geçti**, iki mevcut pandas/tarih uyarısı; C01 sonrasına göre **29 yeni senaryo**. Kayıtlı/unknown/recent dosya koruma, sonradan referans ekleme ve dosya değişimi, pending/running/lease/writer engeli, süreçler arası kilit, PID reuse/bozuk işaretçi, junction, traversal, bozuk manifest, hedef koruma ve kesinti sonrası karantina/geri getirme doğrulandı. İlgili servis/import/UI alt kümesinde ayrıca **127 test** geçti.
- Arka uç branch coverage: **%86,40**, %80 eşik başarılı. UI/desktop bu yüzdeye dahil değildir; AppTest ve geçici workspace ile yerel tarayıcı kontrolü ayrıca yapıldı.
- Ruff lint/format başarılı; **124 dosya** biçim kontrolünü geçti. Mypy **15 çekirdek dosyada** sorun bulmadı. Compileall, ilgili git diff --check ve uv lock --check --offline başarılı.
- Wheel/sdist yeniden üretildi. SHA-256 içeren kaynak yolundan izole kurulan wheel; gerçek worker/rapor/yeniden açılış, snapshot ve yeni geri alınabilir bakım öz testini geçti. Wheel içindeki dört bakım/öz test modülü güncel kaynakla birebir eşleşti.
- Windows EXE yeniden üretildi: dist/AutoAnalyst.exe (**151.443.115 bayt**). --version ve erişilemeyen HTTP/HTTPS proxy'leriyle genişletilmiş --self-test başarılı. Aktivite kilidi, bakım servisi/UI, import, hazırlama/rapor, dil kataloğu ve öz test dahil on modülün derlenmiş kodu güncel kaynakla eşleşti. Ayrı Windows makinesi ve uzak CI bu yerel doğrulamanın kapsamında değildir.

## C03 — Temiz veri dışa aktarma ve rapor özeti

**Kod ve yerel doğrulama tamamlandı: 24 Ekim 2026.**

- Veri sekmesinde seçili immutable sürüm CSV/Parquet olarak dışa aktarılabiliyor. Ekrandaki sayfalama/projeksiyondan bağımsız bütün satır ve kullanıcı sütunları kaynak sırasıyla, görünen sütun adlarıyla yazılıyor. İç satır kimliği/sırası dosyaya dahil edilmiyor; sürüm/head/lineage değişmiyor.
- CSV UTF-8 BOM, virgül ayırıcı, Unicode/alıntı/yeni satır kaçışları ve özgün değerler kullanıyor. Tür bilgisi CSV'de saklanmadığı için yeniden import türleri tekrar tahmin ediyor; Parquet kaynak Arrow alan türlerini, null değerleri ve tarihleri koruyor. Boş hazırlanmış tablolar da geçerli başlık/şema ile aktarılıyor.
- DuckDB okuyucusu 65.536 satırlık Arrow batch'leriyle besleniyor; 66.001 satırlık çok batch'li çıktı eksiksiz/sıralı doğrulandı. Python'a tam tablo alınmıyor. DuckDB sıralama maliyeti ve toplam RSS için garanti verilmedi; performans/iptal C04 kapsamında.
- Hedef workspace dışında ve var olan klasörde olmalı. Device/ADS adları ve yanlış uzantılar reddediliyor. Yazma özel geçici dosyada tamamlanıp fsync ve satır sayısı kontrolünden sonra Windows exclusive rename/POSIX exclusive link ile yayımlanıyor. Yarışla oluşan hedef dahil hiçbir mevcut dosya ezilmiyor; hatada yalnızca çağrının kendi geçici dosyası temizleniyor.
- UI fişi workspace/proje/veri seti/sürüm/biçim/hedef bağlamına bağlı. Seçim değişince eski indirme kaldırılıyor; indirmede dosya boyutu ve SHA-256 yeniden denetleniyor. 256 MiB üzerindeki dosyalar disk yolunda kalıyor. Genel Parquet import UI eklenmedi: Parquet tablo okuyucularıyla açılır; proje geçmişi/sonuçlar için C01 yedeği kullanılır.
- Ortak rapor özeti yalnızca kayıtlı AnalysisResult'tan üretiliyor. Sonuç/çalışma/veri sürümü, kayıtlı sample sayıları, en fazla dört boyutsuz sonlu ölçüt ve ilk üç uyarı/hata gösteriliyor; kalan uyarı sayısı ve kapsam notu var. Split/model/grup ölçütleri ve tanımsız değerler ayrıntıda kalıyor. Yeni hesaplama veya istatistiksel anlamlılık kararı üretilmiyor.
- Aynı özet arayüz, HTML, Markdown, JSON summary ve CSV-ZIP summary.txt içinde mevcut. HTML/Markdown özet metinleri kaçışlanıyor. Şablon presentation-4 oldu; eski rapor byte'ları korunuyor ve yeni rapor ayrı artifact oluşturuyor. Veri yeniden import edilse bile eski özet/rapor özgün sürümünü kullanıyor.
- Türkçe CSV/Parquet kapsam/biçim/hedef alanları ve kayıtlı sonuç özeti ayrı geçici workspace ile yerel tarayıcıda kontrol edildi. Paket öz testi CSV/Parquet üretme, Parquet'i tekrar açma, indirme checksum'u ve rapor kapsam kontrolüyle genişletildi.

### C03 doğrulama sonuçları

- Tam test kümesi: **623 geçti**, iki mevcut pandas/tarih uyarısı; C02 sonrasına göre **43 yeni senaryo**. Hazırlanmış veri round-trip, Unicode/alıntı/yeni satır, tarih/null/tür, boş tablo, çok batch/sıra, yanlış sahiplik/bozuk kaynak, mevcut/race hedef, writer hatası, device/ADS, checksum ve stale UI bağlamları doğrulandı. Özet tüm outcome ve TR/EN biçimlerinde tutarlı; split/tanımsız ölçütler karıştırılmıyor ve veri değişince eski sonuç tekrar hesaplanmıyor.
- Arka uç branch coverage: **%86,73**, %80 eşik başarılı. UI/desktop bu yüzdeye dahil değildir; AppTest ve ayrı geçici workspace ile Türkçe tarayıcı görsel kontrolü ayrıca yapıldı.
- Ruff lint/format başarılı; **129 dosya** biçim kontrolünü geçti. Mypy **16 çekirdek dosyada** sorun bulmadı. Compileall, ilgili git diff --check ve uv lock --check --offline başarılı.
- Wheel/sdist yeniden üretildi. SHA-256 içeren kaynak yolundan izole kurulan güncel wheel; gerçek worker/rapor/yeniden açılış, snapshot, bakım ve yeni CSV/Parquet/özet öz testini geçti.
- Windows EXE yeniden üretildi: dist/AutoAnalyst.exe (**151.457.877 bayt**). --version ve erişilemeyen HTTP/HTTPS proxy'leriyle genişletilmiş --self-test başarılı. Export servisi/okuyucusu/UI, sonuç UI'ı, rapor özeti/render, ana UI, dil kataloğu ve öz test dahil dokuz modülün wheel kaynakları ve EXE derlenmiş kodu güncel kaynakla eşleşti. Ayrı Windows makinesi ve uzak CI bu yerel doğrulamanın kapsamında değildir.

## C04–E04 uygulama kapanışı — 8 Kasım 2026

Kalan paketlerin yerelde uygulanabilir kodu tamamlandı. Son birleşik doğrulama ve paket bilgileri `TAMAMLAMA_RAPORU.md` içindedir. Aşağıdaki dış kabul işleri tamamlanmış sayılmamaktadır.

| Paket | Uygulanan sonuç | Sınır / dış kabul |
|---|---|---|
| C04 | Ayrı import/preview worker'ı, RSS/süre/disk bütçeleri, iptal/yeniden bağlanma, bounded distinct sorgusu, streaming fingerprint, DuckDB spill sınırı, ölçüm aracı | Yerel sentetik benchmark; donanımdan bağımsız SLA veya p95 garantisi yok |
| C05 | TR/EN çevrimdışı yardım, dört sentetik örnek, görev ve sorun kayıt protokolü | Beş gerçek kullanıcı oturumu yapılmadı |
| D01 | Eğitim bölümünde 2–5 fold, grup/zaman koruması, fold içi pipeline, model kartı, validation permutation importance | İkili model denetimi; özgün seçim/model/holdout değişmez |
| D02 | Ortalama/oran CI, Welch fark CI, bağımsızlık onaylı Pearson CI, yöntem/kapsam kayıtları | Spearman CI yok; her istenen karşılaştırma tekil ve multiplicity düzeltmesiz |
| D03 | Validation sigmoid-CV kalibrasyon karşılaştırması, Brier/log-loss/reliability; yeni veri değişimi raporu | Kalibre modeli devreye alma veya otomatik yeniden eğitim yok; drift eşikleri sezgisel |
| D04 | Ayrı regresyon sözleşmesi; Dummy/Ridge/RF, MAE/RMSE/R²; kalıcı final kilidi ve eşlemeli skor | Rastgele/grup/zaman bölme; yeni modül için ayrı CV özelliği yok |
| D05 | Ayrı 3–20 sınıflı sözleşme; Dummy/LR/RF, accuracy/macro-weighted F1, sınıf metrikleri, validation confusion matrix | Pozitif sınıf/eşik dayatılmaz; yeni modül için ayrı kalibrasyon yok |
| E01 | Inner/left join, tür/kardinalite/çoğalma kontrolü, eşleşmeyen satırlar, kaynak lineage, migration 0006 | Null anahtar eşleşmez; sütun kimliği çakışan kendi/geçmiş sürüm join'i reddedilir |
| E02 | V2 kapalı komut adaptörü; belirsizlikte açıklama, açık plan/preview/apply, mevcut sürüm geri dönüşü | Belgelenmiş komut grameri; her doğal dil ifadesini anlama iddiası yok |
| E03 | Varsayılan kapalı metadata-izinli Ollama uyumlu JSON planlayıcı; token/süre/byte bütçesi, yerel doğrulama ve kural fallback | Gerçek model sunucusu entegrasyonu kullanıcı ortamında kabul bekler; test sağlayıcısı kullanıldı |
| E04 | Kayıtlı sonuçtan grafik içeren PDF ve gerçek XLSX; literal hücreler, dil, cache ve bütçeler | PDF görünüm sınırı açık; XLSX Excel limitlerinde hata verir, sessiz kesmez |

C04 referans ölçümleri: 25k×8 import 1,98 sn / 269,0 MiB; 100k×8 4,27 sn / 146,9 MiB; 20k×80 6,59 sn / 343,2 MiB; 1M×5 26,17 sn / 412,9 MiB. İki adımlı önizlemeler sırasıyla 0,97 / 0,94 / 0,98 / 1,75 sn. 50k×8 eski/yeni fingerprint aynı SHA-256'yı üretirken örneklenen süreç tepe RSS 285,9 MiB'dan 100,7 MiB'a indi. Tam donanım/bütçe/ölçüm kaydı `benchmarks/c04-local.json` içindedir. Süreler worker monitörü başladıktan sonraki sürelerdir.

PDF'nin Türkçe altı sayfalık örneği rasterize edilip tüm sayfalarda yazı/grafik/satır sonları görsel olarak incelendi. Otomatik testler PDF başlığı/sonu, XLSX açılışı, tablo değerleri, grafik kaynak/count eşitliği ve formül benzeri metnin literal kalmasını denetler. Paket öz testi gerçek PDF/XLSX üretimi/açılışı, iki yeni modelin alt süreçte eğitimi ve C04 iptal/head korumasını kapsar.

### Dış kabul işleri

1. A06: Ayrı, Python kurulu olmayan Windows makinesinde etkileşimli kullanıcı yolculuğu. Yerel EXE/offline öz test bunun yerine geçmez.
2. C05: Beş gerçek hedef kullanıcıyla pilot, görev başarısı ve ilk rapor süreleri; boş protokol hazırdır.
3. Dağıtım: Ürün sahibinin lisans ve imzalı dağıtım kararı. Bu çalışmada lisans seçilmedi, sertifika alınmadı, yayın yapılmadı.
4. E03: Kullanıcının seçtiği gerçek sunucu/model ile isteğe bağlı entegrasyon kabulü; model kurulumu veya ücretli hizmet çağrısı yapılmadı.
5. Uzak CI: Workflow kontrolleri yerelde uygulanır; uzak çalıştırma sonucu bu oturumda üretilmedi.


### Son birleşik doğrulama — 8 Kasım 2026

706 test geçti; iki mevcut pandas/tarih uyarısı. Backend branch coverage %86,73; kritik domain/preparation/holdout alt kümesi %93,04. Ruff 153 dosyada, Mypy 22 çekirdek dosyada başarılı. İzole wheel ve geçersiz uzak proxy ayarlarıyla EXE öz testleri başarılı. 97 frozen modül ve desktop giriş noktası güncel kaynakla eşleşti. Portable ZIP CRC/giriş/EXE SHA-256 kontrolü geçti. Paket hash ve boyutları TAMAMLAMA_RAPORU.md ile benchmarks/release-verification.json içinde.
