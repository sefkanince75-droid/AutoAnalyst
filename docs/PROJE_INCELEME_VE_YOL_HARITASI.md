# AutoAnalyst — Proje incelemesi ve geliştirme planı

İnceleme tarihi: **22 Ekim 2026**. İncelenen ürün: **2.0.0rc1**, varsayılan `app.py` → `autoanalyst.ui.app` yolu. Plan, mevcut çalışma kopyasını ve kaydedilmemiş dosyaları birlikte değerlendirir. Bu çalışma uygulama kodunu değiştirmez.

Bu belge ilk inceleme anındaki bulguları korur. Sonradan uygulanan düzeltmelerin ve güncel doğrulama sonuçlarının kaydı `UYGULAMA_DURUMU.md` dosyasındadır.

## 1. Karar özeti

AutoAnalyst'in en büyük ihtiyacı yeni algoritma eklemekten önce mevcut analiz motorunu güvenilir ve anlaşılır bir kullanıcı deneyimine dönüştürmektir. Kalıcı proje/veri sürümü, veri hazırlama, ikili sınıflandırma, son test kilidi, arka plan çalıştırma ve rapor altyapısı zaten vardır. Bunların bir kısmı kullanıcı arayüzünde eksik temsil edilmektedir.

Önerilen sıra:

1. Yanlış veri setine işlem uygulama ve yanlış rapor indirme sorunlarını düzelt.
2. İçe aktarma hata yönetimini ve tek bir dağıtım yolunu sağlamlaştır.
3. Veri önizleme, grafikler, Türkçe metinler, ilerleme takibi ve sürüme geri dönmeyi tamamla.
4. Yedekleme, depolama bakımı ve büyük veri davranışını kullanıcıya aç.
5. Pilot kullanım sonuçlarına göre regresyon, daha güçlü model değerlendirme veya güvenli doğal dil asistanından birini seç.

Hedef kullanıcı varsayımı: Python yazmadan CSV/XLSX incelemek, temizlemek, karşılaştırmak ve ikili sınıflandırma yapmak isteyen; Windows'ta, verisini yerelde tutan bireysel analist. Bu, doğrulanmış pazar araştırması değil, mevcut mimariye uygun planlama varsayımıdır. Akademik istatistik, kurumsal ekip kullanımı veya satış hedefi öncelikliyse özellik sıralaması yeniden yapılmalıdır.

## 2. Gerçekte ne var?

| Alan | Mevcut durum | Tamamlanması gereken |
|---|---|---|
| Proje ve veri | SQLite katalog, CSV/XLSX, Parquet, değişmez sürümler, SHA-256 | Veri satırı önizlemesi, içe aktarma seçenekleri, yönetim ekranları |
| Veri hazırlama | Kapalı işlem listesi, çok adımlı reçete altyapısı, tam veri önizlemesi, eski önizlemeyi engelleme | Arayüz tek adımlı; `replace_values` ve `append_rows` sunulmuyor; bağlam hatası var |
| Profil | Eksik değerler, tekrarlar, sayısal/kategorik özetler, histogram verisi | Üretilen grafiklerin ekranda ve HTML raporda gösterilmesi |
| Karşılaştırma | Dağılım, grup özeti, çapraz tablo, Pearson/Spearman, Welch, Fisher | Yöntem açıklamaları, güven aralıkları, uygunluk yönlendirmesi |
| Modelleme | Dummy, Logistic Regression, Random Forest; train-only ön işleme | Çapraz doğrulama, yorumlanabilirlik ve model karşılaştırma deneyimi |
| Model güvenilirliği | Grup/zaman/stratified ayrım, validation üzerinden eşik, kalıcı final holdout kilidi | Kullanıcıya kısıtların açık anlatımı ve sonlandırılmamış modelin tahmin seçiminden ayrılması |
| Çalıştırma | Worker, iptal, olaylar, bellek/süre watchdog, yeniden açılış kurtarma | Otomatik durum güncelleme, yeniden açılışta aktif işi bulma, ayrıntılı hata sunumu |
| Rapor | Kalıcı sonuçtan HTML/Markdown/CSV ZIP/JSON, formül enjeksiyonu koruması | Rapor seçiminin bağlama bağlanması, grafikler, tam çeviri, özet anlatım |
| Yedek | `WorkspaceSnapshotService` dışa/içe aktarma ve bütünlük kontrolü | Kullanıcı arayüzü, güven sınırı, uyumluluk ve boyut kontrolleri |
| Kalite | Testler, CI, kilitli bağımlılıklar, mimari sınır testleri | Kritik kullanıcı yolculukları, gerçek paket/worker senaryoları |
| Asistan | Eski `src/workspace_command_*` ve sohbet modülleri | Bunlar V2 ürün yoluna bağlı değil; V2 sözleşmelerine uyarlama gerekli |

Regresyon, çok sınıflı sınıflandırma, kümeleme ve zaman serisi tahmini README'de bilinçli kapsam dışıdır. Bunlar mevcut sözün tutulmaması değil, potansiyel ürün genişlemeleridir.

## 3. Yapılan doğrulamalar

