# AutoAnalyst V2 geliştirme ve dağıtım

## Ürün ve kod sınırı

Resmi ürün yolu `app.py` → `src/autoanalyst/ui/app.py` ve `autoanalyst` Python paketidir. UI ekranları uygulama servislerini çağırır; sayısal hesaplama ve depolama Streamlit'e bağımlı değildir. Mimari sınırlar `tests/v2/test_architecture_boundaries.py` ile korunur.

`legacy_app.py` ve kök `src/*.py` modülleri eski uygulama/reference yoludur. V2 içine bu modüllerden import eklenmemelidir. Mevcut yerel değişiklikler, eski desktop launcher/spec ve referans dosyaları otomatik silinmez. `desktop_launcher.py`, `AutoAnalyst.spec` ve `build_desktop.bat` alternatif eski/pencere deneyimini içerir; resmi V2 release komutu aşağıdadır. Bu yolların ürün geçişi ayrıca ele alınır.

## Ortam ve çalışma

Python 3.12 x64 kullanılır. `uv.lock` sürüm kontrollüdür. Normal geliştirmede:

```powershell
uv sync --frozen --group dev
uv run --frozen autoanalyst
# Alternatif olarak:
uv run --frozen streamlit run app.py
```

Kullanıcı verisi varsayılan olarak `%LOCALAPPDATA%/AutoAnalyst/workspace` altında tutulur. Test/deneme verisi için ayrı bir `AUTOANALYST_WORKSPACE` dizini seçin; gerçek çalışma alanını test temizliğine dahil etmeyin.

Windows'ta aynı `.venv` üzerinde iki `uv sync`/otomatik senkronizasyon işlemi eşzamanlı çalıştırılmamalıdır. Ortam kurulduktan sonra paralel bağımsız kontrollerde `.venv\Scripts\python.exe`, `.venv\Scripts\ruff.exe` ve `.venv\Scripts\mypy.exe` kullanılabilir.

## Doğrulama

```powershell
uv lock --check
uv run --frozen ruff check src/autoanalyst tests/v2 tools
uv run --frozen ruff format --check src/autoanalyst tests/v2 tools
uv run --frozen mypy src/autoanalyst/domain src/autoanalyst/analyses/contract.py src/autoanalyst/application src/autoanalyst/execution/protocol.py
uv run --frozen playwright install chromium
uv run --frozen pytest -q
uv run --frozen python -m autoanalyst.desktop --self-test
```

Pytest önbelleği için yerel izin hatası varsa `-p no:cacheprovider` eklemek test kapsamını değiştirmeden önbelleği kapatır. Arka uç coverage kapsamı `.coveragerc` içindedir; UI/desktop coverage yüzdesine dahil değildir. Bu nedenle gerçek UI yolculukları ve paket öz testi ayrıca çalıştırılır.

## Resmi Windows portable build

Yerel ve CI aynı komutu kullanır:

```powershell
uv sync --frozen --group dev --group desktop
uv run --frozen --group desktop python tools/build_release.py
.\dist\AutoAnalyst.exe --version
.\dist\AutoAnalyst.exe --self-test
```

Build çıktısı `dist/AutoAnalyst.exe`, ara dosyalar `build/v2-release/` altındadır. Kaynak ağaçtaki `AutoAnalyst.spec` üzerine yazılmaz. Giriş dosyası wheel/EXE içinde bulunan `autoanalyst/ui/entrypoint.py` dosyasıdır; son kullanıcının çalışma dizininde `app.py` bulunmasına gerek yoktur.

Öz test, geçici bir çalışma alanında CSV içe aktarır, gerçek analiz worker'ını başlatır, sonucu uygulamayı yeniden açarak katalogda yayımlar, HTML rapor üretir ve tekrar açıp aynı içeriği okur. Kullanıcının mevcut verisini kullanmaz. `--version` yalnızca sürümü gösterir.

Tek sürüm kaynağı `src/autoanalyst/__init__.py` içindeki `__version__` değeridir. Setuptools paket sürümünü buradan alır; resmi Windows build metadata dosyasını da aynı değerden üretir. RC sürümü dosyanın StringFileInfo alanlarında korunur; Windows sayısal sürüm alanı dört tam sayıdan oluşur.

## Wheel'i kaynak ağacı dışında doğrulama

```powershell
uv run --frozen python -m build
uv export --frozen --no-dev --no-emit-project --output-file build/runtime-requirements.txt
$wheel = (Get-ChildItem dist/autoanalyst-*.whl | Select-Object -First 1).FullName
$wheelDigest = (Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash.ToLowerInvariant()
$wheelStage = Join-Path "build/wheel-check" $wheelDigest
New-Item -ItemType Directory -Force -Path $wheelStage | Out-Null
$checkedWheel = Join-Path $wheelStage (Split-Path $wheel -Leaf)
Copy-Item -LiteralPath $wheel -Destination $checkedWheel -Force
uv run --no-project --python 3.12 --with-requirements build/runtime-requirements.txt --with "$checkedWheel" python -I -m autoanalyst.desktop --self-test
```

İzole Python modu kaynak dizininin ve `PYTHONPATH` değerinin kurulu paket testini yanlışlıkla başarılı göstermesini engeller. Dış ortam bağımlılıkları aynı kilit dosyasından export edilir. CI hem bu testi hem kaynak çalışma kopyasının öz testini çalıştırır; release workflow ayrıca EXE öz testini proxy'ler kapalıyken çalıştırır.

Wheel önce SHA-256 içeren bir dizine kopyalanır. Aynı sürüm ve dosya yoluna yeniden build yapılınca `uv run --with` eski geçici ortamı kullanabildiği için yalnızca `--reinstall-package` yeterli olmayabilir. İçeriğe bağlı kaynak yolu farklı artifact'lerin aynı kurulum önbelleğine düşmesini önler; bağımlılık önbelleğini silmez. B04 doğrulamasında kurulu kurtarma modülünün son revizyonu ayrıca kontrol edildi.

