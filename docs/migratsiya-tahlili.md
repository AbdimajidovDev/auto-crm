# Eski CRM ma'lumotlarini ko'chirish — tahlil va reja

> Maqsad: mijozning eski CRM tizimidagi tarix (mahsulot, kirim, sotuv, transfer, qoldiq, spisaniye) Excel fayllarini bizning bazaga (Django) ko'chirish mumkinmi — va qanday qilib. Bu hujjat faqat **tahlil**, kod yo'q.

## 1. Qisqa xulosa

**Ha, ko'chirish mumkin** — lekin "to'g'ridan-to'g'ri ko'chirib qo'yish" emas. Excel ustunlari bilan bizning jadval ustunlari to'liq mos kelmaydi, shuning uchun **mapping (moslashtirish) + transformatsiya** kerak. Asosiy qiyinchiliklar quyida (6-bo'lim) — ularning hammasi yechiladi, faqat ba'zilarida ma'lumot to'liq emasligini mijozga aytib qo'yish kerak.

Ishonchli ko'chiriladigan qism: **mahsulot katalogi + joriy qoldiq (ostatka) + kirim/transfer tarixi**.
Cheklov bilan ko'chiriladigan qism: **sotuv tarixi** (kunlik yig'ma, chek darajasida emas) va **spisaniye** (bazada mos model yo'q).

---

## 2. Excel fayllar — nima saqlangan

`docs/` ichida 7 ta fayl bor. Har birini 1–300 qator bo'yicha tekshirdim, ustun sarlavhalari to'liq:

| Fayl | Nima | Qator | Asosiy ustunlar |
|------|------|-------|-----------------|
| `Отчет_по_остаткам_...` | **Joriy qoldiq** (har do'kon × mahsulot) | 12 548 | Магазин, Наименование, Артикул, Баркод, Ед.изм, Категория, Бренд, Поставщик, Цена продажи/поставки, Кол-во, last_import |
| `Отчет_по_импортам_...` | **Kirim** (qator = kirim qatori) | 48 802 | Дата импорта, ID заказа, Магазин, mahsulot, Кол-во импортированных, Сумма по цене поставки/продажи + 20+ ta hisobot ustuni |
| `Отчет_по_продажам_...` | **Sotuv** (kunlik yig'ma) | 65 723 | Магазин, Дата, mahsulot, Кол-во проданных, Кол-во возвращенных, Продажи без скидки, Ед.изм |
| `Отчет_по_трансферам_...` | **Transfer** (qator = transfer qatori) | 38 447 | ID, Отправлено/Принято (vaqt), mahsulot, Откуда, Куда, Кол-во отправленных/принятых |
| `Отчет_по_списаниям_...` | **Spisaniye** (hisobdan chiqarish) | 6 411 | ID списания, Причина, Магазин, mahsulot, Кол-во списанных, Цена поставки/продажи |
| `Отчет_по_возвращенным_заказам_...` | **Sotuvdan qaytarish** | 96 | ID операции возврата, ID операции заказа, mahsulot, Кол-во, Сумма возврата |
| `Отчет_продажи_по_поставщикам_...` | Sotuv (postavshik kesimida) | 65 104 | ≈ sotuv fayli + supplier. **Dublikat**, alohida kerak emas |

**Do'konlar (5 ta):** `Avtoyon`, `112 do'kon`, `115 do'kon`, `96-do'kon`, `Bozordagi sklad`.
`Bozordagi sklad` — bu ombor, ya'ni `Store.type = BASE`; qolganlari `STORE`.

---

## 3. Bizning baza — mos modellar

| Tushuncha | Model (`db_table`) | Joylashuv |
|-----------|--------------------|-----------|
| Mahsulot | `Product` (`product`) | apps/products |
| Kategoriya / Brend | `Category`, `Brand` | apps/products |
| O'lchov birligi | `ProductUnitMeasurement` | apps/products |
| **Joriy qoldiq (partiya)** | `ProductBatch` (`product_batch`) | apps/products |
| Do'kon / Ombor | `Store` (`store`) | apps/store |
| Postavshik | `Supplier` (`supplier`) | apps/contract |
| **Kirim** | `StockEntry` + `StockEntryItem` | apps/contract |
| Postavshik qarzi | `SupplierTransaction` | apps/contract |
| **Sotuv** | `Sale` + `SaleItem` | apps/sales |
| Sotuvdan qaytarish | `SaleReturn` + `SaleReturnItem` | apps/sales |
| **Transfer** | `StockTransfer` + `StockTransferItem` | apps/transfer |
| Spisaniye | ❌ **mos model yo'q** | — |

---

## 4. Mapping (Excel ustuni → baza maydoni)

### 4.1 Mahsulot (manba: `остатки` fayli — eng to'liq katalog)
| Excel | → Model.maydon |
|-------|----------------|
| Наименование товара | `Product.name` |
| Артикул | `Product.sku` |
| Баркод | `Product.barcode` |
| Категория | `Category.name` (get_or_create → `Product.category`) |
| Бренд | `Brand.name` (get_or_create → `Product.brand`) |
| Ед. измерения (dona) | `ProductUnitMeasurement` (get_or_create → `Product.unit_measurement`) |
| Архивирован | `Product.status` (a/i) |

### 4.2 Joriy qoldiq (manba: `остатки` → `ProductBatch`)
| Магазин → `store` · Кол-во → `quantity` · Цена поставки → `purchase_price` · Цена продажи → `selling_price` |
> Har (do'kon, mahsulot) juftligi uchun bitta `ProductBatch`. Bu — ko'chirishning **eng muhim** qismi: joriy holatni to'g'ri tiklaydi.

### 4.3 Kirim (manba: `импорты` → `StockEntry` / `StockEntryItem`)
`ID заказа` bo'yicha **guruhlash** → bitta `StockEntry`, qatorlar → `StockEntryItem`.
Дата импорта → `created_at` · Магазин → `store` · Поставщик → `supplier` · Кол-во → `quantity` · Сумма по цене поставки/продажи → `purchase_price`/`selling_price`.

### 4.4 Transfer (manba: `трансферы` → `StockTransfer` / `StockTransferItem`)
`ID` bo'yicha **guruhlash** → bitta `StockTransfer`. Откуда → `from_store` · Куда → `to_store` · Отправлено/Принято → `created_at`/`approved_at` · status → `APPROVED`.

### 4.5 Sotuv (manba: `продажи` → `Sale` / `SaleItem`)
Fayl **kunlik yig'ma**, shuning uchun `(Магазин, Дата)` bo'yicha guruhlab har biriga bitta sintetik `Sale` yasash mumkin; qatorlar → `SaleItem` (qty, unit_price = Продажи/qty). Tafsilot pastda (6.4).

---

## 5. Mahsulotni bazaga kirita olamizmi? — Ha

Mahsulotlar `Артикул` (sku) va `Баркод` (barcode) orqali aniqlanadi. Ikkalasi ham bizning `Product` da `unique` maydon bor, demak ko'chirish texnik jihatdan mumkin. E'tibor beriladigan nuqtalar:

- **Barcode uzunligi:** ma'lumotdagi barcode `2000000003399` = 13 raqam, `Product.barcode max_length=13` — mos. Bo'sh yoki dublikat barcode'lar uchun tekshiruv kerak.
- **SKU formati farqi:** eski sku'lar `MKW-99767`, `A07832` ko'rinishida. Bizda `Product.save()` sku'ni `KATEGORIYA-000123` formatida **avtomatik** yaratadi. Eski sku'ni saqlab qolish uchun (4-bo'lim) `save()` dagi avtogeneratsiyani **chetlab o'tish** kerak (pastga qarang).

---

## 6. Asosiy muammolar va yechimlari

### 6.1 ⚠️ `save()` avtomatik sku/barcode/shtrix-code yaratadi
`Product.save()` yangi obyektga sku, barcode va shtrix-code rasm avtomatik beradi — bu eski qiymatlarni **bosib ketadi**.
**Yechim:** `bulk_create()` ishlatish (u `save()` ni chaqirmaydi) yoki sku/barcode qiymatlarini oldindan to'ldirib kirgizish. Shu yo'l bilan eski Артикул/Баркод saqlanadi.

### 6.2 ⚠️ Tarixiy sanalar (`created_at = auto_now_add`)
`Sale`, `StockEntry`, `StockTransfer` va boshqalarda `created_at` — `auto_now_add=True`, ya'ni yozuv yaratilganda **bugungi sana** qo'yiladi, Excel'dagi eski sana emas.
**Yechim:** ikki variant — (a) yozgandan keyin `UPDATE ... SET created_at = <excel sana>` bilan to'g'rilash, yoki (b) ko'chirish vaqtida maydonning `auto_now_add` ni vaqtincha o'chirib `bulk_create` qilish. Tarix sanasi muhim bo'lgani uchun bu **majburiy**.

### 6.3 ⚠️ Ustunlar mos kelmasligi (column mismatch) — bu hal qilinadigan muammo
Mijoz aytgan asosiy muammo shu. Yechim — **to'g'ridan-to'g'ri nusxalash emas, mapping qatlami** (4-bo'lim jadvallari):
- matnli qiymatlar (Категория, Бренд, Поставщик, Ед.изм) → `get_or_create` bilan FK ga aylantiriladi;
- do'kon nomi → `Store` ga nom bo'yicha map qilinadi (oldin 5 ta Store yaratiladi);
- Excel'da bizda yo'q ustunlar (Маржа%, hisobot summalari) → **e'tiborsiz qoldiriladi**;
- bizda bor lekin Excel'da yo'q maydonlar (`seller`, `customer`, `wholesale_price`) → default/sintetik qiymat (pastga qarang).

### 6.4 ⚠️ Sotuv darajasi: chek emas, kunlik yig'ma
`продажи` faylida **chek/operatsiya ID yo'q** — har qator bu `do'kon + sana + mahsulot` kesimidagi kunlik jami. Demak alohida cheklarni tiklab bo'lmaydi.
**Yechim:** `(do'kon, sana)` bo'yicha bitta sintetik `Sale` yasab, o'sha kungi mahsulotlarni `SaleItem` qilib biriktirish. Bundan tashqari:
- `Sale.seller` — FK, `null` bo'lolmaydi → ko'chirish uchun maxsus **"migration/system" user** yaratib o'shanga bog'lash;
- `Sale.customer` — `null=True`, bo'sh qoldiriladi;
- `SaleItem.purchase_price` — sotuv faylida yo'q; kerak bo'lsa `остатки`/`импорты` dan sku bo'yicha olinadi yoki `null`.

### 6.5 ⚠️ Spisaniye uchun bazada model yo'q
`списания` faylida 6411 qator bor, lekin bizda hisobdan chiqarish (write-off) modeli yo'q. Eng yaqini `InventoryAdjustment` (faqat `difference`), lekin semantikasi (sessiya talab qiladi) mos emas.
**Yechim (mijoz bilan kelishiladi):** (a) yangi `WriteOff` modeli qo'shish (eng to'g'risi), yoki (b) `InventoryAdjustment` ga moslab yozish, yoki (c) bu tarixni umuman ko'chirmaslik. **Tavsiya: (a)** — agar mijozga spisaniye tarixi kerak bo'lsa.

### 6.6 ⚠️ Eski ID lar va o'zaro bog'lanishlar
Fayllarda eski ID lar bor (`ID заказа`, `ID списания`, transfer `ID`, qaytarish `ID операции заказа`). Bizning modellar avto-PK ishlatadi.
- Kirim/transfer ichidagi guruhlash uchun bu ID lar **kerak** (qatorlarni bitta hujjatga yig'ish) — buni mapping vaqtida ishlatamiz.
- `возвраты` fayli `ID операции заказа` orqali sotuvga bog'lanadi, lekin sotuv faylida bunday ID **yo'q** → qaytarishlarni aniq sotuvga bog'lab bo'lmaydi (atigi 96 qator, ahamiyatsiz). Tavsiya: qaytarishlarni alohida ko'chirmaslik yoki sotuv `returned_quantity` siga umumiy qo'shish.

---

## 7. Tavsiya etilgan ko'chirish tartibi

Bog'liqlik (FK) tartibida, har bosqich import skripti (Django management command) sifatida:

1. **Lug'atlar:** `Store` (5 ta, type to'g'ri), `Supplier`, `Category`, `Brand`, `ProductUnitMeasurement` — `get_or_create`.
2. **Mahsulotlar:** `остатки` dan unikal (Артикул/Баркод) bo'yicha `Product` (`bulk_create`, sku/barcode saqlanadi — 6.1).
3. **Joriy qoldiq:** `остатки` dan `ProductBatch` (do'kon × mahsulot).
4. **Kirim:** `импорты` ni `ID заказа` bo'yicha guruhlab `StockEntry`+`StockEntryItem` (+ xohlasa `SupplierTransaction`).
5. **Transfer:** `трансферы` ni `ID` bo'yicha guruhlab `StockTransfer`+`StockTransferItem`.
6. **Sotuv:** `продажи` ni `(do'kon, sana)` bo'yicha `Sale`+`SaleItem` (system-user bilan — 6.4).
7. **(Ixtiyoriy) Spisaniye / Qaytarish:** model qarori qabul qilingach (6.5).
8. Har bosqichdan keyin tarixiy `created_at` larni to'g'rilash (6.2).

**Texnik tavsiyalar:**
- SQLite (`db.sqlite3`) da emas, **PostgreSQL** da bajarish (hajm katta: ~225 ming qator).
- Har bosqichni `transaction.atomic` + `bulk_create(batch_size=...)` bilan.
- `остatки` ni "haqiqat manbai" (source of truth) deb olish: kirim − sotuv − transfer − spisaniye hisob-kitobi bilan emas, joriy qoldiqni to'g'ridan-to'g'ri `остатки` dan olish.
- Avval **test bazada** (yoki kichik nusxada) sinab ko'rish.

---

## 8. Yakuniy javob (mijozga)

- ✅ Mahsulot katalogi + joriy qoldiq — **to'liq ko'chiriladi**.
- ✅ Kirim va transfer tarixi — **ko'chiriladi** (sana to'g'rilash bilan).
- ⚠️ Sotuv tarixi — **kunlik yig'ma** darajada ko'chiriladi (alohida cheklar eski tizimda saqlanmagan).
- ⚠️ Spisaniye — yangi model qo'shilsa ko'chiriladi; aks holda ko'chirilmaydi.
- ⚠️ Qaytarishlar (96 ta) — sotuvga aniq bog'lab bo'lmaydi, ahamiyatsiz.

Ustunlar mos kelmasligi muammosi **mapping qatlami** bilan hal qilinadi — bu standart ETL ishi, to'siq emas.