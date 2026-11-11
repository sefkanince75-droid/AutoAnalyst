# V2 Windows başlangıç düzeltmesi

## Yeniden üretilen hata

1046d96 aday EXE'sinde geçici workspace ile sağlık endpoint'i 200/ok, ana sayfa 404/Not Found döndü. Log sunucunun 8501'de, duyurulan frontend URL'sinin 3000'de olduğunu gösterdi. Kurulu eski uygulama çalıştırılmadı veya değiştirilmedi; PyInstaller arşivi salt okunur incelendi.

Eski paket ayrı Streamlit child ve pywebview kullanır; CLI'ya `--global.developmentMode=false`, loopback adresi ve seçili portu açıkça verir. Kurulu eski paketin `_internal` klasöründe statik index.html vardır. Yeni V2 tek dosya paketi, bütün runtime'ı içine aldığı için daha büyüktür; boyut farkı port hatasının nedeni değildir.

## Kök neden

1. Streamlit geliştirme modu varsayılanını modül yolunda site-packages/dist-packages bulunup bulunmamasından çıkarır. PyInstaller `_MEI` yolu bu kontrolü geçmez ve geliştirme modu açılır.
2. Geliştirme modu harici Vite frontend'ini 3000'de bekler; Python sunucusunun sağlıklı olması production HTML'i sunduğu anlamına gelmez.
3. V2, Streamlit CLI'nın yaptığı ilk `bootstrap.load_config_options` adımını atlayıp doğrudan `bootstrap.run` çağırıyordu. Verilen flag_options ilk açılışta uygulanmıyordu. Bu nedenle yalnızca sözlüğe port veya developmentMode eklemek yeterli değildir.
4. Önceki backend öz testi gerçek HTTP sunucusu ve tarayıcı frontend'ini başlatmıyordu. Başarılı öz testten arayüzün açıldığı sonucu çıkarılması eksik doğrulamaydı.

## Düzeltme ve regresyon

Üretim modu server başlamadan yüklenir. Server/browser 127.0.0.1 üzerinde aynı seçili portu kullanır; URL Streamlit'in gerçek browser resolver'ından alınır. Varsayılan 8501 doluysa yeni boş loopback portu seçilir. Kayıtlı kullanıcı/proje config'i port, developmentMode veya base path üzerinden desktop frontend'ini bozamaz. `--port 0` ve `--no-browser` doğrulama/ikinci örnek açılışı için kullanılabilir.

Yedi yeni vaka: ilk config yükleme sırası ve ortak URL resolver'ı, üç geçersiz port, dolu port fallback'i, temiz ve çakışan config ile iki gerçek HTTP/tarayıcı yolculuğu. Son ikisi HTML/JS/CSS, WebSocket, başlık, script tamamlanması, dataframe ve özel workspace'te örnek proje açılmasını denetler. CI ayrıca gerçek EXE için aynı aracı çalıştırır; backend self-test tek kabul ölçütü değildir.

Doğrulama yardımcı aracının ilk Windows sahiplik kontrolünde psutil endpoint statüsünün NONE olması iki yalancı başarısızlığa yol açtı. Bu yardımcı kontrol düzeltildi; gerçek sunucu erişimi ayrıca doğrulanır. Kurulu uygulama, kullanıcı verisi ve kısayol hiçbir aşamada yazma hedefi değildir.

Playwright yalnızca dev grubuna eklendi; sayısal/runtime bağımlılık sürümleri değiştirilmedi. Genel release candidate ve dış kullanıcı kabul kapıları devam eder; bu düzeltme merge veya kararlı yayın yetkisi vermez.