## Hata teşhisi ve veri koruma

### İçe aktarma önizlemesi ve veri tarayıcısı

CSV ayırıcısı ve kodlaması ya da XLSX sayfası seçildikten sonra kullanıcı önce **İçe aktarmayı önizle**, ardından **İçe aktar** adımını kullanır. Önizleme en fazla 100 satır gösterir; başka satır olup olmadığını anlamak için okuyucu en fazla 101 veri satırı ister. Önizleme sürüm/artifact oluşturmaz. Dosya içeriği, hedef veri seti veya ayrıştırma ayarları değiştiğinde oturumdaki eski örnek geçersizleşir. Örnekteki türler kesin tam-dosya şeması olarak sunulmaz.

Varsayılan CSV kodlaması `utf-8-sig` olup BOM içeren/içermeyen UTF-8 için uygundur. `utf-8`, `cp1254`, `iso8859-9` ve `utf-16` seçenekleri de vardır. Ayırıcı ve kodlama kayıtlı parse contract'a aktarılır. Tek sütun bulunduğunda ayırıcı kontrolü önerilir; tek sütunlu geçerli dosyalar engellenmez.

Veri tarayıcısı `TableAccess.page` üzerinden parametrik `LIMIT/OFFSET` ve katalogdaki fiziksel sütun kimlikleriyle çalışır. Sayfa en fazla 500 satır ve 50 sütun döndürür; satırlar sabit iç sıra ile sıralanır. Değerler/sütunlar arayüzde kullanıcı adlarıyla gösterilir. Veri sürümü ve sayfa boyutu değiştiğinde sayfa girdisi ilk sayfaya döner. Sayfalama tam tablonun Python/Arrow tablosuna dönüştürülmesini önler; artifact bütünlük doğrulaması ve DuckDB sıralamasının disk/CPU maliyetini ortadan kaldırmaz. Büyük veri benchmark ve kaynak sınırları C04 kapsamındadır.

İçe aktarma hataları TR/EN düzeltme önerisi ve teşhis numarasıyla gösterilir. Çalışma alanının `logs/import-errors.jsonl` kaydı yalnızca numara, UTC zaman, sabit neden kodu ve exception türünü içerir. Ham exception metni, dosya adı/yolu ve veri satırları kayda yazılmaz.

İçe aktarma ayrıştırma/doğrulama tamamlanmadan ham dosyayı kalıcılaştırmaz. Sonraki bir hata durumunda yalnızca bu denemenin oluşturduğu ve katalogda kaydı bulunmayan nesneler temizlenir. Katalog commit'i başarılı olduktan sonra oluşan hata, kayıtlı nesnelerin silinmesine yol açmaz. Ani süreç sonlanmasında kalan eski kayıtsız nesneler C02 inceleme/karantina akışında değerlendirilebilir.

## Ortak dil kataloğu ve tipli formlar

`src/autoanalyst/localization.py`, UI ve HTML/Markdown raporlarının ortak TR/EN kataloğudur. Yeni anahtarları iki dile de aynı biçimlendirme parametreleriyle ekleyin; `tests/v2/test_localization.py` bu eşitliği denetler. Kullanıcı sütun adları ve serbest metinleri çevrilmez. Ham exception metni kullanıcıya gösterilmez; tanınmayan hata ve bulgular yerelleştirilmiş genel açıklamaya döner. JSON/CSV dışa aktarma anahtarları ile teknik yöntem/kaynak bilgisi dil değişiminden etkilenmez.

Proje varsayılan dili ilk açılışta ve proje değişiminde kullanılır. Kullanıcı dil seçimini açıkça değiştirmişse proje değişimi bu tercihi ezmez. Dil değişimi form değerlerini ve geçerli hazırlama önizlemesini korur. Rapor sunum değişikliklerinde `reporting/render.py` içindeki `TEMPLATE_VERSION` revizyonunu artırın; önbellek eski dosyanın üzerine yazmadan yeni rapor üretmelidir.

`ui/typed_inputs.py`, fiziksel/semantik sütun türüne göre giriş üretir. Sayısal değerler sayı, mantıksal değerler gerçek `bool`, tarih/saat değerleri ISO metni olarak kapalı reçeteye aktarılır. Büyük tam sayılarda kesin giriş seçeneği JavaScript hassasiyet sınırını aşan değerleri yuvarlamadan doğrular. Farklı türlerden seçili sütunlara tek sabit doldurma ve sayısal olmayan sütunlara ortalama/medyan doldurma önizlemeye açılmaz. Filtre işleçleri türle sınırlandırılır. Welch formu bağımsızlık onayı ister; yeni veri puanlama formu yalnızca son değerlendirmesi tamamlanan modelleri sunar. Arka uç doğrulaması ayrıca devam eder.

## Kayıtlı grafik sunumu

`reporting/charts.py`, yalnızca `AnalysisResult`, `ChartSpec` ve `ResultTable` kullanır; veri setine, analize veya UI'a erişmez. Şu anda motorun ürettiği `histogram` türü desteklenir. `x`, `x2`, `y` alanlarının kayıtlı tablodaki farklı sütunlara bağlanması, sayısal/sonlu değerler, pozitif aralık genişliği, sıralı/çakışmayan aralıklar ve negatif olmayan tam sayı frekansları doğrulanır. En fazla 1000 aralık çizilir; aşımda kısmi veya yeniden örneklenmiş grafik yerine açıklama gösterilir. Mevcut profil motoru en fazla beş histogram ve histogram başına en fazla 100 aralık üretir.

