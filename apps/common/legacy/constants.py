"""
Eski CRM ("docs/" ichidagi Excel hisobotlari) -> bizning baza ko'chirish uchun
KONSTANTALAR va MAPPING jadvallari.

Bu yerda HECH QANDAY biznes-logika yo'q — faqat:
  * fayllarni topish uchun glob shablonlari,
  * Excel ustun sarlavhalari -> mantiqiy kalit (logical key) xaritalari,
  * do'kon nomi -> Store turi,
  * spisaniye sababi -> WriteOff.Reason,
  * "Архивирован" (Да/Нет) -> Product.status.

Sarlavhalar `docs/migratsiya-tahlili.md` va fayllarning haqiqiy header
qatorlaridan olingan. Ustun NOMI bo'yicha (index emas) ishlatamiz —
fayldagi ustun tartibi o'zgarsa ham kod buzilmaydi.
"""

# ── Excel fayllarni topish (nomlari sana/UUID bilan o'zgaradi, shuning uchun glob) ──
FILE_PATTERNS = {
    "ostatki":   "Отчет_по_остаткам*.xlsx",     # joriy qoldiq (do'kon × mahsulot)
    "importy":   "Отчет_по_импортам*.xlsx",      # kirim (qator = kirim qatori)
    "prodaji":   "Отчет_по_продажам*.xlsx",       # sotuv (kunlik yig'ma)
    "transfery": "Отчет_по_трансферам*.xlsx",     # transfer
    "spisaniya": "Отчет_по_списаниям*.xlsx",      # spisaniye (write-off)
}

# ── Ustun sarlavhasi (Excel) -> mantiqiy kalit. {kalit: [bo'lishi mumkin bo'lgan sarlavhalar]} ──
# Bir nechta variant berildi — eski/yangi eksportlar orasidagi nom farqlariga chidamli.

OSTATKI_COLUMNS = {
    "store":          ["Магазин"],
    "name":           ["Наименование товара", "Наименование"],
    "sku":            ["Артикул"],
    "barcode":        ["Баркод", "Штрихкод"],
    "unit":           ["Ед. измерения", "Единица измерения"],
    "category":       ["Категория"],
    "brand":          ["Бренд"],
    "supplier":       ["Поставщик"],
    "archived":       ["Архивирован"],
    "selling_price":  ["Цена продажи"],
    "purchase_price": ["Цена поставки"],
    "quantity":       ["Кол-во"],
    "last_import":    ["last_import"],
}

IMPORTY_COLUMNS = {
    "date":           ["Дата импорта"],
    "order_id":       ["ID заказа"],
    "store":          ["Магазин"],
    "name":           ["Наименование", "Наименование товара"],
    "barcode":        ["Штрихкод", "Баркод"],
    "sku":            ["Артикул"],
    "category":       ["Категория"],
    "brand":          ["Бренд"],
    "supplier":       ["Поставщик"],
    "quantity":       ["Кол-во импортированных"],
    "purchase_total": ["Сумма импорта по цене поставки"],
    "selling_total":  ["Сумма импорта по цене продажи"],
}

PRODAJI_COLUMNS = {
    "store":          ["Название магазина", "Магазин"],
    "date":           ["Дата"],
    "name":           ["Наименование", "Наименование товара"],
    "sku":            ["Артикул"],
    "barcode":        ["Баркод", "Штрихкод"],
    "category":       ["Категория"],
    "unit":           ["Ед. измерения", "Единица измерения"],
    "sold_qty":       ["Кол-во проданных"],
    "returned_qty":   ["Кол-во возвращенных"],
    "revenue":        ["Продажи без учета скидки"],   # chegirmasiz tushum (qatorning jami)
}

TRANSFERY_COLUMNS = {
    "transfer_id":    ["ID"],
    "name":           ["Наименование", "Наименование товара"],
    "sent_at":        ["Отправлено"],
    "received_at":    ["Принято"],
    "sku":            ["Артикул"],
    "barcode":        ["Баркод", "Штрихкод"],
    "brand":          ["Бренд"],
    "category":       ["Категория"],
    "supplier":       ["Поставщик"],
    "from_store":     ["Откуда"],
    "to_store":       ["Куда"],
    "sent_qty":       ["Кол-во отправленных"],
    "received_qty":   ["Кол-во принятых"],
    "purchase_total": ["Сумма по цене поставки"],
    "selling_total":  ["Сумма по цене продажи"],
    "unit":           ["Единица измерения", "Ед. измерения"],
}

SPISANIYA_COLUMNS = {
    "writeoff_id":    ["ID списания"],
    "title":          ["Название списания"],
    "reason":         ["Причина списания"],
    "store":          ["Название магазина", "Магазин"],
    "created_at":     ["Время создания"],
    "name":           ["Названия продукта", "Наименование"],
    "sku":            ["Артикул"],
    "barcode":        ["Баркод", "Штрихкод"],
    "category":       ["Категории", "Категория"],
    "brand":          ["Бренд"],
    "supplier":       ["Поставщик"],
    "description":    ["description"],
    "purchase_price": ["Цена поставки"],
    "selling_price":  ["Цена продажи"],
    "quantity":       ["Кол-во списанных товаров"],   # DIQQAT: "Количество" ustuni o'lchov birligi ("шт"), miqdor EMAS
}

# ── Do'kon nomi -> Store.type ──────────────────────────────────────────────
# "Bozordagi sklad" — ombor (BASE), qolganlari oddiy do'kon (STORE).
# Nomlar fayldagi AYNI ko'rinishida (backtick `kon bilan) saqlanadi.
BASE_STORE_NAMES = {"Bozordagi sklad"}

# ── "Архивирован" (Да/Нет) -> Product.status (a/i) ──────────────────────────
ARCHIVED_YES = {"да", "yes", "true", "1", "ha"}

# ── Spisaniye sababi (rus) -> WriteOff.Reason kaliti ────────────────────────
# Bizning WriteOff.Reason: damaged / expired / lost / inventory / catalog / other
WRITEOFF_REASON_MAP = {
    "списание с каталога":     "catalog",
    "инвентаризация":          "inventory",
    "потеря":                  "lost",
    "исправление пересорта":   "other",   # "qayta saralash tuzatishi" — mos kelmaydi -> other
    "брак":                    "damaged",
    "испорчен":                "damaged",
    "просрочен":               "expired",
}

# ── Ko'chirish bosqichlari (FK bog'liqlik tartibida) ────────────────────────
STEPS = ["dicts", "products", "batches", "entries", "transfers", "sales", "writeoffs"]

# Ko'chirish uchun maxsus "tizim" foydalanuvchisi (Sale.seller / created_by majburiy joylar uchun).
SYSTEM_USER_PHONE = "+00000000000"
SYSTEM_USER_NAME = "Migration System"
