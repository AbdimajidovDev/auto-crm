# To'lov tizimi yangilanishi — Bank kartalari, aralash to'lov, `Sale.payment_type`

> **Sana:** 2026-07-11
> **Muallif:** Claude (AI agent)
> **Holat:** Bajarildi, migratsiyalar dev bazaga qo'llandi, 17 ta yangi test — hammasi o'tdi.

---

## 1. Vazifa nima edi

1. Kompaniya istalgancha **bank kartasi** yarata olsin (faqat ichki hisobot uchun — to'lov
   tizimlari bilan integratsiya YO'Q, shuning uchun faqat nom saqlanadi).
2. Mijoz sotuvda to'lovni istalgan nisbatda bo'lib to'lasin: bir qismi **naqd**, bir qismi
   do'konning **istalgan bitta bank kartasiga**.
3. Sotuvda **aralash (mixed)** to'lov turi paydo bo'lsin va hisobotlar
   **Naqd / Karta / Aralash / Qarz** hamda **har bir karta kesimida** to'g'ri chiqsin.
4. `payment_type` ni faqat backend hisoblasin, logika **bitta markaziy joyda** bo'lsin va
   barcha oqimlar (sotuv, qarz to'lash, qaytarish, data migration) o'shani ishlatsin.
5. Qaytarish (sale return) ham sotuv kabi erkin to'lov taqsimini qabul qilsin.
6. Migratsiyalar eski ma'lumotlarni buzmasin, N+1 bo'lmasin.

---

## 2. Tanlangan arxitektura va NIMA UCHUN

### 2.1. Uch qatlamli taqsimot (Clean Architecture / SRP)

| Qatlam | Fayl | Mas'uliyat |
|--------|------|-----------|
| **Domain qoidasi (pure)** | `apps/sales/payment_rules.py` | `compute_payment_type(cash_total, card_total)` — ORM/Djangoga bog'liq bo'lmagan sof funksiya. |
| **Model (holat + invariant)** | `apps/sales/models.py` | `Sale.recalculate_payment_type()` — o'z to'lovlaridan turini hisoblaydi; `Payment.clean()` — karta/naqd invarianti; `BankCard.save()` — yagona default kafolati. |
| **Service (oqim)** | `apps/sales/services/payment_service.py` | `SalePaymentService.record_payments()` — to'lovlarni yozishning yagona nuqtasi; `validate_payment_method()` — serializerlar uchun umumiy qoida. |

**Nega qoida alohida `payment_rules.py` da?** Data migration ichida Django'ning "historical
model"lari custom metodlarga ega bo'lmaydi — `Sale.recalculate_payment_type()` ni migratsiyadan
chaqirib bo'lmaydi. Qoidani sof funksiyaga ajratganimiz uchun **runtime ham, migratsiya ham
AYNAN BITTA implementatsiyani** import qiladi — `if/else` hech qayerda takrorlanmagan
(foydalanuvchi talabi №4).

**Nega `Sale.recalculate_payment_type()` model metodi (service emas)?** Bu hisob faqat sotuvning
O'Z to'lovlariga bog'liq sof domain logika — tashqi dependency yo'q. Model metodi qilinsa uni
service, admin, shell, boshqa app (debts) — hamma joydan circular-importsiz chaqirish mumkin.
`SalePaymentService` esa oqim ishi (bulk yozish, invariant tekshirish) uchun qoldi — SRP saqlanadi.

### 2.2. Aralash to'lov "Payment.Type" ga qo'shilmadi — ongli qaror

`Payment` qatori har doim BITTA usulni ifodalaydi (`cash` yoki `card`), chunki har bir qator
konkret pul harakati. **"Aralash" — bu sotuvning xususiyati**, alohida to'lov emas. Shuning uchun
`mixed` faqat `Sale.PaymentType` da bor: `cash | card | mixed | debt`. Bu xuddi loyihada allaqachon
ishlab turgan `StockEntry.PaymentType` (contract app) naqshi — kod bazasi bilan uslubiy yaxlit.

### 2.3. `Payment.is_refund` maydoni — nega kerak bo'ldi

Qaytarish (sale return) oqimi ilgari ham mijozga qaytarilgan pulni `Payment` qatori sifatida
yozardi, lekin uni kirim to'lovdan **ajratib bo'lmasdi** — bu karta kesimi hisobotini buzgan
bo'lardi (qaytarim "tushum" bo'lib ko'rinardi). Endi:
- kirim to'lov → `is_refund=false`;
- mijozga qaytarilgan pul → `is_refund=true`;
- `payment_type` va karta hisoboti **NET** (kirim − qaytarim) asosida hisoblanadi.

### 2.4. Hisoblash qoidasi (markaziy, foydalanuvchi bergan spetsifikatsiya)

```
cash_total = SUM(cash kirim) − SUM(cash qaytarim)
card_total = SUM(card kirim) − SUM(card qaytarim)

cash==0, card==0 → DEBT
cash>0,  card==0 → CASH
cash==0, card>0  → CARD
cash>0,  card>0  → MIXED
```

Bu qoidani ishlatuvchi oqimlar (hammasi `sale.recalculate_payment_type()` orqali):
1. Sotuv yaratish — `SaleService.create_sale` (save=False, yakuniy `sale.save()` bilan bitta UPDATE);
2. Qarz to'lash — `DebtService.pay_debt`;
3. Qaytarish — `SaleReturnService.create_return`;
4. Data migration — `0012` (pure funksiyani to'g'ridan-to'g'ri import qiladi).

---

## 3. O'zgargan/yangi fayllar (to'liq ro'yxat)

### YANGI fayllar

| Fayl | Nima |
|------|------|
| `apps/sales/payment_rules.py` | `compute_payment_type()` — markaziy pure qoida. |
| `apps/sales/services/payment_service.py` | `SalePaymentService.record_payments()` (bulk_create + invariant), `validate_payment_method()`. |
| `apps/sales/serializers/bank_card_serializer.py` | `BankCardSerializer`. |
| `apps/sales/views/bank_card_view.py` | `BankCardListCreateAPIView`, `BankCardDetailAPIView` (soft delete), `IsSuperuserOrReadOnly` permission. |
| `apps/sales/migrations/0011_...py` | Sxema: `BankCard`, `Payment.bank_card`, `Payment.is_refund`, `Sale.payment_type`, indekslar. |
| `apps/sales/migrations/0012_backfill_sale_payment_type.py` | Data migration — eski sotuvlarni to'ldirish. |
| `apps/sales/tests.py` | 17 ta test (qoida, model, serializer, sotuv/qarz/qaytarish oqimlari). |

### O'ZGARGAN fayllar

| Fayl | Nima o'zgardi |
|------|---------------|
| `apps/sales/models.py` | + `BankCard` modeli (yagona default: `save()` + DB partial unique constraint `unique_default_bank_card`); + `Sale.PaymentType` choices va `payment_type` maydoni (`db_index=True`); + `Sale.recalculate_payment_type()`; + `Payment.bank_card` (FK, `PROTECT`), `Payment.is_refund`, `Payment.clean()` (card↔bank_card invarianti, `save()` da chaqiriladi), `Payment.Meta.indexes` (`created_at`, `type+created_at`). |
| `apps/sales/serializers/sale_serializer.py` | `PaymentInputSerializer` + `bank_card` (`PrimaryKeyRelatedField`, faqat faol kartalar) va `validate_payment_method()` chaqiruvi; `PaymentSerializer` chiqishiga `bank_card`, `bank_card_name`, `is_refund`; `SaleListSerializer.fields` ga `payment_type`. |
| `apps/sales/serializers/sale_return_serializer.py` | `SaleReturnCreateSerializer` ga ixtiyoriy `payments = PaymentInputSerializer(many=True)`. |
| `apps/sales/services/sales_services.py` | To'lov yozish loop → `SalePaymentService.record_payments()` (bulk_create, bank_card bilan); statusdan keyin `sale.recalculate_payment_type(save=False)`. |
| `apps/sales/services/sale_return_service.py` | ACCOUNTING bloki qayta yozildi: qarz kamaytirilgach pul qoldig'i `payments[]` bo'yicha taqsimlanadi (`is_refund=True`), jami tekshiruvi; `payments[]` yo'q bo'lsa eski xatti-harakat (to'liq naqd); oxirida `sale.recalculate_payment_type()`. |
| `apps/sales/views/sale_view.py` | List: payments prefetch → `select_related("bank_card")`; Detail: `items__product` va `payments__bank_card` prefetch qo'shildi (N+1 tuzatildi). |
| `apps/sales/urls.py` | + `bank-cards/`, `bank-cards/<pk>/`. |
| `apps/sales/serializers/__init__.py`, `views/__init__.py` | Yangi modullar eksporti. |
| `apps/sales/admin.py` | + `BankCardAdmin`, `PaymentAdmin` (`list_select_related`); `SaleAdmin` ga `payment_type` ustuni va filtrlar. |
| `apps/debts/serializers.py` | `PayDebtSerializer` + `bank_card` maydoni + markaziy `validate_payment_method()`. |
| `apps/debts/services.py` | `pay_debt(..., bank_card=None)`; to'lovdan keyin `sale.recalculate_payment_type()`. |
| `apps/debts/views.py` | `bank_card` ni service'ga uzatish. |
| `apps/reports/services/report_service.py` | `PAYMENT_METHOD_LABELS` + `mixed`; `UNKNOWN_CARD_LABEL`; `PaymentStructureService` endi `Sale.payment_type` bo'yicha (Aralash ko'rinadi, javobga `type` maydoni qo'shildi); YANGI `BankCardBreakdownService` (karta kesimi, NET, `NULL` → "Noma'lum karta"); `ReportService.get()` javobiga `cardBreakdown` kaliti. |
| `docs/frontend-api-ozgarishlar.md` | Frontend uchun to'liq yangi bo'lim (2-qism): barcha yangi/o'zgargan endpointlar, misollar, checklist. |

---

## 4. Migratsiyalar va eski ma'lumotlar

### `0011` — sxema (ma'lumot yo'qotmaydi)
- `bank_card` jadvali yaratiladi;
- `Payment.bank_card` — **nullable** (eski to'lovlar buzilmaydi), `on_delete=PROTECT`
  (to'lovi bor kartani o'chirib bo'lmaydi — buning o'rniga soft delete);
- `Payment.is_refund` — `default=False`;
- `Sale.payment_type` — `default='debt'`, `db_index=True`;
- `Payment` uchun 2 ta indeks: `created_at` va `(type, created_at)` — hisobot so'rovlari uchun.

### `0012` — data migration (backfill)
- Har bir sotuv uchun to'lovlaridan `cash_total`/`card_total` **bitta annotate so'rov** bilan
  olinadi va `compute_payment_type()` (runtime bilan bir xil funksiya) qo'llanadi;
- 1000 talik `bulk_update` batchlarda — katta jadvalda ham xotira/tranzaksiya muammosisiz;
- **Muhim topilma va qaror:** dev bazadagi 707 ta sotuvning HAMMASI Excel importidan kelgan —
  `status=paid`, `paid_amount>0`, lekin `Payment` yozuvlari umuman YO'Q. Spetsifikatsiya
  bo'yicha "payment bo'lmasa → DEBT" ularni "qarz" qilib qo'yardi va hisobot butunlay buzilardi.
  Shuning uchun qoidaga bitta legacy-istisno kiritildi:
  **to'lov yozuvi yo'q, LEKIN `paid_amount > 0` → CASH** (o'sha davrda karta hisobi yuritilmagan).
  Qolgan barcha holatlar spetsifikatsiya bo'yicha. Natija: 707 sotuv → `cash`.
- Migratsiya qayta chaqirilsa idempotent (qiymatlarni qayta hisoblaydi), orqaga qaytishi xavfsiz
  (reverse — noop, maydon `0011` rollbackida o'chadi).

---

## 5. Validatsiyalar (qatlamma-qatlam)

| Qoida | Serializer | Model |
|-------|-----------|-------|
| `type=card` → `bank_card` majburiy | ✅ `validate_payment_method()` (sale create, sale return, pay debt) | ✅ `Payment.clean()` (`save()` da avtomatik) |
| `type=cash` → `bank_card` taqiqlanadi | ✅ | ✅ |
| Faqat faol (`is_active=True`) karta tanlanadi | ✅ `PrimaryKeyRelatedField(queryset=...)` | — |
| To'lov summasi > 0 | ✅ | — |
| To'lovlar jami yakuniy summadan oshmasin (ortiqcha to'lov) | ✅ (avvalgi bosqichda qo'shilgan) | — |
| Qaytarim `payments[]` jami pul-qoldiqqa teng | ✅ service (`create_return`) | — |
| Bitta default karta | — | ✅ `BankCard.save()` + DB partial unique constraint |

`bulk_create` Django'da `save()`/`clean()` ni chaqirmaydi — shuning uchun
`SalePaymentService.record_payments()` yozishdan oldin har bir obyekt uchun `clean()` ni
**qo'lda** chaqiradi: invariant bulk yo'lda ham ishlaydi.

---

## 6. Performance optimizatsiyalari

1. **bulk_create** — sotuvdagi bir nechta to'lov endi bitta INSERT (avval loop ichida alohida
   `create()` lar edi).
2. **N+1 tuzatildi**: sotuv list VA detail view'larida `payments` prefetch'i
   `select_related("bank_card")` bilan — `bank_card_name` qo'shimcha so'rovsiz. Detail view'da
   qo'shimcha `items__product` prefetch ham qo'shildi (avval umuman yo'q edi).
3. **Indekslar**: `Sale.payment_type` (hisobot GROUP BY / filtr), `Payment(created_at)`,
   `Payment(type, created_at)` — hisobot sana-oralig'i so'rovlari uchun.
4. **Hisobot so'rovlari**: `paymentStructure` — 2 ta SQL (guruh + qarz agregati),
   `cardBreakdown` — 1 ta SQL (`Case/When` bilan NET yig'indi).
5. **recalculate_payment_type** — bitta `aggregate` (4 filtered Sum) + `queryset.update()`
   (race-safe, model bo'ylab keraksiz to'liq save yo'q); sotuv yaratishda esa `save=False`
   rejimida yakuniy `sale.save()` bilan birlashtiriladi — qo'shimcha UPDATE chiqmaydi.
6. **Data migration** — annotate bilan bitta SELECT, 1000 talik `bulk_update` batchlar.

---

## 7. Test qilish bo'yicha yo'riqnoma

Avtomatik: `python manage.py test apps.sales` — 17 test (hammasi o'tgan):
qoida (4 holat), yagona default karta, model invariantlari, serializer validatsiyalari,
naqd/karta/aralash sotuv, qisman to'lov, karta bilan qarz yopish → mixed,
kartali qaytarim → NET recalc, qaytarim summa mos kelmasligi, `payments[]`siz qaytarim (naqd fallback).

Qo'lda tekshirish uchun ssenariylar:
1. **Karta CRUD**: superuser bilan karta yarating (`POST /api/sales/bank-cards/`), oddiy user
   bilan yaratishga urinib ko'ring (403 bo'lishi kerak), DELETE dan keyin karta ro'yxatda
   `is_active=false` bo'lishi va yangi sotuvda tanlab bo'lmasligi kerak.
2. **Aralash sotuv**: 200 000 lik savdoga 120 000 naqd + 80 000 karta yuboring →
   `payment_type="mixed"`, ikkala payment bazada, kartada `bank_card` to'ldirilgan.
3. **Validatsiya**: karta to'lovini `bank_card`siz yuboring → 400; naqd to'lovga `bank_card`
   qo'shib yuboring → 400; jami to'lovni yakuniy summadan oshiring → 400.
4. **Qarz oqimi**: qisman naqd sotuv → `payment_type="cash"`; keyin qarzni karta bilan yoping →
   `payment_type="mixed"` ga o'tishi kerak.
5. **Qaytarish**: aralash sotuvdan bitta mahsulotni karta+naqd taqsimi bilan qaytaring →
   `is_refund=true` yozuvlar va `payment_type` NETga ko'ra yangilanishi.
6. **Hisobot**: `GET /api/reports/...` → `paymentStructure` da "Aralash" qatori,
   `cardBreakdown` da kartalar va (eski ma'lumot bo'lsa) "Noma'lum karta".
7. **Admin**: Sale ro'yxatida `payment_type` ustuni/filtri, BankCard va Payment bo'limlari.

Eslatma: `python manage.py test` to'liq to'plamida `apps.inventory` da 11 ta XATO bor —
ular bu o'zgarishlardan OLDIN ham bor edi (toza git HEAD'da tekshirildi), bu ishga aloqasi yo'q.

---

## 8. Ongli ravishda O'ZGARTIRILMAGAN narsalar

- `Payment.Type` ga `mixed`/`debt` qo'shilmadi (2.2-bandga qarang).
- `Payment` jadval nomi (`sales_payment`) va mavjud maydonlari — teginilmadi.
- Qaytarim `payments[]` yubormasa — eski xatti-harakat aynan saqlangan (naqd, faqat mijozli sotuvda).
- Eski API kontraktlarida breaking change yo'q: `bank_card` faqat karta to'lovida majburiy bo'ldi —
  frontend hozircha faqat naqd yuborsa, hech narsa buzilmaydi.

---

## 9. FRONTEND UCHUN — o'zgargan/yangi API'lar va ulardan foydalanish

> To'liq versiya (barcha misollar bilan): `docs/frontend-api-ozgarishlar.md`, "2-QISM".
> Quyida frontend ishini boshlash uchun yetarli bo'lgan to'liq kontrakt.

### 9.1. YANGI: Bank kartalari spravochnigi

**`GET /api/sales/bank-cards/`** — barcha kartalar. Kassa ekrani uchun faqat faollari:
`GET /api/sales/bank-cards/?is_active=true`

```json
[
  { "id": 1, "name": "Uzcard", "is_default": true,  "is_active": true, "created_at": "2026-07-11T10:00:00" },
  { "id": 2, "name": "Humo",   "is_default": false, "is_active": true, "created_at": "2026-07-11T10:01:00" }
]
```

- `is_default: true` — karta to'lovi tanlanganda UI **avtomatik shu kartani** tanlab qo'ysin.
  Bir vaqtda faqat bitta default bo'ladi (backend kafolatlaydi).
- **`POST /api/sales/bank-cards/`** — yaratish: `{ "name": "Kapital", "is_default": false }`.
  `name` unikal. `is_default: true` yuborilsa eski default avtomatik bekor bo'ladi.
- **`PATCH /api/sales/bank-cards/{id}/`** — tahrirlash (masalan defaultni almashtirish).
- **`DELETE /api/sales/bank-cards/{id}/`** — **soft delete**: karta o'chmaydi, `is_active: false`
  bo'ladi; yangi to'lovda tanlab bo'lmaydi, eski hisobotlarda ko'rinaveradi.
- Ruxsatlar: o'qish — barcha login bo'lgan xodimlar; yaratish/tahrirlash/o'chirish — **faqat superuser**
  (oddiy userga 403 keladi — UI da tugmalarni yashiring).

### 9.2. O'ZGARDI: Sotuv yaratish — `POST /api/sales/create/`

`payments[]` elementida yangi `bank_card` maydoni:

```json
{
  "store": 1,
  "customer": 5,
  "items": [ { "product": 10, "quantity": 2, "price": 100000 } ],
  "payments": [
    { "type": "cash", "amount": 120000 },
    { "type": "card", "amount": 80000, "bank_card": 1 }
  ]
}
```

Qoidalar (buzilsa `400`, xato `bank_card` yoki `payments` kalitida):
- `type: "card"` → `bank_card` **majburiy** (faol karta ID);
- `type: "cash"` → `bank_card` **yuborilmasin**;
- naqd + karta istalgan nisbatda bo'linadi (aralash to'lov to'liq qo'llanadi);
- to'lovlar jami chegirmadan keyingi yakuniy summadan **oshmasligi** kerak;
- to'lovlar jami yetmasa — qarzga savdo: `customer` va `debt_due_date` majburiy (avvalgidek).

### 9.3. O'ZGARDI: Sotuv ro'yxati/detali — `GET /api/sales/list/`, `GET /api/sales/{id}/`

Har sotuvda YANGI read-only maydon **`payment_type`**:

| Qiymat | Ma'nosi | UI tavsiya |
|--------|---------|------------|
| `cash` | Faqat naqd | badge "Naqd" |
| `card` | Faqat karta | badge "Karta" |
| `mixed` | Naqd + karta aralash | badge "Aralash" |
| `debt` | Hali pul tushmagan (to'liq qarz) | badge "Qarz" |

`payments[]` elementlarida yangi maydonlar:

```json
{ "id": 55, "amount": "80000.00", "type": "card",
  "bank_card": 1, "bank_card_name": "Uzcard",
  "is_refund": false, "created_at": "..." }
```

- `bank_card_name` — tayyor nom, qo'shimcha so'rov kerak emas;
- `is_refund: true` — mijozga QAYTARILGAN pul (qaytarimdan kelgan) — minus/qizil ko'rsating.

### 9.4. O'ZGARDI: Qarz to'lash — `POST /api/debts/create/`

Yangi ixtiyoriy `bank_card` maydoni (qoidalar sotuvdagi bilan bir xil):

```json
{ "sale": 12, "amount": 150000, "type": "card", "bank_card": 1 }
```

Qarz karta bilan yopilsa sotuvning `payment_type` i avtomatik yangilanadi
(masalan `debt` → `card`, naqd boshlanib karta bilan yopilsa → `mixed`) — ro'yxatni refresh qiling.

### 9.5. O'ZGARDI: Sotuvni qaytarish — `POST /api/sales/sale-return/`

YANGI ixtiyoriy `payments[]` — pul mijozga QANDAY qaytarilishi:

```json
{
  "sale": 12,
  "items": [ { "sale_item": 30, "quantity": 1 } ],
  "payments": [
    { "type": "card", "amount": 80000, "bank_card": 1 },
    { "type": "cash", "amount": 20000 }
  ],
  "comment": "Mijoz qaytardi"
}
```

- `payments[]` yuborilmasa — eski xatti-harakat: pul qismi to'liq NAQD deb yoziladi;
- backend avval qaytarim summasidan sotuv QARZini yopadi, faqat qolgan qismi pul bilan qaytariladi;
- `payments[]` jami aynan shu **pul qoldig'iga teng** bo'lishi shart — teng bo'lmasa `400`
  (xato matnida kutilgan summa yoziladi, UI shu summani ko'rsatib qayta so'rashi mumkin);
- 100% naqd, 100% karta, 50/50 — istalgan kombinatsiya ishlaydi.

### 9.6. O'ZGARDI: Hisobot javobi (ReportService)

**`paymentStructure`** — endi 4 tur, har qatorda yangi `type` maydoni (rang/ikonka uchun),
`method` — tayyor o'zbekcha label:

```json
{
  "paymentStructure": [
    { "method": "Naqd",    "type": "cash",  "count": 120, "amount": "5200000.00", "percent": "52.0%" },
    { "method": "Karta",   "type": "card",  "count": 60,  "amount": "2800000.00", "percent": "28.0%" },
    { "method": "Aralash", "type": "mixed", "count": 15,  "amount": "1200000.00", "percent": "12.0%" },
    { "method": "Qarz",    "type": "debt",  "count": 10,  "amount": "800000.00",  "percent": "8.0%"  }
  ]
}
```

**YANGI `cardBreakdown`** — har bank kartasi bo'yicha NET tushum (to'lovlar − qaytarimlar):

```json
{
  "cardBreakdown": [
    { "bankCardId": 1,    "name": "Uzcard",         "count": 45, "amount": "1900000.00", "percent": "67.9%" },
    { "bankCardId": null, "name": "Noma'lum karta", "count": 5,  "amount": "200000.00",  "percent": "7.1%"  }
  ]
}
```

`bankCardId: null` — yangi tizimgacha bo'lgan eski karta to'lovlari ("Noma'lum karta" deb chiqariladi).

### 9.7. Frontend checklist

- [ ] Sozlamalar: "Bank kartalari" CRUD sahifasi (yozish faqat superuser).
- [ ] Kassa: karta to'lovida karta selektori (`?is_active=true`), default avtomatik tanlangan.
- [ ] Kassa: aralash to'lov — naqd va karta summalarini alohida kiritish.
- [ ] Sotuv ro'yxati/detali: `payment_type` badge; `payments[]` da karta nomi va refund belgisi.
- [ ] Qarz to'lash modali: karta tanlansa `bank_card` yuborish.
- [ ] Qaytarish ekrani: pul qaytarish usuli tanlash (ixtiyoriy `payments[]`).
- [ ] Hisobot: paymentStructure'ga "Aralash", yangi cardBreakdown jadval/diagramma.

**Breaking change YO'Q**: hozirgi frontend faqat naqd yuborsa hamma narsa ishlayveradi.
Yagona yangi majburiyat: `type:"card"` to'lovda `bank_card`siz so'rov endi `400` qaytaradi.