Çizici kayıtlı aralıkları birleştirmez veya histogramı yeniden hesaplamaz. SVG'de her çubukta özgün aralık/sayı bilgisi ve açıklayıcı başlık bulunur; metinler kaçışlanır. SVG erişilebilir başlık/açıklama, yerel yazı tipleri ve kendi şekillerini içerir; betik veya dış kaynak gerektirmez. UI, aynı SVG'yi `st.image` ile gösterir. HTML içine aynı SVG yerleştirilir ve kaynak tabloya bağlantı eklenir. Başlık için sütun adı kayıtlı profil tablosundan alınır; güncel veri seti şeması kullanılmaz. Desteklenmeyen/eksik/boş/geçersiz grafiklerde yerelleştirilmiş uyarı verilir, sonuç tabloları erişilebilir kalır.

`ui/result_view.py`, en fazla dört boyutsuz/sonlu kayıtlı ölçütü özet kartları olarak sunar; skor veya oran türetmez. Tüm ölçütler ve kaynak tablolar açılır detay alanlarındadır. HTML aynı özet seçimini kullanır. Markdown, CSV ve JSON grafik görseli içermez; kayıtlı veriyi dışa aktarmaya devam eder. Şablon revizyonu `presentation-3` olarak artırılmıştır. Paket öz testi, gerçek worker'ın ürettiği kayıtlı grafiğin HTML raporunda birebir yer aldığını da doğrular.

## Aktif çalışma ve ilerleme takibi

`ui/run_monitor.py`, aktif analizleri `RunStore.active_analysis_runs()` ile çalışma alanı genelinde bulur. Başlatan tarayıcının oturum kaydı zorunlu değildir. Tek ağır worker alanı projeler arasında ortak olduğu için diğer projedeki iş de sahibi açıkça belirtilerek gösterilir; birden fazla bekleyen kayıt varsa izlenecek çalışma seçilebilir. Yeni analiz başlatma kontrolü de kalıcı aktif kayıtları kullanır. Worker rezervasyonu başarısız olan yeni kayıt terminal `failed` durumuna geçer; başka çalışmanın lease'i kaldırılmaz. Eski, başlatılmamış `pending` kayıt kullanıcı tarafından iptal edilebilir; otomatik kuyruk çalıştırılmaz.

Panel aktifken `st.fragment(run_every=1.0)` ile yalnızca izleme alanı yenilenir. Yeni iş başlatılınca tek tam rerun zamanlayıcıyı kurar. Terminal durum algılanınca sonuç/bildirim için tek tam rerun yapılır ve aktif iş kalmamışsa zamanlayıcı kurulmaz. Tarayıcı açık olmalıdır; bu bir arka plan bildirim servisi değildir. İptal mevcut sahiplik doğrulamalı koordinatör yolunu kullanır. Geçmişte çalışma seçimiyle olay ayrıntıları açılabilir.

`execution/progress.py`, worker'ın atomik `progress.json` dosyasından en fazla 64 KiB okur. Manifest sürümü, çalışma kimliği, UTC zamanı, adım ve 0–1 arası sonlu sayısal ilerleme doğrulanır. Dosya okunamazsa analiz durumu değiştirilmez. Ham payload atılır; yalnızca izinli model kimliği aktarılır. Koordinatör/kurtarma, gözlenen ilerlemeyi app tarafında SQLite'a kaydeder; worker SQLite yazmaz. Aynı adım/fraction/model bilgisi aynı transaction içinde tekilleştirilir. Bu geçmiş worker'ın tüm ara mesajlarının kayıpsız günlüğü değil, gözlenen ilerleme örnekleridir.

Profil modülü yükleme ve sütun adımlarını, karşılaştırma/hazırlama modülleri işlem sınırlarını bildirir; eğitim mevcut model adımlarını bildirir. Gösterilen yüzde mevcut adıma aittir, tüm analizin yüzdesi veya kalan süre değildir. 15 saniyedir yeni ilerleme yoksa işlem hâlâ sürebileceği açıklanır; otomatik başarısızlık ilan edilmez. Olay ekranı son 100 kayıtla, servis sorgusu en fazla 500 kayıtla sınırlanabilir. Bilinmeyen kod/adımlar açıklayıcı genel metne döner; ham hata/payload gösterilmez.

Birden fazla oturum aynı tamamlanmış manifesti gözleyebilir. Sonuç yayımı `BEGIN IMMEDIATE` yazma kilidi içinde durum tekrar kontrol edilerek tekilleştirilir. Diğer oturumun tamamladığı terminal geçişi yeniden uygulanmaz. Böylece sonuç, holdout yayımı ve tamamlanma olayı tek kayıt olarak kalır.

## Proje yönetimi ve sürüm geri dönüşü

`ui/project_management.py`, yan panelden yeniden adlandırma, arşivleme ve geri getirmeyi sunar. Callback'ler ekranda görülen proje revision'ını `ProjectService` üzerinden kataloğa iletir; başka oturumun değişikliği ezilmez. Ad değiştiğinde seçili proje aynı UUID ile korunur. Aynı adlı projeler kısa kimlikleriyle ayrılır. Proje arşivlenince aktif listeden çıkar, arşiv listesinden geri getirilebilir; son proje arşivlense bile geri getirme alanı görünür. Arşivleme aktif `pending/running` iş varken transaction içinde reddedilir. Veri/sürüm/sonuç/rapor dosyaları silinmez.

`ProjectService.list()` varsayılan olarak arşivlenmemiş projeleri döndürür; tüm projeler için `include_archived=True` kullanılır. `get()` ve ham katalog sorguları arşivlenmiş kayıtları okuyabilir. Arşivleme kullanıcı arayüzünde düzenleme/ayırma işlemidir; yedekleme veya erişim yetkilendirmesi değildir. `archived_at` alanı mevcut ilk migration'da bulunduğu için yeni migration veya snapshot şeması değişikliği gerekmedi. Proje revision'ı rename/archive/restore işlemlerinde artar; aynı arşiv durumunu tekrar istemek yeni revision üretmez.