| Kontrol | Sonuç | Yorum |
|---|---|---|
| `uv run --frozen --group dev pytest -q` | **327 geçti**, 3 uyarı | Windows, Python 3.12.11 |
| Tam test + branch coverage | **327 geçti**, **%83,36** | Yerel ölçüm %80 arka uç eşiğini geçti |
| Kritik modül alt kümesi | Yaklaşık **%92** | Tam koşunun coverage verisinden hesaplandı; CI'daki ayrı Linux koşusu çalıştırılmadı |
| Ruff lint | **1 hata** | `tools/build_icon.py:3`, I001 import sırası |
| Ruff format | **1 dosya başarısız** | Aynı dosya; diğer 92 dosya biçim kontrolünü geçti |
| Mypy, CI'daki çekirdek kapsam | **14 dosyada sorun yok** | Tüm kod tabanının tip kontrolü anlamına gelmez |
| `uv lock --check` | Başarılı | Kilit dosyası kontrolü |
| Compileall | Başarılı | `src`, `app.py`, `legacy_app.py` |
| Wheel ve sdist build | Başarılı | Build başarısı, temiz makinede uygulama açılışı değildir |
| `python -m autoanalyst.desktop --self-test` | Başarılı | Kaynak çalışma kopyasından; yeni EXE üretilmedi |
| Geçici çalışma alanıyla ek AppTest deneyleri | 2 bağlam hatası yeniden üretildi | Ayrıntılar aşağıda |
| Hatalı CSV deneyi | 1 kayıtsız nesne dosyası kaldı | Katalogda 0 artifact, dosya sisteminde 1 nesne |

Coverage hesabı `.coveragerc` gereği **V2 UI ve desktop modüllerini dışarıda bırakıyor**. Yüzde, bütün ürünün aynı ölçüde doğrulandığı şeklinde yorumlanmamalı. Mevcut V2 AppTest dosyasında proje yeniden açılışını kontrol eden tek test var. Worker coordinator yaklaşık %52, snapshot servisi yaklaşık %81 düzeyinde; bu alanların hata yolları ayrıca ele alınmalı.

Üç uyarı: test verisinde pandas dtype uyumsuzluğu, tarih ayrıştırmada biçim çıkarımı, yerel `.pytest_cache` yazma izni. Sonuncusu ortam sorunudur; ürün hatası olarak sınıflandırılmadı.

Doğrulama için kilitli bağımlılıklarla `.venv` oluşturuldu; derleme çıktıları `build/` ve `dist/` altında üretildi. Gerçek kullanıcı çalışma alanı yerine geçici dizinler kullanıldı. Depoda inceleme başında 61 değişmiş/izlenmeyen durum satırı vardı; mevcut değişiklikler korunmuştur. Uzak CI geçmişi, temiz Windows makinesi, imzalı EXE, gerçek kullanıcı verisi ve yük testi bu incelemede doğrulanmadı.

## 4. Öncelikli bulgular ve kabul kriterleri

Bu planda P0 = yayın öncesi doğruluk engeli; P1 = temel ürünün güvenilir kullanımını tamamlayan iş; P2 = pilot talebiyle sıralanacak iyileştirme; P3 = opsiyonel genişleme. Öncelik bir güvenlik açığı şiddet puanı değildir. F01/F02 geçici çalışma alanında davranış olarak doğrulandı; diğer bulgularda kod incelemesi ile deneysel kanıt ayrı belirtilmiştir.

### F01 — Veri seti değişince eski önizleme uygulanabiliyor — P0

**Kanıt:** `src/autoanalyst/ui/app.py:287–305`. `preview_id` oturum genelinde tutuluyor; seçili proje/veri sürümü ile eşleşme kontrol edilmiyor. A veri setinde oluşturulmuş önizleme varken B'ye geçip Uygula'ya basıldığında A'nın head sürümü değişti; B değişmedi; arayüz exception vermedi. Depolamadaki stale-preview koruması kendi veri setini kontrol ediyor, seçili ekran bağlamını bilmiyor.

**Çözüm:** Önizleme bağlamına `project_id`, `dataset_id`, `base_version_id`, reçete hash'i ekle. Bağlam veya form değiştiğinde geçersiz kıl. Uygulama öncesinde seçili veri seti/sürüm ile yeniden doğrula. Önizleme üzerinde hedef veri setini ve işlem özetini göster.

**Bitti sayılması:** A→B, proje A→B, sürüm değişikliği ve parametre değişikliği testlerinde eski önizleme uygulanamaz; A'nın head'i değişmez. Geçerli önizleme tek uygulamada doğru sürümü oluşturur; tekrar uygulama idempotent kalır.

### F02 — Eski rapor başka proje/sonuç altında indirilebiliyor — P0

**Kanıt:** `src/autoanalyst/ui/app.py:734–747`. `report_id` seçili sonuca bağlanmamış. İki projeli AppTest deneyinde ikinci proje seçildiğinde ilk projenin rapor ID'si korundu ve indirme düğmesi görünmeye devam etti. İndirme içeriği eski rapordan, dosya adı seçili yeni sonuçtan türetilebiliyor.

**Çözüm:** Raporu sonuç kimliği + biçim + dil + şablon/seçenekler anahtarıyla tut. `root_result_id` ve proje sahipliğini indirme öncesinde kontrol et. Dosya adını doğrulanan rapordan üret.

**Bitti sayılması:** Proje/sonuç/biçim/dil değişiminde yanlış dosya sunulmaz; içerik, dosya adı ve ekrandaki sonuç eşleşir. Aynı projedeki iki farklı sonuç da test edilir.

### F03 — İçe aktarma hata yönetimi ve nesne temizliği eksik — P1

**Kanıt:** `_data_tab` içindeki XLSX açma ve import çağrıları kullanıcıya uygun hata sınırına alınmamış. `data/ingest.py:67–77` ham artifact'i CSV ayrıştırılmadan kalıcı konuma taşıyor. Boş CSV ile denemede `DataError` sonrası katalogda 0 artifact'e karşılık `projects/.../objects` altında 1 dosya kaldı.

