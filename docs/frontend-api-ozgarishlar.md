# Frontend uchun API o'zgarishlari — pagination

> **Sana:** 2026-07-01
> **Kimga:** Frontend jamoasi
> **Sabab:** Eski CRM ma'lumotlari bazaga ko'chirilgandan keyin ba'zi ro'yxat (list) endpointlar
> **juda uzoq yuklanardi** (masalan transferlar ro'yxati). Sababi — ular **paginationsiz** butun jadvalni
> (o'n minglab qator) bitta javobda qaytarardi va ortiqcha SQL so'rovlar (N+1) qilardi.
>
> **Hozir tuzatildi.** Quyidagi 3 ta GET endpoint endi **sahifalangan (paginated)** javob qaytaradi.
> Bu **javob shakli o'zgarishi** (breaking change) — frontend shu 3 joyni moslashtirishi kerak.

---

## 0. Eng muhim o'zgarish (bir jumlada)

Bu endpointlar **avval to'g'ridan-to'g'ri massiv** (`[ {...}, {...} ]`) qaytarardi.
**Endi obyekt** qaytaradi va haqiqiy ro'yxat `results` ichida bo'ladi:

```json
{
  "count": 7653,
  "total_pages": 3827,
  "current_page": 1,
  "next": "http://.../?limit=2&page=2",
  "previous": null,
  "results": [ { ... }, { ... } ]
}
```

➡️ Ya'ni: `response.data` o'rniga **`response.data.results`** ni o'qing.
Har bir obyektning **ichki maydonlari o'zgarmadi** — faqat tashqi o'ram (wrapper) qo'shildi.

### Yangi query parametrlar (barcha 3 endpoint uchun bir xil)

| Parametr | Ma'nosi | Default | Cheklov |
|----------|---------|---------|---------|
| `page`   | Sahifa raqami | `1` | — |
| `limit`  | Sahifadagi qatorlar soni | `20` | maksimal `100` |

Masalan: `GET /api/transfer/?page=2&limit=50`

### Wrapper (o'ram) maydonlari

| Maydon | Tur | Ma'nosi |
|--------|-----|---------|
| `count` | int | Jami qatorlar soni (butun jadval bo'yicha) |
| `total_pages` | int | Jami sahifalar soni |
| `current_page` | int | Hozirgi sahifa |
| `next` | string / null | Keyingi sahifa URL (oxirgi sahifada `null`) |
| `previous` | string / null | Oldingi sahifa URL (birinchi sahifada `null`) |
| `results` | array | **Haqiqiy ro'yxat** shu yerda |

---

## 1. Transferlar ro'yxati — `GET /api/transfer/`

**View:** `TransferListAPIView`
**Nima o'zgardi:** paginationsiz massiv → **paginated obyekt**. (Bu endpoint eng sekin edi — bazada 7600+ transfer.)

### ❌ AVVAL
```json
[
  { "id": 7654, "from_store": 43, "items": [ ... ] },
  { "id": 7655, "from_store": 44, "items": [ ... ] }
]
```

### ✅ HOZIR
```json
{
  "count": 7653,
  "total_pages": 3827,
  "current_page": 1,
  "next": "http://.../api/transfer/?limit=2&page=2",
  "previous": null,
  "results": [
    {
      "id": 7654,
      "from_store": 43,
      "from_store_name": "112 do`kon",
      "to_store": 46,
      "to_store_name": "96-do`kon",
      "status": "a",
      "created_by": 18,
      "approved_by": 18,
      "approved_by_name": "Migration System",
      "approved_at": "2026-06-28T18:16:54+05:00",
      "items": [
        {
          "id": 38193,
          "product": 12698,
          "product_name": "Osvejitel areon X perience",
          "sku": "A07851",
          "quantity": 1,
          "purchase_price": "24100.00",
          "selling_price": "35000.00"
        }
      ]
    }
  ]
}
```

**Frontend nima qilishi kerak:**
- `res.data` → `res.data.results` ga o'zgartiring.
- Sahifalash UI (pagination) `count` / `total_pages` / `current_page` dan foydalanadi.
- Element maydonlari (`from_store_name`, `items`, `product_name`, `sku` ...) **o'zgarmadi**.

---

## 2. Qarzlar ro'yxati — `GET /api/debts/list/`

**View:** `PayDebtListAPIView`
**Nima o'zgardi:** paginationsiz massiv → **paginated obyekt** (+ ichki N+1 tuzatildi).

### ❌ AVVAL
```json
[
  { "id": 1, "sale": 10, "customer": 5, "customer_name": "Ali", "amount": "50000.00", "type": "i", "created_at": "..." }
]
```

### ✅ HOZIR
```json
{
  "count": 120,
  "total_pages": 6,
  "current_page": 1,
  "next": "http://.../api/debts/list/?page=2",
  "previous": null,
  "results": [
    { "id": 1, "sale": 10, "customer": 5, "customer_name": "Ali", "amount": "50000.00", "type": "i", "created_at": "..." }
  ]
}
```

**Frontend nima qilishi kerak:**
- `res.data` → `res.data.results`.
- `page` / `limit` parametrlarini qo'llang.

> ⚠️ **Diqqat (backend eslatmasi):** bu endpointda hali **ma'lum bir mantiqiy nomuvofiqlik** bor
> (serializer `Payment` modeliga yozilgan, lekin ma'lumot `CustomerDebt` dan keladi). Bu **alohida** tuzatiladi —
> pagination o'zgarishiga aloqasi yo'q. Agar `type` yoki ba'zi maydonlar g'alati ko'rinsa, backendga xabar bering.

---

## 3. Qarz (bitta) — `GET /api/debts/<id>/`

**View:** `PayDebtDetailAPIView`
**Nima o'zgardi:** faqat **ichki tezlashtirish** (`select_related`) — **javob shakli O'ZGARMADI.**
Frontend tomonda **hech narsa qilish shart emas**. Oldingidek bitta obyekt qaytaradi, endi tezroq.

---

## 4. Foydalanuvchilar (sellerlar) ro'yxati — `GET /api/users/`

**View:** `UsersListView`
**Nima o'zgardi:** paginationsiz massiv → **paginated obyekt**.

### ❌ AVVAL
```json
[
  { "id": 3, "full_name": "Vali", "phone_number": "+998...", "store_id": 2, "store_name": "Avtoyon", ... }
]
```

### ✅ HOZIR
```json
{
  "count": 45,
  "total_pages": 3,
  "current_page": 1,
  "next": "http://.../api/users/?page=2",
  "previous": null,
  "results": [
    { "id": 3, "full_name": "Vali", "phone_number": "+998...", "store_id": 2, "store_name": "Avtoyon", ... }
  ]
}
```

**Frontend nima qilishi kerak:**
- `res.data` → `res.data.results`.
- `page` / `limit` parametrlarini qo'llang.
- Element maydonlari **o'zgarmadi**.

---

## 4.1. Inventarizatsiya mahsulotlari — `GET /api/inventory/list/<session_id>/`

**View:** `InventoryDetailAPIView`
**Nima o'zgardi:** ko'p ma'lumotdan **qotib qolardi** — endi mahsulotlar **paginated**, `checked`/`checked_count`
alohida maydon, va `status` filtri qo'shildi. (Bu **breaking change** — frontend moslashtiriladi.)

### ❌ AVVAL
```json
{
  "products": [ { "product_id": 1, "scanned": 8, "sold_out": 2, "final": 9, ... }, ... ],
  "checked":  [ { ... } ]
}
```
> `products` — sessiyadagi BARCHA mahsulot (paginationsiz), `checked` — o'shalardan filtrlab olingani.

### ✅ HOZIR
```json
{
  "count": 12500,
  "total_pages": 625,
  "current_page": 1,
  "next": "http://.../api/inventory/list/7/?page=2",
  "previous": null,
  "results": [
    {
      "product_id": 1, "product_name": "Brake Pad", "barcode": "2000...",
      "declared": 10, "scanned": 8,
      "sold_out": 2, "returned": 1, "transfer_out": 0, "transfer_in": 0, "entry": 3,
      "final": 10, "difference": 0, "status": "e", "is_check": true
    }
  ],
  "checked": [
    { "product_id": 1, "product_name": "Brake Pad", "barcode": "2000...", "scanned": 8, "status": "e", "is_check": true }
  ],
  "checked_count": 42
}
```

**Frontend nima qilishi kerak:**
- **Mahsulot ro'yxati** endi `res.data.results` (paginated) — `?page=` / `?limit=` bilan yuklanadi.
- **Belgilangan (checked) ro'yxat** endi `res.data.checked` (alohida maydon, **butun sessiya bo'yicha**, paginationdan mustaqil). Eskidagidek `products` ichidan filtrlash **shart emas**.
- **Belgilanganlar soni** — `res.data.checked_count` (butun sessiya). Bu SQL darajasida hisoblanadi, tez.
- ⚠️ Diqqat: `checked` endi **yengil** shaklda (`product_id, product_name, barcode, scanned, status, is_check`) — movement hisob-kitoblari (`sold_out`, `final`, ...) faqat `results` ichida bo'ladi.

**Yangi filter:** `?status=checked | unchecked | all` (default `all`)
- `checked` — faqat belgilangan mahsulotlar
- `unchecked` — hali belgilanmaganlar (umuman sanalmagan yoki `is_check=false`)
- `all` — barchasi

> Eslatma: avvalgi `?status=p|e|l|m` (count status) filtri bu endpointda **`checked|unchecked|all` ga almashtirildi**.
> Count status (ko'p/kam chiqqan) bo'yicha ajratish uchun alohida endpointlar bor:
> `/api/inventory/sessions/<id>/over/` (ko'p) va `/api/inventory/sessions/<id>/short/` (kam).

---

## 5. Umumiy migratsiya bo'yicha eslatma (frontend uchun namuna kod)

Loyihada boshqa list endpointlar (masalan sotuvlar, mahsulotlar) **allaqachon** shu bir xil pagination
formatini (`count / total_pages / current_page / next / previous / results`) ishlatadi.
Shu sabab, agar sizda umumiy "paginated response" helper bo'lsa — yuqoridagi 3 endpointni ham o'shanga ulang.

```ts
// Umumiy tur (TypeScript)
interface Paginated<T> {
  count: number;
  total_pages: number;
  current_page: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

// Misol: transferlar
const res = await api.get<Paginated<Transfer>>("/api/transfer/", {
  params: { page, limit: 20 },
});
const transfers = res.data.results;   // ⬅️ avvalgi `res.data` o'rniga
const totalPages = res.data.total_pages;
```

---

## 6. Qisqacha jadval — nima o'zgardi

| Endpoint | Metod | O'zgarish | Frontend harakati |
|----------|-------|-----------|-------------------|
| `/api/transfer/` | GET | Massiv → paginated obyekt | `res.data.results` + pagination |
| `/api/debts/list/` | GET | Massiv → paginated obyekt | `res.data.results` + pagination |
| `/api/users/` | GET | Massiv → paginated obyekt | `res.data.results` + pagination |
| `/api/inventory/list/<session_id>/` | GET | `{products,checked}` → paginated `results` + `checked` + `checked_count` + `?status=` | `res.data.results` (paginated), `checked`/`checked_count` alohida |
| `/api/debts/<id>/` | GET | Faqat ichki tezlashtirish | Hech narsa (shakl o'zgarmadi) |

**Muhim:** faqat yuqoridagi **3 ta list endpoint**ning javob shakli o'zgardi. Boshqa endpointlar
(create, approve, reject, detail, login va h.k.) **o'zgarmadi**.

---

## 7. Nega bu o'zgarish qilindi (texnik izoh)

Transfer ro'yxatida o'lchangan natija (20 qatorli sahifa uchun):

| Holat | SQL so'rovlar soni |
|-------|--------------------|
| Avval (paginationsiz + N+1) | **188 ta** so'rov (va butun 7653 qator yuklanardi) |
| Hozir (pagination + `select_related`/`prefetch_related`) | **4 ta** so'rov |

Ya'ni javob endi **jadval hajmidan qat'i nazar** doim yengil va tez.


---
---

# 2-QISM: To'lov tizimi yangilandi — Bank kartalari, aralash to'lov, payment_type

> **Sana:** 2026-07-11
> **Kimga:** Frontend jamoasi
> **Sabab:** Sotuvda aralash to'lov (naqd + karta) modelda to'g'ri aks etmasdi va qaysi bank
> kartasiga qancha pul tushganini bilish imkonsiz edi. Endi kompaniya istalgancha **bank kartasi**
> yaratadi, mijoz to'lovni istalgan nisbatda **naqd + bitta karta** ga bo'lib to'laydi, hisobotlar
> **Naqd / Karta / Aralash / Qarz** va **har bir karta kesimida** chiqadi.

---

## 2.0. Yangi tushunchalar (bir ko'rishda)

| Tushuncha | Ma'nosi |
|-----------|---------|
| `BankCard` | Kompaniyaning ichki karta spravochnigi (Uzcard, Humo, Click...). Faqat nom saqlanadi — karta raqami YO'Q, to'lov tizimlari bilan integratsiya YO'Q. |
| `Payment.bank_card` | Karta to'lovi QAYSI kartaga tushgani. `type="card"` bo'lsa **majburiy**, `type="cash"` bo'lsa yuborish **taqiqlanadi**. |
| `Payment.is_refund` | `true` — bu mijozga QAYTARILGAN pul (sotuv qaytarimi), `false` — mijozdan KELGAN pul. |
| `Sale.payment_type` | Sotuvning to'lov tarkibi: `cash` / `card` / `mixed` / `debt`. **Faqat backend hisoblaydi** — frontend hech qachon yubormaydi, faqat o'qiydi. |

---

## 2.1. YANGI: Bank kartalari CRUD — `/api/sales/bank-cards/`

O'qish — barcha xodimlar, yaratish/tahrirlash/o'chirish — **faqat superuser**.

### Ro'yxat — `GET /api/sales/bank-cards/`

Kassa ekrani uchun faqat faollari: `GET /api/sales/bank-cards/?is_active=true`

```json
[
  { "id": 1, "name": "Uzcard",  "is_default": true,  "is_active": true, "created_at": "..." },
  { "id": 2, "name": "Humo",    "is_default": false, "is_active": true, "created_at": "..." }
]
```

`is_default: true` — karta to'lov tanlanganda frontend AVTOMATIK shu kartani tanlab qo'yishi kerak.
Bir vaqtda faqat bitta karta default bo'ladi (backend kafolatlaydi).

### Yaratish — `POST /api/sales/bank-cards/`

```json
{ "name": "Kapital", "is_default": false }
```

`name` unikal. Yangi karta `is_default: true` bilan yaratilsa — eski default avtomatik bekor bo'ladi.

### Tahrirlash — `PATCH /api/sales/bank-cards/{id}/`  |  O'chirish — `DELETE /api/sales/bank-cards/{id}/`

DELETE — **soft delete**: karta o'chmaydi, `is_active: false` bo'ladi (eski to'lovlar tarixi saqlanadi).
O'chirilgan karta yangi to'lovlarda tanlab bo'lmaydi, lekin eski hisobotlarda ko'rinaveradi.

---

## 2.2. O'ZGARDI: Sotuv yaratish — `POST /api/sales/create/`

`payments[]` ichida karta to'loviga endi `bank_card` (karta ID) **majburiy**:

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

Qoidalar (buzilsa `400` + `bank_card` maydonida xato):
- `type: "card"` → `bank_card` **majburiy** va faol karta bo'lishi kerak;
- `type: "cash"` → `bank_card` **yuborilmasin**;
- to'lovlar jami yakuniy (chegirmadan keyingi) summadan **oshmasligi** kerak;
- naqd + karta istalgan nisbatda bo'linadi, karta to'lovi bitta yoki bir nechta bo'lishi mumkin.

---

## 2.3. O'ZGARDI: Sotuv ro'yxati/detali — `GET /api/sales/list/`, `GET /api/sales/{id}/`

Har bir sotuvda YANGI maydon `payment_type` (faqat o'qish uchun):

| Qiymat | Ma'nosi |
|--------|---------|
| `cash` | Faqat naqd to'langan |
| `card` | Faqat karta bilan to'langan |
| `mixed` | Naqd + karta aralash |
| `debt` | Hali hech qanday pul tushmagan (to'liq qarz) |

`payments[]` elementlarida yangi maydonlar:

```json
{
  "id": 55, "amount": "80000.00", "type": "card",
  "bank_card": 1, "bank_card_name": "Uzcard",
  "is_refund": false, "created_at": "..."
}
```

`is_refund: true` bo'lgan yozuvlar — mijozga qaytarilgan pul (UI da minus/qizil ko'rsatish tavsiya etiladi).

---

## 2.4. O'ZGARDI: Qarz to'lash — `POST /api/debts/create/`

Karta bilan qarz yopilsa `bank_card` majburiy (qoidalar sotuvdagi bilan bir xil):

```json
{ "sale": 12, "amount": 150000, "type": "card", "bank_card": 1 }
```

Qarz to'lovidan keyin sotuvning `payment_type` i avtomatik yangilanadi
(masalan `debt` → `card`, yoki naqd boshlanib karta bilan yopilsa → `mixed`).

---

## 2.5. O'ZGARDI: Sotuvni qaytarish — `POST /api/sales/sale-return/`

Endi qaytariladigan pul ham xuddi sotuvdagidek taqsimlanadi — YANGI ixtiyoriy `payments[]`:

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

Qoidalar:
- `payments[]` **ixtiyoriy** — yuborilmasa, pul qismi avvalgidek to'liq NAQD deb yoziladi;
- avval qaytarim sotuv QARZini kamaytiradi, faqat qolgan qismi pul bilan qaytariladi;
- `payments[]` jami aynan shu **pul bilan qaytariladigan qoldiqqa teng** bo'lishi shart, aks holda `400`
  (xato matnida kutilgan summa yoziladi);
- qaytarim to'lovlari bazada `is_refund: true` bilan saqlanadi va sotuvning `payment_type` i qayta hisoblanadi.

---

## 2.6. O'ZGARDI: Hisobot — `GET /api/reports/...` (ReportService javobi)

### `paymentStructure` — endi SOTUV darajasida (Aralash qo'shildi)

```json
"paymentStructure": [
  { "method": "Naqd",    "type": "cash",  "count": 120, "amount": "5200000.00", "percent": "52.0%" },
  { "method": "Karta",   "type": "card",  "count": 60,  "amount": "2800000.00", "percent": "28.0%" },
  { "method": "Aralash", "type": "mixed", "count": 15,  "amount": "1200000.00", "percent": "12.0%" },
  { "method": "Qarz",    "type": "debt",  "count": 10,  "amount": "800000.00",  "percent": "8.0%"  }
]
```

Diqqat: har bir qatorda YANGI `type` maydoni bor (UI rang/ikonka uchun undan foydalaning,
`method` — tayyor o'zbekcha label). `amount`: cash/card/mixed uchun — real tushgan pul
(`paid_amount`), `debt` uchun — to'lanmagan qoldiq.

### YANGI blok: `cardBreakdown` — har bir bank kartasi bo'yicha tushum

```json
"cardBreakdown": [
  { "bankCardId": 1,    "name": "Uzcard",         "count": 45, "amount": "1900000.00", "percent": "67.9%" },
  { "bankCardId": 2,    "name": "Humo",           "count": 12, "amount": "700000.00",  "percent": "25.0%" },
  { "bankCardId": null, "name": "Noma'lum karta", "count": 5,  "amount": "200000.00",  "percent": "7.1%"  }
]
```

- `amount` — NET (karta to'lovlari MINUS shu kartaga qilingan qaytarimlar);
- `bankCardId: null` / `"Noma'lum karta"` — yangi tizimgacha qilingan eski karta to'lovlari.

---

## 2.7. Frontend uchun qisqa checklist

- [ ] Sozlamalarda "Bank kartalari" boshqaruv sahifasi (CRUD, faqat superuser'ga yozish).
- [ ] Kassa (sotuv) ekrani: to'lov usullari ro'yxatiga karta tanlansa karta selektori chiqsin
      (`GET /api/sales/bank-cards/?is_active=true`), default karta avtomatik tanlansin.
- [ ] Aralash to'lov UI: naqd + karta summalarini alohida kiritish (jami yakuniy summadan oshmasin).
- [ ] Sotuv ro'yxati/detali: `payment_type` badge (Naqd/Karta/Aralash/Qarz).
- [ ] Qarz to'lash modali: karta tanlansa `bank_card` yuborish.
- [ ] Qaytarish ekrani: pul qaytarish usullarini tanlash (ixtiyoriy `payments[]`).
- [ ] Hisobot sahifasi: `paymentStructure` da "Aralash" qatori + yangi `cardBreakdown` jadval/diagramma.