`ui/version_navigation.py`, Geçmiş sekmesinde seçili veri setinin güncel sürümü dışındaki kayıtlı sürümleri sunar. Seçilen sürümden en fazla 25 satır/20 sütun `TableAccess.page` üzerinden doğrulanarak gösterilir. Önizleme sürüm veya artifact üretmez. İnceleme bağlamı proje, veri seti, güncel head, head revision ve hedef sürüme bağlıdır; bunlardan biri değişince uygulama düğmesi kaldırılır. Aynı sürüm kimliğine gidilip geri gelinmesi de revision değişimiyle saptanır.

Yeni `DatasetService.restore_version`, proje/veri seti sahipliği, beklenen head/revision ve hedef artifact'in bütünlüğünü doğrular. Katalog `move_head` yazma transaction'ında bağlamı tekrar denetler. Başarı yalnızca head işaretçisini ve monoton head revision'ı değiştirir; `restore_version` nedenli head olayı kaydedilir. Sürüm verisi, reçete ve lineage değişmez, ileri sürüme tekrar geçilebilir. Aynı review'nun tekrar uygulanması eski revision ile reddedilir. Hazırlama ve geri dönüş önizlemeleri başarılı işlemden sonra temizlenir; eski hazırlama önizlemesi head tekrar aynı sürüme dönse de revision nedeniyle uygulanamaz. Eski sonuç/rapor/model kilitleri kendi kayıtlı girdilerine bağlı kalır; çalışan analizler mevcut girdi sürümlerini kullanır.

## Çok adımlı hazırlama reçeteleri

`ui/preparation_panel.py`, on kapalı işlemi tek adım veya çok adımlı taslak olarak sunar. `ui/recipe_editor.py`, en fazla 20 adımı ekleme/düzenleme/taşıma/kaldırma ve sütun kimliğini koruyan geçici şema görünümünü yönetir. Yeniden adlandırma, sütun kaldırma ve tür dönüşümü sonraki form alanlarına yansır; düzenleme formu seçili adımdan önceki şemayı kullanır. Geçici şema görünümü doğrulamanın yerine geçmez: tam veri önizlemesinde motor tüm sıralı işlemleri yeniden doğrular. Bozuk sıra, örneğin kaldırılmış sütunu kullanan sonraki adım, kullanıcı tarafından düzeltilmeden yeni adım eklenemez.

Taslak tarayıcı oturumundadır; kayıtlı reçete önizleme sırasında oluşturulur ve uygulanan sürüme bağlanır. Taslakta listelenmeyen form düzenlemeleri reçeteye katılmaz; kullanıcı önce adımı eklemeli/kaydetmelidir. Önizleme bağlamı tam sıralı adımlar ile form/proje/veri seti/base/head revision değerlerini kapsar. Düzenleme/sıralama/silme ve bağlam değişimleri eski önizlemeyi geçersizleştirir; dil değişimi taslağı ve geçerli önizlemeyi korur. Tam taslak tek immutable sürüm olarak uygulanır; orijinal veri ve önceki sürümler değişmez.

Değer değiştirme en fazla 20 tür koruyan tam eşleşme çifti sunar; kaynak/hedef eksik değer açıkça seçilebilir. Aynı adımın çiftleri başlangıç değerlerine eşzamanlı uygulanır; örneğin 1→2 ve 2→3 sonucu [2,3] olur. Özgün eşleştirme değerleri tekrarlanamaz. Satır ekleme yalnızca aynı projedeki, sütun kimliği/fiziksel adı/sırası/normalize türü uyumlu kayıtlı sürümleri listeler. Bu mevcut motor sözleşmesidir; başlıkları aynı ayrı import'lar farklı sütun kimlikleri taşıdığı için bu dilimde otomatik eşlenmez. Kaynak seçimi sırası reçeteye ve lineage'a aktarılır. Aynı girdi sürümü bir reçete içinde tekrar eklenemez.

Append sırasında iç sıra numarası satır sayısından değil mevcut en büyük iç sıradan başlatılır; böylece filtre sonrası oluşan sıra boşlukları çakışma üretmez. Eklenen satır kimlikleri taban sürümü + girdi sürümü + kaynak satır kimliği üzerinden deterministik türetilir; aynı kaynak sonraki bir reçetede tekrar kullanılsa bile mevcut satır kimliğiyle çakışmaz. Motor adım hatalarına sıfır tabanlı `step_position` ekler; UI bunu yerelleştirilmiş adım numarasıyla açıklar. Önceki adımların başarılı olması son adım başarısızken kısmi sürüm yayımlamaz.

`PreparationService.preview_samples` aday artifact'in sahiplik/bütünlük/son kullanma bilgilerini kontrol eder; taban ve nihai adaydan en fazla 25 satır/20 sütun gösterilir. Aday Parquet kontrollü sütun projeksiyonuyla bounded batch olarak okunur. Bu iki tablo ilk satır örnekleridir; filtre/append nedeniyle satır satır eşleştirme iddiası yoktur. Adım bazında tam veri etki sayıları ve uyarılar ayrıca gösterilir. Örnek boyutu sınırı, tam veri hesaplama ve dosya hash kontrolünün disk/CPU maliyetini sınırlamaz; büyük veri kaynak yönetimi C04 kapsamındadır.

## Çalışma alanı yedekleme ve geri yükleme

`ui/workspace_backups.py`, proje olmasa da yan panelde görünür. Yedek mevcut çalışma alanının tamamını, arşivlenmiş projeleri ve kayıtlı artifact'leri içerir; staging/log dosyalarını kopyalamaz. Disk hedefi kullanıcı tarafından görülebilir/değiştirilebilir. Oluşturulan dosya 256 MiB sınırına kadar tarayıcıdan indirilebilir; daha büyük dosya disk yolunda kalır. Aktif pending/running çalışma varken export reddedilir; UI ayrıca geri yükleme ve alan geçişini de engeller.

