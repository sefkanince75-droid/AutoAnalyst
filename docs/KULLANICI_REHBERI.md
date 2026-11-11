# Kullanıcı rehberi

AutoAnalyst yerel tablo analizi uygulamasıdır. Veri dosyaları ve hesaplama bilgisayarda kalır; isteğe bağlı dış planlayıcı ayrıca izin ister.

1. Yan panelden örnek proje oluşturabilir veya kendi projenizi/veri setinizi açabilirsiniz. Örnekler sentetiktir.
2. CSV için ayırıcı ve kodlamayı, XLSX için sayfayı seçin. Önizleme ilk 100 satırdır; tam dosyanın türleri farklı olabilir.
3. Veri sekmesinde sayfaları ve sütunları seçin. Dışa aktarma tüm kullanıcı sütunlarını ve satırları içerir.
4. Hazırlama adımlarını sırayla düzenleyin. Önizleme kayıtlı veriyi değiştirmez; Uygula yeni sürüm oluşturur. Sürüm geçmişinden geri dönebilirsiniz.
5. Analiz sekmesinde hedef ve özellikleri seçin. Hedefin özelliklere dahil edilmemesine ve özelliklerin tahmin anında biliniyor olmasına dikkat edin. Grup/zaman bağımlılığı varsa uygun bölme politikasını seçin.
6. Modelleme ön işleme adımları eğitim/fold içinde öğrenilir. Veri genelinde öğrenilmiş doldurma gibi hazırlamalar modellemede veri sızıntısı nedeniyle engellenebilir. Son test kümesi seçim/tuning için kullanılmaz.
7. Sonuçlar kayıtlı sürüme bağlıdır. Yeni veri yüklense de eski sonuç/rapor yeniden hesaplanmaz. Özet ve ayrıntılardaki veri bölümü, örnek büyüklüğü ve yöntem sınırlarını okuyun.
8. Büyük import/preview yan panelden izlenebilir ve iptal edilebilir. İptalden önce tamamlanmış katalog kaydı korunur; sınırlar ve yerel ölçümler DEVELOPMENT.md içindedir.
9. Proje arşivi veri silmez. Karantina geri alınabilir ve disk alanı boşaltmaz. Workspace yedeği ayrı yeni/boş klasöre geri yüklenir. Yalnızca kaynağına güvendiğiniz model içeren yedekleri açın.

Sorun giderme: Yanlış CSV görünümü için ayırıcı/kodlamayı değiştirip örneği yenileyin. Dolu hedefte export yeni dosya adı ister. Eski önizleme için güncel sürümde tekrar önizleyin. Kaynak sınırında veriyi küçültün veya gereksiz sütunları azaltın. Hata kaydı teşhis kimliği içeriyorsa bunu ve uygulama sürümünü destek talebine ekleyin; ham veriyi paylaşmayın.


## Yeni analiz ve hazırlama seçenekleri

- Model denetimi: kayıtlı ikili eğitim çalışmasını seçin. Fold'lar yalnızca eğitim bölümünden üretilir; önem değerleri validation üzerinde hesaplanır. Önem nedensellik göstermez.
- Güven aralığı: ortalama veya oranı, güven düzeyini ve bağımsız gözlem onayını seçin. Korelasyon ekranındaki bağımsızlık onayı Pearson için %95 aralık ekler; Spearman için aralık yoktur. Tekrarlı ölçümler için bu onayı kullanmayın.
- Kalibrasyon raporu: validation üzerindeki karşılaştırmadır; kullanılan model kendiliğinden değişmez. Veri değişimi uyarısı etiketli yeni performans ölçümü veya otomatik yeniden eğitim değildir.
- Regresyonda sayısal hedef, çok sınıflı modelde 3–20 hedef sınıfı gerekir. Önce eğitim/validation; sonra açık son test; ardından yeni veri eşleme/tahmin adımlarını kullanın. Dummy sonucu aşılmadığında model önerilmemesi beklenen davranıştır.
- Join için sağ veri sürümünü, eşit türde anahtarları ve beklediğiniz kardinaliteyi seçin. Satır ve çoğalma sınırlarını belirleyin, eşleşmeyen satır sayılarını önizlemede kontrol edin. Null anahtarlar eşleşmez. Aynı sütun kimliklerinin çakıştığı kendi sürümleri arasında join desteklenmez.

## Komutla hazırlama

Veri sekmesindeki komut alanı örnekleri: `filter value >= 2`, `rename "eski ad" "yeni ad"`, `drop_missing value`, `fill value median`, `replace category A B`. Türkçe kısayollar: `filtre`, `yeniden_adlandir`, `eksikleri_sil`, `doldur`, `degistir`, `sutun_sil`, `tekrarlari_sil`. Alan ve değer adlarında boşluk varsa çift tırnak kullanın. Önce planı inceleyin, sonra önizleyin, en son Uygula'ya basın. Belirsiz/uygunsuz komutta veri değişmez. Geri dönüş sürüm geçmişindedir.

İsteğe bağlı planlayıcı varsayılan kapalıdır. Açarsanız kendi Ollama uyumlu uç noktanızı/modelinizi girin. Her istek için hangi adrese komut ve sütun metadata'sı gönderileceğini okuyup onaylayın; satırlar gönderilmez. Yanıt geçersizse yerel kurallar denenir. Yerel sunucu/model kurulu olması bu uygulamayla otomatik sağlanmaz.

## PDF ve XLSX

Sonuçlar → Dışa aktar alanından biçim ve dili seçin. CSV biçimi ZIP içinde tablo dosyalarıdır; XLSX gerçek Excel çalışma kitabıdır. PDF grafik ve okunabilir kayıt sayfaları içerir; tablo başına ilk 100 satır/hücre başına 500 karakter sınırı sayfada belirtilir. Tam kayıtlı sonuç tablolarını XLSX/CSV/JSON ile alın. Bunlar kaynak verinin tamamı anlamına gelmez; tüm kaynak veri için Veri → CSV/Parquet kullanın. Excel'in hücre/satır sınırı aşılırsa veri sessizce kesilmez, başka biçim seçmeniz gerekir.

Rapor kaynak bütçesi: XLSX toplam 500.000 kayıtlı tablo/ölçüt hücresi ve 50 grafik ile sınırlıdır. Büyük sonuçlarda CSV/JSON seçin. PDF 250 sayfa ve bölüm başına 1 milyon karakter bütçesi uygular.