**Çözüm:** Ayrıştırma/doğrulama/publication adımlarını başarısızlıkta güvenli temizleme yapacak şekilde düzenle; yalnızca o denemeye ait dosyaları temizle. Hataları yerelleştirilmiş açıklama ve düzeltme önerisine çevir. Hata ayrıntısını veri içermeyen teşhis kaydına yaz.

**Bitti sayılması:** Boş/bozuk CSV, yanlış encoding, bozuk XLSX, eksik sayfa, limit aşımı ve başarısız publication ekranı çökertmez, head'i değiştirmez ve sahipsiz dosya bırakmaz.

### F04 — Motor yetenekleri arayüzde eksik — P1

**Kanıt:** `_data_tab` satır örneği yerine sütun metadatasını gösteriyor. Profil motoru `ChartSpec` üretiyor; `_results_tab` grafik döngüsü içermiyor. `_html` metrik/tablo üretiyor fakat `result.charts` çizdirmiyor. Bir profil deneyinde sonuçta 1 grafik nesnesi olduğu doğrulandı. Hazırlama motorunda 10 işlem var; arayüzde 8 işlem ve tek adımlı reçete var. `move_head`, proje rename ve snapshot servisleri UI'da erişilebilir değil.

**Çözüm:** Sayfalı veri görüntüleme, kapalı grafik renderer'ı, çok adımlı reçete editörü, sürüme geri dönüş ve yönetim ekranlarını mevcut servislere bağla. Grafikler kayıtlı sonuçtan çizilsin; rapor üretimi analizi tekrar çalıştırmasın.

**Bitti sayılması:** Kullanıcı veri örneğini görür; histogram ekranda ve çevrimdışı HTML'de aynı veriyi kullanır; append/replace erişilebilir; geri dönüş ve çok adımlı hazırlama lineage'ı korunur.

### F05 — Dil, form tipleri ve model yönlendirmesi — P1

**Kanıt:** Dil anahtarları sınırlı; analiz adları, birçok alan ve rapor bölümü İngilizce kalıyor. Hatalar/bulgular ham kod/sözlük olarak gösteriliyor. Filtre ve sabit doldurma alanları `text_input`; tipli veri sözleşmesine uygun kontrol seçilmiyor. `_score_form` eğitim adaylarını listelerken backend sonlandırılmış model şartını ayrıca uyguluyor.

**Çözüm:** UI/rapor/bulgu kataloğunu ortak TR/EN anahtarlara taşı. Sayısal/tarih/boolean alanlara tipli girdiler koy. Analiz uygunluğunu çalıştırmadan göster; scoring listesinde final durumunu açıkça ayır. Proje dil tercihinin açılışta uygulanmasını tanımla.

**Bitti sayılması:** Türkçe akışta istemsiz İngilizce metin ve ham hata kodu görünmez; yanlış tipler anlamlı biçimde engellenir; final yapılmadan tahmin düğmesi yönlendirme gösterir.

### F06 — Kaynak ve performans sınırları kullanıcı akışına tam yayılmamış — P1/P2

**Kanıt:** Analiz worker'ında kaynak tahmini ve bellek/süre gözetimi var. Ancak UI içe aktarma ve hazırlama önizlemesini doğrudan çalıştırıyor. CSV/XLSX pandas ile belleğe alınıyor; `_distinct_values` tüm sütunu pandas'a alıp sonradan ilk 200 farklı değeri seçiyor. UI sabit 2 GB bellek/4 GB disk/600 saniye bütçesi oluşturuyor. Dosya sınırları CSV 256 MiB, XLSX 64 MiB; bunlar çalışma anı bellek ihtiyacını garanti etmez.

**Çözüm:** Önce ölç; sonra import/preview işlerini kaynak kontrollü yürütmeye taşı. Tablo servisine limitli örnek ve farklı-değer sorgusu ekle. Büyük kardinaliteyi açıkça bildir, arama sun. Disk boşluğu ve geçici dosya bütçesini kontrol et.

**Bitti sayılması:** Tanımlı benchmark setinde süre, tepe RSS ve disk kullanımı kaydedilir; limit aşımı temiz hata verir; UI yanıt vermeye devam eder. Sadece dosya boyutuna bakarak büyük veri desteği ilan edilmez.

### F07 — Dağıtım yolları ve sürüm kaynakları ayrışıyor — P1

**Kanıt:** CI `src/autoanalyst/desktop.py` üzerinden onefile paketliyor. Yerel `build_desktop.bat` → `AutoAnalyst.spec` → `desktop_launcher.py` ise pywebview/onedir yolu kullanıyor. `pywebview` ayrı requirements dosyasında, `pyproject.toml` desktop grubunda yok. Paket sürümü rc1, Windows metadata 2.0.0. Oluşan wheel 5 SQL migration içeriyor; kök `app.py` içermiyor; tek console script worker. Desktop kodu kaynak çalışma kopyasındaki kök `app.py` konumuna bağımlı.

**Çözüm:** Bir resmi desktop build yolu seç; eski yolu kaldırmadan önce kullanımını belirle. Sürümü tek yerden türet. Wheel son kullanıcı dağıtımı olacaksa paket içi UI başlangıcı ve kullanıcı CLI'sı ekle; değilse README'de wheel'in amacını sınırla. Clean-machine/offline EXE testini gerçek import→analiz→rapor yolculuğuna genişlet.

**Bitti sayılması:** Aynı tag'den aynı sürüm/bağımlılık kümesiyle paket çıkar; Python kurulu olmayan Windows'ta açılır, analiz worker'ı çalışır ve yeniden açılışta sonuç korunur. Kaynak ağaç import'unu “kurulu wheel testi” yerine kullanma.