Geri yükleme dosya → inceleme → kaynak güveni onayı → boş hedef akışındadır. İnceleme bağlamı kaynak byte'larının SHA-256'sı, hedef metni ve mevcut workspace köküyle bağlıdır; dosya veya hedef değişince eski inceleme/onay uygulanamaz. Hedef mevcut çalışma alanı veya onun alt klasörü olamaz. Servis dolu hedefi ve yayım sırasında araya giren dosyaları korur. İnceleme, bütünlük kontrolü ve bilinen migration'lar özel geçici alanda tamamlanmadan hedef yayımlanmaz. Hata halinde yalnızca oluşturulan geçici geri yükleme klasörü temizlenir.

`WorkspaceSnapshotService.import_snapshot(..., trusted=True)` artık açık güven kararı ister; varsayılan False'dur. SHA-256 kaynak kimliğini/imzasını doğrulamaz. Yedekteki joblib modelleri daha sonraki model yüklemesinde kod çalıştırabileceği için UI bu sınırı açıklar. İnceleme ve geri yükleme model deserialize etmez. Doğrulama SQLite'ı önce salt okunur ve `trusted_schema=OFF` ile açar; migration checksum'larını, bilinen tablo/index SQL şemasını, integrity/foreign-key durumunu ve paketçe tanınan domain payload'larını kontrol eder. Fazladan trigger/view/şema, bilinmeyen/future migration, farklı uygulama ailesi ve katalog/manifest artifact farkları reddedilir. Bilinen eski migration prefix'i mevcut sürüme hedef yayımlanmadan yükseltilebilir. Başka kütüphane sürümlerinde model deserialize uyumluluğu incelemede garanti edilmez.

Arşiv en fazla 2 GiB, açılmış içerik en fazla 8 GiB, katalog en fazla 256 MiB, manifest en fazla 8 MiB ve giriş sayısı en fazla 100.000 olabilir. Sınırlar export ve import'ta uygulanır. Kanonik göreli POSIX yolları dışında traversal, Windows alias/ADS/reserved adlar, case çakışmaları, duplicate girişler, symlink, şifreleme ve desteklenmeyen sıkıştırma reddedilir. Declared boyutlar ZIP başlıklarıyla karşılaştırılır; tüm katalog/artifact byte'ları hash'lenir ve katalogdaki artifact kümesiyle birebir eşleştirilir. Bunlar güvenlik/işlem sınırlarıdır; büyük veri performans taahhüdü değildir.

Geri yükleme mevcut workspace'i değiştirmez. Kullanıcı açıkça Geri yüklenen çalışma alanını aç düğmesine bastığında yalnızca o Streamlit oturumunun `active_workspace` yolu değiştirilir; süreç genelindeki `AUTOANALYST_WORKSPACE` değişmez. Eski proje/veri/önizleme/rapor widget state'i temizlenir, özgün alana dönüş düğmesi sunulur. Tarayıcı/uygulama yeniden açıldığında geri yüklenen klasör, paneldeki mevcut çalışma alanı yoluyla tekrar açılabilir. Yedekleme dosyası var olan dosyanın üzerine yazılmadan yayımlanır; Windows'ta rename, POSIX'te exclusive hard-link yayımı kullanılır.

```python
service = WorkspaceSnapshotService(catalog, artifact_store)
service.export_snapshot(backup_path)
inspection = WorkspaceSnapshotService.inspect_snapshot(backup_path)
# İçeriği, hedefi ve kaynağa güveni değerlendirdikten sonra:
WorkspaceSnapshotService.import_snapshot(backup_path, empty_target, trusted=True)
```

Paket öz testi artık gerçek worker/rapor/yeniden açılışa ek olarak yedek oluşturur, inceler, ayrı geçici alana geri yükler ve rapor/veri sürümünü doğrular. Snapshot işlemleri yerel, senkron işlemlerdir; kaynak kontrollü uzun iş taşıma/ilerleme C04 kapsamındadır.

## Depolama ve geri alınabilir bakım

Yan paneldeki Depolama ve bakım alanı kullanıcı isteğiyle dosya envanteri çıkarır. Katalog, kayıtlı artifact, kayıtsız üretilmiş dosya, staging, günlük, karantina ve diğer dosyaların mantıksal byte toplamları ayrı gösterilir. Symlink/junction izlenmez. Tarama otomatik değildir; uygun eski adayların SHA-256 kontrolü disk okuması gerektirir.

`StorageMaintenanceService` bütün artifact kayıtlarını korur: arşivli projeler, eski sürümler, süresi dolmuş hazırlama önizlemeleri, raporlar, modeller ve holdout ilişkileri silinmez. Yalnızca uygulamanın UUID nesne/staging adlandırmasına uyan, katalogda referansı bulunmayan ve en az 24 saattir değişmeyen dosyalar adaydır. Bilinmeyen dosyalar, günlükler ve worker/protokol dosyaları otomatik temizlenmez.

İşlem inceleme → dosya seçimi → karantina → isteğe bağlı geri getirme akışındadır. Her seferinde en fazla 1.000 dosya taşınır. Karantina `staging/maintenance-trash/<işlem UUID>` altında kalır; disk alanı boşaltmaz ve kalıcı silme içermez. Tamamlanmış boş işlem manifestleri denetim izi olarak tutulur. Mevcut hedef dosya asla ezilmez. Manifest taşımadan önce fsync edilir; kısmi taşıma veya geri yükleme yeniden geri alınabilir. Geri getirme hash/boyut/mtime/inode kontrolü yapar.