### F08 — Test miktarı iyi; kritik kullanıcı senaryoları eksik — P1

327 testin geçmesi F01/F02'yi yakalamadı. UI coverage dışında. Yeni testleri basit getter/setter tekrarları için değil, bağlam geçişi, worker başlatma/iptal, kurtarma, dosya hataları ve gerçek paket yolculuğu için ekle. Ruff hatasını ilk bakım paketinde çöz; mevcut yerel dosya izlenmediği için bunu uzak CI'ın kesin başarısız olduğu şeklinde sunma.

### F09 — Yedekleme kullanılabilirliği ve güven sınırı — P1

Snapshot servisi vardır; yeniden yazılmamalıdır. Dışarıdan alınan snapshot'ın checksum'ı, kaynağın güvenilir olduğunun kanıtı değildir. Model yüklemesi `analyses/binary/module.py:658–660` üzerinden `joblib.load` kullanıyor. Snapshot UI açılmadan önce yalnızca kendi güvenilir yedeğini geri yükleme akışı, güvenilmeyen pakette model yüklememe veya daha güvenli model biçimi kararı alınmalıdır. Bu incelemede kötü amaçlı model çalıştırılmadı.

Ayrıca arşiv açılmadan toplam açılmış boyut/girdi sayısı/disk kotası, sürüm uyumluluğu ve başarısız restore sonrası durum doğrulansın. Mevcut boş hedefe geri yükleme kuralı korunsun. scikit-learn, pickle tabanlı model yüklemenin güven sınırını ve farklı sürümler arası yüklemenin desteklenmediğini açıklar: [model persistence](https://scikit-learn.org/stable/model_persistence.html).

### F10 — Bakım ve ürün dokümantasyonu — P2

Eski ve yeni uygulama yolları, izlenmeyen modüller, eski v1.2 odaklı `proposal.md`, dağınık bağımlılık listeleri ve kullanıcı rehberi eksikliği bakım yükünü artırıyor. Mevcut dosyaları körlemesine silme. Kullanılan yolları belirle; V2 bağımlılık sınırını koru; legacy'yi açıkça etiketle. Lisans/lisans bildirimleri, CHANGELOG, örnek veri, sorun giderme ve desteklenen sınırlar dağıtım kontrol listesine alınsın. Lisans seçimi ürün sahibinin kararıdır.

## 5. Uygulama tasarımı

### Katmanlar

- UI: ekranlar, tipli kontroller, bağlama göre session state, TR/EN metinler.
- Application: import/preparation/run/report/backup kullanım senaryoları; seçili bağlam doğrulaması.
- Domain: değişmez plan ve sonuçlar; sürümlü parametre şemaları.
- Analyses: sayısal hesaplama ve yöntem uygunluğu; UI bağımlılığı yok.
- Execution: ağır işler, ilerleme/iptal, kaynak limiti, yeniden açılış kurtarma.
- Storage: atomik katalog publication, değişmez artifact, sahiplik, migration.
- Reporting: kayıtlı sonuçtan sunum; yeniden analiz yok.

Yaklaşık 880 satırlık mevcut UI modülü, davranış testleri eklendikten sonra ekranlara bölünsün. Toplu yeniden yazım yerine küçük, ölçülebilir değişiklikler tercih edilsin.

### Bağlam kuralları

Önizleme anahtarı: `(project_id, dataset_id, base_version_id, recipe_hash)`.

Rapor anahtarı: `(project_id, result_id, format, language, template_version, options_hash)`.

Çalışan işin kaynağı yalnızca `session_state` olmamalı; kalıcı run kaydından bulunmalı. Tek worker kuralı korunmalı. Ekran aktifken periyodik durum yenileme, kurulu Streamlit sürümüne uygun `st.fragment(run_every=...)` veya eşdeğer yaklaşım ile sağlanabilir: [Streamlit fragment dokümanı](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment).

### Modelleme kuralları

Yeni model, imputasyon, özellik seçimi, kalibrasyon veya hiperparametre araması eklendiğinde öğrenilen adımlar eğitim bölümü/fold'u içinde kalmalı. Final holdout seçim/tuning için kullanılmamalı. Var olan pipeline yaklaşımı korunmalı: [scikit-learn veri sızıntısı rehberi](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).

Model kartında hedef/pozitif sınıf, özellikler, split, veri sürümü, seed, kütüphane sürümleri, baseline, validation sonucu, final durumu ve kullanım sınırı gösterilsin. Model skoru kalibre edilmediyse olasılık güvenilirliği iddia edilmesin; mevcut `uncalibrated_model_score` bilgisi kullanıcıya taşınsın.

## 6. İş paketleri ve süre hesabı

Süreler **tahmindir**; mevcut kodun büyüklüğü, entegrasyon ve doğrulama işi dikkate alınmıştır. Birimli hız geçmişi veya gerçek kullanıcı görüşmesi bulunmadığından kesin teslim tarihi değildir.

- 1 kişi-gün = **6 saat odaklı geliştirme/doğrulama**.
- Haftada 5 kişi-gün; tek tam zamanlı geliştirici varsayımı.
- O = iyimser, M = en olası, P = kötümser kişi-gün.
- Beklenen süre: **E = (O + 4M + P) / 6**.
- Takvim bütçesi: **toplam E × 1,20**; %20 entegrasyon/geri bildirim payı.
- Saat karşılığı: bütçeli kişi-gün × 6.
- Her paketin süresi geliştirme, uygun test ve kısa dokümantasyonu içerir. C05 ayrıca kullanıcı pilotu ve ürün rehberidir.
- Örnek: A02 = (1 + 4×2 + 3)/6 = **2 kişi-gün**, pay ile **2,4 kişi-gün**.
- İki geliştirici için süreyi doğrudan ikiye bölme; state/storage/migration işleri seri bağımlıdır. İş dağılımı yapıldıktan sonra ayrıca takvim çıkar.

| ID | Öncelik | İş paketi | Bağımlılık | O | M | P | E (gün) | Kabul özeti |
|---|---|---|---|---:|---:|---:|---:|---|
| A01 | P1 | Depo sınırı, geliştirme başlangıcı ve lint | — | 0,50 | 1,00 | 2,00 | 1,08 | V2/legacy ve resmi komutlar belgeli; Ruff geçer |
| A02 | P0 | Önizleme bağlamı düzeltmesi | A01 | 1,00 | 2,00 | 3,00 | 2,00 | Veri seti/proje/parametre değişince eski preview uygulanamaz |
| A03 | P0 | Rapor bağlamı düzeltmesi | A01 | 0,50 | 1,00 | 2,00 | 1,08 | İçerik, sonuç ve dosya adı her geçişte eşleşir |
| A04 | P1 | Import hata sınırı ve rollback | A01 | 1,00 | 2,00 | 4,00 | 2,17 | Başarısız import head değiştirmez, nesne bırakmaz |
| A05 | P1 | Resmi paket yolu ve sürüm kaynağı | A01 | 2,00 | 4,00 | 7,00 | 4,17 | Tek dağıtım yolu; kaynak ağacı dışında başlangıç doğrulanır |
| A06 | P1 | Worker ve dağıtım yolculuğu testleri | A02–A05 | 2,00 | 3,00 | 5,00 | 3,17 | İptal/restart/offline/EXE yolculuğu geçer |
| B01 | P1 | Import sihirbazı ve satır tarayıcı | A04 | 2,00 | 4,00 | 6,00 | 4,00 | Delimiter/encoding, XLSX sayfa, limitli satır önizleme |
| B02 | P1 | UI/HTML grafik renderer ve sonuç düzeni | A03; B03 | 3,00 | 5,00 | 8,00 | 5,17 | Kayıtlı ChartSpec aynı verilerle UI ve raporda görünür |
| B03 | P1 | Tam TR/EN, tipli girdiler, açıklamalar | A02–A04 | 2,00 | 4,00 | 6,00 | 4,00 | Ham hata kodu yerine açıklama; tipe uygun alanlar |
| B04 | P1 | İlerleme, iş geçmişi ve yeniden bağlanma | A06 | 2,00 | 3,00 | 5,00 | 3,17 | Aktif iş restart sonrasında bulunur; otomatik güncelleme |
| B05 | P1 | Proje yönetimi ve sürüm geri dönüşü | A02 | 1,00 | 2,00 | 4,00 | 2,17 | Rename/restore doğru revision ve lineage üretir |
| B06 | P1 | Çok adımlı reçete, append ve replace | A02; B01; B03 | 3,00 | 5,00 | 8,00 | 5,17 | 10 hazırlama işlemi erişilebilir; sıra ve preview doğrulanır |
| C01 | P1 | Snapshot UI, güven ve uyumluluk | A05; B05 | 3,00 | 5,00 | 8,00 | 5,17 | Boş hedefe bütünlük kontrollü restore; güven sınırı açık |
| C02 | P2 | Depolama görünümü ve güvenli bakım | A04; C01 | 2,00 | 4,00 | 6,00 | 4,00 | Önce listeleme; aktif/referanslı artifact silinmez |
| C03 | P1 | Temiz veri export ve rapor özeti | B02; B03; B06 | 2,00 | 4,00 | 6,00 | 4,00 | CSV/Parquet yeniden açılabilir; özet sonuçla tutarlı |
| C04 | P1 | Kaynak kontrollü import/preview ve benchmark | A04; B01; B04; B06 | 4,00 | 7,00 | 12,00 | 7,33 | Ölçülmüş limitler, tepe RSS, iptal ve disk testi |
| C05 | P1 | Rehber, örnek veri ve kullanıcı pilotu | B01–B06; C01; C03; C04 | 2,00 | 4,00 | 6,00 | 4,00 | 5 kullanıcılı görev raporu ve öncelikli geri bildirimler |
| D01 | P2 | Çapraz doğrulama, model kartı, açıklama | C05 | 4,00 | 7,00 | 11,00 | 7,17 | Fold bazında leakage testleri; baseline ve önem açıklaması |
| D02 | P2 | Güven aralıkları ve istatistiksel yorum | C05 | 3,00 | 5,00 | 8,00 | 5,17 | Referans değerlere karşı test; yöntem/varsayım kaydı |
| D03 | P2 | Kalibrasyon ve veri değişimi uyarısı | D01 | 3,00 | 5,00 | 9,00 | 5,33 | Final set tuning için kullanılmaz; skor anlamı açık |
| D04 | P2 | Regresyon modülü | D01 | 4,00 | 7,00 | 11,00 | 7,17 | Dummy karşılaştırması; MAE/RMSE/R² ve kalıcı sonuç |
| D05 | P2 | Çok sınıflı sınıflandırma | D01 | 4,00 | 7,00 | 12,00 | 7,33 | Sınıf bazlı/macro metrikler; ayrı sözleşme ve rapor |
| E01 | P2 | Kardinalite kontrollü join | B06; C04 | 4,00 | 7,00 | 12,00 | 7,33 | Satır patlaması sınırı, unmatched özeti, provenance |
| E02 | P2 | Kural tabanlı V2 komut asistanı | B03; B06; C05 | 3,00 | 5,00 | 8,00 | 5,17 | Komut → kapalı plan → preview → apply; belirsizlik sorusu |
| E03 | P3 | İsteğe bağlı LLM planlayıcı | E02 | 5,00 | 9,00 | 15,00 | 9,33 | Şema doğrulama, veri izni, bütçe ve offline fallback |
| E04 | P2 | Grafikli PDF ve XLSX rapor | B02; C03 | 3,00 | 5,00 | 9,00 | 5,33 | Dosyalar doğru açılır; tablo/grafik kaynağı aynı sonuç |

### Aşama toplamları

| Aşama | İyimser toplam | En olası toplam | Kötümser toplam | Beklenen E | %20 paylı gün | Paylı hafta |
|---|---:|---:|---:|---:|---:|---:|
| A — Doğruluk ve dağıtım | 7,00 | 13,00 | 23,00 | 13,67 | 16,40 | 3,28 |
| B — Kullanılabilir V2 | 13,00 | 23,00 | 37,00 | 23,67 | 28,40 | 5,68 |
| C — Koruma, performans ve pilot | 13,00 | 24,00 | 38,00 | 24,50 | 29,40 | 5,88 |
| D — İleri analiz — seçmeli | 18,00 | 31,00 | 51,00 | 32,17 | 38,60 | 7,72 |
| E — Ürün genişletme — seçmeli | 15,00 | 26,00 | 44,00 | 27,17 | 32,60 | 6,52 |

### Kapsam senaryoları

| Senaryo | Kapsam | Beklenen gün | Paylı kişi-gün | Odaklı saat | Yaklaşık hafta |
|---|---|---:|---:|---:|---:|
| Mevcut kapsamın teknik yayın kapısı | A | 13,67 | 16,40 | 98,40 | 3,28 |
| Kullanılabilir V2 temel sürüm | A + B | 37,33 | 44,80 | 268,80 | 8,96 |
| Önerilen kapsamlı ürünleştirme | A + B + C | 61,83 | 74,20 | 445,20 | 14,84 |
| Bütün seçmeli paketler dahil üst kapsam | A + B + C + D + E | 121,17 | 145,40 | 872,40 | 29,08 |

D ve E toplamları, tüm seçeneklerin yapılması durumudur; önerilen zorunlu teslim kapsamına dahil değildir. A+B sonrası sınırlı beta ve C sonrası daha dayanıklı dağıtım mümkündür. Pilot kullanıcı bulma, onay veya dış hizmet bekleme süreleri hesaplanan mühendislik kapasitesine dahil değildir. İyimser/kötümser toplamlar istatistiksel güven aralığı sayılmaz. İlk 5–10 iş gününde ölçülen hız ve paket kararlarıyla tahminler yenilenmelidir.

Tek geliştiriciyle tabloda seçilen bütün işler kapasiteyi tüketir. Teknik bağımlılığın ana akışı: A01 → A02/A03/A04/A05 → A06; ardından B01/B03 → B02/B06 → C03/C04 → C05. C01/C02 ve B04/B05 için ayrı geliştirici varsa kısmi paralellik mümkündür; ortak storage değişiklikleri koordinasyon gerektirir.

## 7. Adım adım yürütme

### Aşama A — Yayın öncesi doğruluk ve dağıtım

1. A01: Mevcut değişiklik envanteri, V2/legacy sınırı, geliştirme komutları ve Ruff düzeltmesi.
2. A02 ve A03: Önizleme/rapor bağlam hataları ve bunları yakalayan kullanıcı yolculuğu testleri.
3. A04: İçe aktarma hataları, sahipsiz dosya sorunu, tutarlı kullanıcı mesajları.
4. A05: Resmi paket yolu, sürüm kaynağı, wheel kapsam kararı.
5. A06: Gerçek worker ve paket yolculuğu, iptal/yeniden açılış/çevrimdışı denemeleri.

**Çıkış kapısı:** Açık P0 yok; test/lint/type/build kontrolleri geçiyor; bağımsız Windows ortamında veri→analiz→rapor doğrulanmış. Bu, sınırlı mevcut kapsamlı sürüm için asgari teknik kapıdır; kapsamlı kullanıcı deneyimi B'de tamamlanır.

### Aşama B — Kullanılabilir V2 akışı

1. B01: CSV delimiter/encoding seçimi, XLSX sayfa kontrolü, satır örnekleri ve şema özeti.
2. B03: Merkezi dil kataloğu, tipli alanlar ve anlamlı bulgu/hata anlatımı.
3. B02: Kayıtlı grafiklerin UI/HTML renderer'ı ve özet sonuç düzeni.
4. B04: Otomatik ilerleme, olay ayrıntısı, restart sonrası aktif iş.
5. B05: Proje yeniden adlandırma ve sürüm geri dönüşü; arşiv için katalog değişikliği gerekiyorsa migration.
6. B06: Append/replace ve çok adımlı reçete düzenleme; önce/sonra örneği; adım bazlı uyarılar.

**Çıkış kapısı:** İlk kez kullanan kişi örnek CSV'yi yardım almadan açıp inceleyebilir, bir değişikliği önizleyip uygulayabilir, analizi çalıştırıp doğru raporu indirebilir. TR/EN akışları aynı işlevleri sunar.

### Aşama C — Veri koruma, performans, pilot

1. C01: Mevcut snapshot servisine yedekleme/geri yükleme ekranı; güven ve uyumluluk kuralları.
2. C02: Depolama kullanım ekranı, yalnızca referanssız nesneler için önce listeleyen bakım işlemi; aktif iş/preview/holdout referanslarını koruma.
3. C03: Temizlenmiş veriyi CSV/Parquet olarak dışa aktarma, rapor özetleri ve kapsam notları.
4. C04: Limitli tablo sorguları, import/preview kaynak kontrolü, benchmark ve darboğaz düzeltmeleri.
5. C05: Rehber, örnek veri, pilot ve geri bildirim doğrultusunda son düzenlemeler.

**Çıkış kapısı:** Yedekten dönülen proje/sürüm/sonuçlar eşleşir; beklenmedik kesinti veri bütünlüğünü bozmaz; ürünün desteklenen veri sınırları ölçümlere dayanır.

### Aşama D — Analiz kapasitesi; talebe göre seç

- D01: Kontrollü çapraz doğrulama ve model kartı; grup/zaman ayrımını koruyan fold'lar; sınırlı permutation importance ve karşılaştırma.
- D02: Welch/korelasyon gibi uygun yöntemlerde güven aralığı ve etki büyüklüğü; çoklu test varsa düzeltme politikasını belirtme; küçük örneklem uyarıları.
- D03: Kalibrasyon ve yeni veri karşılaştırması; Brier/reliability çıktıları, bilinmeyen kategori/eksiklik değişimi; yeniden eğitim için yalnızca uyarı.
- D04: Regresyon modülü; DummyRegressor, doğrusal ve ağaç tabanlı baseline; MAE/RMSE/R²; ayrı domain/applicability sözleşmesi.
- D05: Çok sınıflı sınıflandırma; macro/weighted metrikler, sınıf bazlı recall ve karışıklık matrisi; ikili pozitif sınıf/eşik sözleşmesini zorlamayan tasarım.

**Seçim kuralı:** Sayısal hedef talebi baskınsa D04; mevcut classifier'ın güvenilirliği önemliyse D01/D03; araştırma analizi baskınsa D02. Hepsi aynı sürüme zorunlu değildir.

### Aşama E — Opsiyonel ürün genişlemeleri

- E01: Güvenli join: anahtar/kardinalite doğrulaması, eşleşmeyen satırlar, çoktan-çoğa büyüme bütçesi, provenance. Mevcut append'in yerini almaz.
- E02: Eski kural tabanlı komut parser'ını V2 plan/recipe/spec üreten bir adaptöre taşı. Belirsizlikte soru, preview/apply, undo sürümü, kapalı işlem listesi.
- E03: İsteğe bağlı LLM planlayıcı. Kapalı şemaya çıktı üretir; keyfi Python/SQL çalıştırmaz. Varsayılan veri yerelde; sağlayıcıya ne gideceği açık, bütçe ve izin politikası belirli; LLM kapalıyken temel ürün çalışır. E02 ve değerlendirme veri seti tamamlanmadan başlamaz.
- E04: Grafik içeren PDF ve gerçek XLSX raporları. Mevcut CSV ZIP'in XLSX olmadığı kullanıcıya açıkça belirtilir. Sayısal kaynak yine kayıtlı AnalysisResult olur.

Kümeleme, zaman serisi tahmini, görüntü/NLP, canlı model API'si, bulut/çok kullanıcı ve dashboard builder daha sonraki ayrı ürün kararlarıdır. Kullanım kanıtı oluşmadan bu listeye bütçe bağlanmamalı.

## 8. İlk 10 iş gününün somut planı

Bu takvim ilk dilimdir; A aşamasının tamamının 10 günde biteceği taahhüdü değildir.

| Gün | İş | Gün sonu kanıtı |
|---|---|---|
| 1 | Değişiklik envanteri, release yolu kararı, Ruff | Tekrarlanabilir yerel kontrol komutları; dosya sahipliği listesi |
| 2 | Önizleme hatasının kalıcı regresyon testi | A→B ve parametre değişimi senaryoları önce kırmızı |
| 3 | Önizleme bağlam düzeltmesi | Senaryolar yeşil; doğru head hareketi |
| 4 | Rapor bağlam düzeltmesi | Farklı proje/sonuç/dil/format için doğru içerik |
| 5 | Import hata sınırı | Bozuk dosyalar kullanıcı mesajıyla karşılanıyor |
| 6 | Import rollback/nesne temizliği | Başarısız denemede katalog ve dosya sistemi tutarlı |
| 7 | Paket giriş noktası ve sürüm tekilleştirme | Seçilen resmi build yolu belgeli |
| 8 | Temiz ortam kurulum/build denemesi | Kaynak ağacına gizli bağımlılık listesi kapanıyor |
| 9 | Worker başlangıç/iptal/kurtarma | Gerçek alt süreçle deterministik kontroller |
| 10 | Birleşik regresyon ve öncelik güncelleme | P0 durumu, kalan dağıtım işi ve B kapsamı net |

## 9. Başarı ölçümü ve test matrisi

### Kullanıcı yolculukları

1. İlk açılış→proje→CSV/XLSX→satır örneği→profil→HTML indirme.
2. Hazırlama→preview→proje/veri seti/parametre değişikliği→eski preview reddi.
3. İki proje ve iki sonuç arasında gidip gelme→doğru rapor.
4. Çok adımlı reçete→apply→sürüm geri dönüşü→yeniden açılış.
5. Eğitim→validation eşik seçimi→final kilidi→yeni veri eşleme→tahmin.
6. Çalışırken iptal ve uygulamayı kapatma→yeniden açılışta tutarlı durum.
7. Snapshot→başka boş çalışma alanına restore→hash/sürüm/sonuç eşitliği.
8. Bozuk dosya, disk yetersizliği, bellek/süre aşımı→temiz hata ve korunmuş katalog.

### Veri çeşitliliği

Türkçe karakterler; UTF-8/BOM ve desteklenmesine karar verilen encoding'ler; virgül/noktalı virgül; farklı ondalık/tarih biçimleri; boş/tekrarlı başlıklar; tümü eksik sütun; yüksek kardinalite; dengesiz sınıf; grup ve zaman ayrımları; bilinmeyen kategoriler; sıfır satır üreten hazırlama. Ondalık/tarih seçenekleri backend sözleşmesine eklenmeden UI'da varmış gibi sunulmasın.

### Performans deneyi

Referans makine: örneğin 16 GB RAM, SSD ve 4+ CPU çekirdeği; gerçek donanım sonuçla birlikte kaydedilsin. 10 bin, 100 bin ve 1 milyon satırlı sentetik setler; 20 ve 100 sütun; yüksek kardinaliteli ayrı set; 64 MiB'a kadar XLSX. Her dosya mevcut byte limitine de uymalı. 1 milyon satır, destek vaadi değil ölçüm senaryosudur.

Kaydedilecekler: içe aktarma süresi, tepe bellek, disk çoğalması, ilk tablo görünümü, profil/preview süresi, iptal gecikmesi. Başlangıç hedefleri: önizleme ilk 100 satırı p95 <2 saniye, aktif iş göstergesi ≤2 saniyede yenileme, iptal isteği arayüzde ≤1 saniyede görünür. Worker durdurma süresi ayrı ölçülsün; iptal isteği ile gerçek sonlanma karıştırılmasın. Bu değerler mevcut performans ölçümü değil, önerilen kabul hedefleridir.

### Pilot

En az 5 hedef kullanıcıyla aynı 5 temel görev; görevlerin ≥%80'i geliştirici yardımı olmadan tamamlansın; ilk rapora ulaşma hedefi ≤10 dakika. Ham kullanıcı verisini telemetriye taşımadan görev başarısı/hata ve süre kaydı. Küçük pilotun istatistiksel pazar kanıtı olmadığı belirtilsin. Başarısızlıklar yeni algoritmalardan önce B/C paketlerine geri beslensin.

## 10. Maliyet, risk ve kararlar

Gerçek parasal maliyet için günlük ücret, ekip ve dağıtım modeli bilinmiyor. **İşçilik = bütçeli kişi-gün × günlük ücret**; ya da **bütçeli saat × saatlik ücret**. İkisini birlikte ekleme. İmza sertifikası, donanım/CI ve isteğe bağlı LLM hizmeti ayrı kalemlerdir; burada piyasa fiyatı uydurulmamıştır.

| Risk | Önlem | Karar noktası |
|---|---|---|
| Bağlam karışması | F01/F02 ve uçtan uca testler | Herhangi bir sürümden önce |
| Yeni özelliklerle veri sızıntısı | Train/fold içi öğrenme, holdout değişmezliği | Her modelleme paketi |
| Büyük veri bellek/disk baskısı | Benchmark, kaynak bütçesi, limitli sorgu | Boyut iddiası/dağıtım öncesi |
| Snapshot/model güveni | Kaynak güveni, boyut sınırı, uyumluluk, veri-only alternatifi | Backup UI yayınından önce |
| V1/V2 bakımının ikiye katlanması | Resmi yol ve legacy sınırı | A01/A05 |
| Paket yerelde çalışıp kullanıcıda açılmaması | Python'sız Windows, offline worker yolculuğu | A06 |
| Kapsam büyümesi | A/B/C kapıları, D/E için pilot talebi | Her aşama sonunda |

İlk sprint sonunda ürün sahibiyle doğrulanacaklar: hedef kullanıcı ve ilk 3 görevi; resmi desktop deneyimi (tarayıcı mı pencere mi); desteklenen veri boyutu/donanım; kişisel mi ticari dağıtım mı; çevrimdışı zorunluluğu; D/E'den ilk talep. Bunlar bug düzeltmelerini başlatmayı engellemez; ileri aşamaların kapsamını belirler.

**Yayın tanımı:** Kodun birleşmesi tek başına yeterli değildir. İlgili kabul testleri, kullanıcı metinleri, geri dönüş yolu, migration uyumluluğu ve hedef paket doğrulaması birlikte tamamlanmalıdır.

## 11. Kod kanıtlarına hızlı erişim

Bu bağlantılar incelemenin yapıldığı yerel çalışma kopyasına aittir; dosyalar değişirse satır numaraları kayabilir.

- [V2 giriş noktası](../app.py#L14)
- [Önizleme state ve apply akışı](../src/autoanalyst/ui/app.py#L287)
- [Rapor state ve indirme akışı](../src/autoanalyst/ui/app.py#L734)
- [CSV artifact publication sırası](../src/autoanalyst/data/ingest.py#L67)
- [Kapalı hazırlama işlem listesi](../src/autoanalyst/preparation/operations.py#L17)
- [Mevcut yedekleme servisi](../src/autoanalyst/application/snapshots.py#L29)
- [Sürüm geçmişi ve head değiştirme servisi](../src/autoanalyst/application/datasets.py#L49)
- [Model yükleme sınırı](../src/autoanalyst/analyses/binary/module.py#L658)
- [Mevcut V2 UI testi](../tests/v2/test_ui_apptest.py#L14)
- [Coverage kapsamı](../.coveragerc#L1)
- [CI kontrol listesi](../.github/workflows/ci.yml#L1)
- [Resmi release workflow](../.github/workflows/release.yml#L1)
- [Paket ve bağımlılık tanımı](../pyproject.toml#L1)