Taşıma/geri getirme sırasında işletim sistemi dosya kilidi (`.activity.guard`) ve SQLite BEGIN IMMEDIATE birlikte tutulur. CSV/XLSX import, hazırlama önizlemesi ve rapor yayımı tüm üretim/katalog kayıt süresince PID/create_time işaretçisi taşır. Aktif/kararsız işaretçi, pending/running run veya mevcut worker lease bakım işlemini engeller. Böylece henüz kataloğa yazılmamış aktif çıktı da korunur. İşlem öncesi referanslar, dosya yaşı, kök, hash, boyut, mtime, inode ve bağlantısız sınırlandırılmış yollar yeniden doğrulanır. Süreç ölünce OS kilidi açılır; PID tekrar kullanımı create_time ile ayrılır; bozuk işaretçi güvenli kabul edilmez.

Bakım kullanıcı verisi üzerinde geliştirme sırasında çalıştırılmaz. Paket öz testi ayrı geçici restore alanında eski bir staging dosyası oluşturur; karantina/geri alma sonrasında byte'larını doğrular. Kalıcı disk alanı geri kazanımı ve kayıtlı artifact/metadata budaması bu paketin kapsamına alınmadı.


## C03 — Veri dışa aktarma ve kayıtlı sonuç özeti

`DataExportService.export` proje/veri seti/sürüm sahipliğini ve kaynak artifact SHA-256 bütünlüğünü doğrular. Seçili immutable sürümün bütün kullanıcı sütunlarını display_name ile ve INTERNAL_ROW_ORDER sırasıyla verir; iç satır kimliği/sırası dışarı aktarılmaz. Ekrandaki sayfa/sütun projeksiyonu çıktıyı etkilemez. Sıfır satırlı hazırlanmış sürümler başlık/şemalarıyla aktarılabilir.

CSV UTF-8 BOM ve virgül ayırıcı kullanır; metinler Arrow CSV yazıcısıyla kaçışlanır, özgün değerler değiştirilmez. CSV tür metadata'sı taşımaz; yeniden import tür tahmini yapar, eksik/boş metin ayrımı ve tür hassasiyeti için Parquet gerekir. Parquet alan türleri kaynak Arrow şemasından alınır; DuckDB okuyucu batch'leri bu şemaya güvenli cast edilip Snappy sıkıştırmayla yazılır. Genel Parquet import UI eklenmemiştir; dosya Arrow/Parquet okuyucusuyla açılır. Proje geçmişi/model/sonuç taşımak için C01 workspace yedeği kullanılmalıdır.

`TableAccess.export_batches` DuckDB bağlantısını okuyucunun ömrü boyunca açık tutar ve 65.536 satırlık Arrow batch'leri verir. Bu, Python tarafında tam tablo materializasyonunu önler; sıralamanın DuckDB bellek/disk maliyeti ve toplam RSS için bir üst sınır garantisi değildir. Kaynak ölçümleri/iptal C04 kapsamındadır.

Hedef var olan klasörde, workspace dışında ve biçime uygun uzantıda olmalıdır; Windows device/ADS adları reddedilir. Aynı hedef klasörde özel rastgele geçici dosya oluşturulur, writer kapatılıp fsync edilir; satır sayısı ve SHA-256 hesaplanır. Windows exclusive rename, POSIX exclusive hard-link yayımı kullanır. Araya giren hedef dahil hiçbir mevcut dosya ezilmez. Hata temizliği yalnızca bu çağrının sahip olduğu geçici dosyayı kapsar. Çıktı external veri dosyasıdır; katalog/head/lineage değiştirilmez ve workspace yedeğine eklenmez.

UI indirme fişi workspace/proje/veri seti/sürüm/biçim/hedef bağlamına bağlıdır; seçim değişince eski indirme kaldırılır. İndirmeden önce boyut ve SHA-256 tekrar kontrol edilir. En fazla 256 MiB indirilir; daha büyük çıktı disk yolunda kalır. Dil değişimi veri dosyasını değiştirmez. Yeniden aynı dolu hedefe export mevcut dosyayı korur ve yeni dosya adı ister.

`reporting.summary.result_summary` yalnızca immutable AnalysisResult kullanır; güncel veri tablosuna erişmez. Sonuç/run/input version, kayıtlı sayısal sample_summary, en fazla dört boyutsuz sonlu ölçüt (birim ve n varsa), ilk üç warning/error ve kalan uyarı sayısı sunulur. Boyutlu/split/model ölçütleri ve tanımsız değerler ayrıntıda kalır. Özet sonuç durumunu aynen aktarır; başarı/anlamlılık veya yeni istatistik çıkarımı üretmez. Aynı TR/EN içerik UI, HTML, Markdown, JSON summary ve CSV-ZIP summary.txt içinde bulunur. HTML/Markdown özet metinleri kaçışlanır; sayısal sonuç payload'ı değişmez.

Rapor şablonu presentation-4 oldu; eski raporlar ezilmez, yeni sunum ayrı artifact oluşturur. Paket öz testi ayrı geçici alanda CSV ve Parquet üretir, Parquet'i tekrar açar, fiş checksum'unu ve raporun kapsam notunu doğrular.


## C04 — Kaynak kontrollü veri işlemleri ve ölçüm

`execution/data_tasks.py` yalnızca csv, xlsx ve preparation işlemlerini kabul eder. Çalışma alanındaki ortak heavy-worker lease analiz ve veri worker'larının aynı anda başlamasını engeller. UUID ile sınırlı staging/data-tasks klasöründe istek, süreç kimliği, metrik, iptal ve sonuç dosyaları bulunur; rastgele komut, SQL veya pickle çalıştırılmaz. PID/create_time ve alt süreç ebeveyn ilişkisi doğrulanır; Windows Python redirector/PyInstaller bootloader PID'si gerçek worker PID'sinden ayrılır. İşlem tarayıcı kapanınca devam eder; son görev işaretçisi ve lease üzerinden yeni oturum izlemeye bağlanır.

UI'da CSV >=4 MiB, XLSX >=1 MiB veya hazırlamada (append girdileri dahil) >=100.000 satır / >=1.000.000 hücre ayrı sürece zorlanır. Daha küçük işlemlerde kullanıcı ayrı süreç seçebilir; eski küçük senkron yol shape/disk kontrolleriyle korunur, sert worker watchdog'u içermez. Varsayılan DataBudget: 1.536 MiB RSS, 120 sn worker çalışma süresi, 1.024 MiB net yeni workspace dosyası ve 128 MiB boş disk rezervi. Ön disk tahmini de uygulanır. Worker 0,1 sn hedef aralıkla RSS/zaman/net dosya artışı/boş disk kontrolü yapar; fs tarama maliyeti ve örnekleme kısa taşmalara izin verir. OS düzeyinde tahsis garantisi değildir. Süre, lease doğrulanıp worker izleyicisi başladıktan itibaren ölçülür; bootloader/modül başlangıcının önceki kısmı dahil değildir.

Bütçe/iptal worker'ı sonlandırabilir; katalog WAL transaction'ı tamamlanmadıysa rollback olur. Import ID ve preparation preview ID görev UUID'sidir. Yeniden bağlanmada yalnızca outcome dosyasına güvenilmez: katalogda tamamlanmış sürüm/önizleme varsa işlem başarıyla tamamlanmış kabul edilir. Commit, iptal/sonuç manifesti yarışını kazanır. Başarılı import head'i değiştirir; hazırlama önizlemesi kullanıcı Apply demeden head'i değiştirmez. UI form bağlamı değişmişse biten preview otomatik bağlanmaz. Hata/iptalde daha önce kayıtlı veri ve raporlar korunur.

Aktif veri görevi yedekleme/çalışma alanı geçişi, proje arşivi, bakım ve çakışan artifact üretimini engeller. Producer kayıt kontrolü C02 OS gate'i içinde yapılır. Task tamamlandıktan ve gerçek süreç durduktan sonra yalnızca task'ın source.bin kopyası ve kaydedilmiş özel spill klasörü temizlenir. Recursive cleanup öncesinde son mutlak yol, staging parent'ı, UUID'li ad öneki ve link/junction durumu doğrulanır. Zorla kesilmiş import'un katalogda kaydı olmayan object/.stage artıkları C02 eski kayıtsız dosya incelemesine kalabilir. Kullanıcının kaynak dosyasına dokunulmaz. Protokol/metrik geçmişi staging'de kalır; workspace yedeği terminal görev protokolünü taşımaz, kayıtlı veri/preview artifact'lerini taşır.

CSV path kaynaklarında boyut tam okumadan önce stat ile, ardından limit+1 bounded read ile tekrar denetlenir. Byte kaynakları zaten caller belleğindedir. CSV'de satır/sütun/hücre preflight'ı pandas'ın tam okumasından önce yapılır; parse sonrası shape tekrar kontrol edilir. XLSX toplam açılmış içerik 256 MiB, tek giriş 128 MiB ve giriş sayısı 10.000 ile sınırlıdır; worksheet dimensions tam pd.read_excel öncesi kontrol edilir. Import/preparation üst politikası 1.000.000 satır, 500 kullanıcı sütunu ve 5.000.000 hücredir; her boyut aynı anda serbest değildir ve bütçe daha önce durdurabilir.

İçerik imzası aynı kanonik JSON byte sırasını satır satır SHA-256'ya verir; ikinci tam per-cell sözlük ağacı/JSON string'i kaldırıldı. Eski parmak iziyle byte karşılaştırması test edildi; format/sürüm hash değişmedi. Hazırlama motoru bir sonraki temp tablo üretildikten sonra önceki tabloyu bırakır. Kendi bağlantısında 512 MiB DuckDB pool, tek thread ve 256 MiB özel spill uygular. TableAccess sabit sorguları da 512 MiB pool/tek thread kullanır; harici spill kapalıdır (max_temp_directory_size=0B). DuckDB pool, Python/Arrow dahil tüm süreç RSS'sinin üst sınırı değildir; izole worker'ın RSS denetimi ayrıdır.

Windows'ta telemetry okuma sırasında rename sharing violation için kısa sınırlı retry var; tek bir metrik yayımının başarısız olması işin öldürülmesine yol açmaz. Kaynak denetimi devam eder. Son örnek tamamlanma sırasında tekrar alınır. Metriklerin RSS değeri tüm worker'ın örneklenen tepesidir; net byte artışı toplam disk yazması/allocate blok sayısı değildir ve geçici input kopyası temizlenmeden önceki son ölçümü içerir.

Tekrarlanabilir sentetik ölçüm:

```powershell
.venv\Scripts\python.exe tools/benchmark_data.py --output docs/benchmarks/c04-local.json
```

Araç yalnızca kendisinin oluşturduğu özel geçici workspace'leri kullanır; kullanıcı verisi açılmaz. 25.000x8, 100.000x8, 20.000x80 ve sınırdaki 1.000.000x5 tabloları izole import ve iki adımlı filtre/rename önizlemesiyle ölçer. Ayrı süreçlerde 50.000x8 eski/yeni fingerprint karşılaştırması yapar; digest eşitliğini zorunlu tutar. Donanım/kütüphane sürümleri, bütçe ve ham ölçümler JSON'dadır. Bunlar yerel sentetik ölçümlerdir; bütün türler, bütün limit kombinasyonları veya farklı makineler için SLA değildir. Paket öz testi izole import/preview ve gerçek iptal/head korumasını da doğrular.


## C05 / D01–D05 / E01–E04

- `application/examples.py` creates deterministic independent quality/binary/regression/multiclass projects. `ui/help_panel.py` provides offline TR/EN guidance. A real-user pilot is planned in PILOT_GOREVLERI.md; automated tests do not count as participant sessions.
- `analyses/binary/audit.py` runs fresh pipelines within training-only stratified/group/time folds. Timestamp ties remain together. Permutation importance uses validation only and is limited to 100 features/5 repeats. Original model artifacts, selection and final holdout remain unchanged.
- Calibration is a validation diagnostic with sigmoid CalibratedClassifierCV and original split-aware folds; Brier/log-loss and reliability bins are saved. It produces no deployment artifact or altered selection. Drift uses training-only reference quantiles/KS or categorical total variation; PSI/distance/novel/missing thresholds are heuristic and persisted.
- `analyses/intervals.py` supports Student-t mean and Clopper-Pearson exact proportion intervals after explicit independent-row acknowledgement. Welch supports confidence .80–.99; Pearson adds Fisher-z CI for acknowledged independent samples with n>3. Spearman has no interval. Multiplicity is unadjusted for each requested comparison; no causal claim is made.
- `analyses/supervised.py` adds separate regression/multiclass contracts. Shared final holdout locking/recovery accepts all three supervised module IDs, validates project/version/task before opening holdout, and retains legacy binary selection hashes. Regression: DummyMedian/Ridge/RandomForest. Multiclass: Dummy/LR/RandomForest, 3–20 typed labels, macro/weighted F1, per-class metrics and validation confusion matrix. Selection is validation-only; no CV/calibration feature is claimed for these two modules.
- `preparation/joins.py` checks exact physical key types and declared one-to-one/many-to-one/one-to-many/many-to-many cardinality before materialization. Inner/left joins only; null keys do not match. Output cap ≤1M rows, expansion ≤10 and shared 5M cells apply. Right user column identities are retained for leakage lineage; colliding identities/self/history joins are rejected. Migration 0006 extends input lineage roles with join. Learned dataset-level preparation anywhere in joined ancestry conservatively blocks modeling.
- `application/commands.py` is a V2 closed-operation adapter: filter/rename/drop/drop-missing/deduplicate/fill/replace, quoted names and typed values. Unknown/ambiguous input yields clarification, never eval/SQL. Proposal identity binds workspace/project/dataset/base version/head revision. Preview/apply revalidate captured identity and recipe hash, including ABA head changes.
- `application/planner.py` is an optional Ollama-compatible HTTP JSON provider. HTTPS required except HTTP loopback; redirects rejected. Per-request consent precedes all network calls. Only command/column metadata/closed operation schemas leave the machine. One request, 32–2048 requested output tokens, ≤30s timeout, 32KiB request and 64KiB response; local closed validation and rule fallback apply. Provider token enforcement is not a monetary billing guarantee. No real model/server connection was exercised; tests use bounded fake HTTP responses and adversarial plans in test_command_planner.py.
- `reporting/binary_formats.py` uses Matplotlib PDF backend and openpyxl, with no new runtime dependency. Charts consume the same validated saved bins as HTML, not source datasets. PDF pages have bounded monospaced wrapping, Turkish font support, page numbers and explicit record/cell truncation. XLSX stores every saved result table, source-table references and embedded PNG charts; formula-like text is forced to string cells. Excel limits fail explicitly. XLSX export additionally caps aggregate saved table/metric cells at 500,000 and charts at 50 before Workbook allocation; use CSV/JSON for larger results. PDF checks a 1M-character section budget before wrapping. Binary containers can include timestamps; persisted report caching returns identical saved bytes after reopening. Template presentation-5 invalidates only new rendering cache keys.

Primary API references: [scikit-learn calibration](https://scikit-learn.org/stable/modules/calibration.html), [Ollama chat](https://docs.ollama.com/api/chat). These document provider/library behavior; they are not evidence of a live external integration test.


## Windows başlangıcı: üretim frontend'i

V2 doğrudan `bootstrap.run` çağırır. Bu API flag_options değerlerini ilk açılışta kendiliğinden yüklemez; `bootstrap.load_config_options` önce çağrılmalıdır. PyInstaller yolları site-packages içermediğinden Streamlit varsayılanı developmentMode=true olabilir; 3000 frontend'i bekler ve Python sunucusu HTML sunmaz. Launcher üretim modunu, loopback adresini, browser/server portunu ve boş base path'i tek konfigürasyonda yükler. İzleyici kapalıdır; browser URL'si Streamlit'in kendi resolver'ından alınır.

`tools/check_startup.py` varsayılan olarak normal source launcher'ını, `--executable` verilirse gerçek EXE'yi başlatır. CWD ve AUTOANALYST_WORKSPACE ayrı geçici dizindir. Yalnızca kendi sunucusunu durdurur; varsayılan tarayıcı süreçlerini sonlandırmaz. HTTP health yanında root HTML, JS/CSS MIME/bütünlüğü, WebSocket origin/portu, uygulama başlığı, script bitişi ve sentetik örnek veri ekranı denetlenir. Uzak HTTP istekleri test tarayıcısında engellenir. Hostile config ve dolu port iki gerçek tarayıcı regresyon senaryosudur.

Windows psutil bazı uvicorn TCP endpoint'lerini LISTEN yerine NONE olarak raporlayabilir. Doğrulayıcı sahipliği PID ailesi ve yerel adres/port üzerinden kontrol edip gerçek HTTP erişimini ayrıca doğrular; kullanıcı workspace'ine yazmadan önce özel test kataloğunun oluşmasını ister.

```powershell
uv run --frozen playwright install chromium
uv run --frozen python tools/check_startup.py --executable dist/AutoAnalyst.exe --output build/startup-check.json --screenshot build/startup-ui.png
```

CI Linux'ta browser sistem bağımlılıklarını da `playwright install --with-deps chromium` ile kurar. Bu araç uygulamanın runtime bağımlılığı değildir. Gerçek kurulu EXE ve masaüstü kısayolu testlerin kapsamı dışındadır; değiştirilmez.
