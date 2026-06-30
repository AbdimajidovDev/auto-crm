"""
Lug'at (dictionary) va mahsulot RESOLVER'lari.

Maqsad: matnli qiymatlarni (do'kon nomi, kategoriya, brend, postavshik, o'lchov
birligi, mahsulot sku/barcode) bizning FK obyektlarimizga aylantirish — N+1
so'rovlarsiz (hammasi xotirada cache qilinadi).

Har bir resolver `get_or_create` semantikasida: avval cache, keyin baza,
topilmasa yaratiladi. `dry_run` rejimida HECH NARSA yozilmaydi — faqat mavjudlar
qaytariladi, yangilar uchun None (chaqiruvchi buni hisobga oladi).
"""
from apps.contract.models import Supplier
from apps.products.models import (
    Brand,
    Category,
    Product,
    ProductUnitMeasurement,
)
from apps.products.utils.barcode_utility import normalize_barcode
from apps.store.models import Store
from apps.users.models import User

from .constants import (
    ARCHIVED_YES,
    BASE_STORE_NAMES,
    SYSTEM_USER_NAME,
    SYSTEM_USER_PHONE,
)


def _norm(name: str) -> str:
    """Case-insensitive solishtirish uchun kalit."""
    return (name or "").strip().lower()


# Turli xil apostrof/backtik belgilari — do'kon nomlarida bir xil hisoblanishi kerak.
# Masalan Excel'da "112 do`kon" (backtik), bazada esa "112 do'kon" (apostrof) —
# bular AYNI do'kon. Aks holda dublikat do'kon yaratiladi.
_QUOTE_CHARS = "`'’‘´ʼ"
_QUOTE_TABLE = {ord(ch): "'" for ch in _QUOTE_CHARS}


def _store_key(name: str) -> str:
    """Do'kon nomini solishtirish kaliti: barcha apostrof/backtik variantlari birxil."""
    return _norm(name).translate(_QUOTE_TABLE)


class DictionaryResolver:
    """Store / Supplier / Category / Brand / Unit uchun cache'langan get_or_create."""

    def __init__(self, *, dry_run=False):
        self.dry_run = dry_run
        self.created = {"store": 0, "supplier": 0, "category": 0, "brand": 0, "unit": 0}

        self._stores = {_store_key(s.name): s for s in Store.objects.all()}
        self._suppliers = {_norm(s.name): s for s in Supplier.objects.all()}
        self._categories = {_norm(c.name): c for c in Category.objects.all()}
        self._brands = {_norm(b.name): b for b in Brand.objects.all()}
        self._units = {_norm(u.measurement): u for u in ProductUnitMeasurement.objects.all()}

    # ── Store ───────────────────────────────────────────────────────────────
    def store(self, name: str):
        key = _store_key(name)
        if not key:
            return None
        if key in self._stores:
            return self._stores[key]
        if self.dry_run:
            return None
        store_type = (
            Store.StoreType.BASE if name.strip() in BASE_STORE_NAMES
            else Store.StoreType.STORE
        )
        obj = Store.objects.create(
            name=name.strip(),
            phone_number="",
            address="",
            type=store_type,
        )
        self._stores[key] = obj
        self.created["store"] += 1
        return obj

    # ── Supplier ──────────────────────────────────────────────────────────────
    def supplier(self, name: str):
        key = _norm(name)
        if not key:
            return None
        if key in self._suppliers:
            return self._suppliers[key]
        if self.dry_run:
            return None
        obj = Supplier.objects.create(
            name=name.strip(),
            phone_number="",
            description="",
            address="",
        )
        self._suppliers[key] = obj
        self.created["supplier"] += 1
        return obj

    # ── Category ──────────────────────────────────────────────────────────────
    def category(self, name: str):
        key = _norm(name)
        if not key:
            return None
        if key in self._categories:
            return self._categories[key]
        if self.dry_run:
            return None
        # Category.save() slug'ni avtomatik yasaydi — get_or_create EMAS, create() kerak.
        obj = Category.objects.create(name=name.strip(), description="")
        self._categories[key] = obj
        self.created["category"] += 1
        return obj

    # ── Brand ─────────────────────────────────────────────────────────────────
    def brand(self, name: str):
        key = _norm(name)
        if not key:
            return None
        if key in self._brands:
            return self._brands[key]
        if self.dry_run:
            return None
        obj = Brand.objects.create(name=name.strip())
        self._brands[key] = obj
        self.created["brand"] += 1
        return obj

    # ── Unit ──────────────────────────────────────────────────────────────────
    def unit(self, name: str):
        key = _norm(name) or "dona"
        if key in self._units:
            return self._units[key]
        if self.dry_run:
            return None
        obj = ProductUnitMeasurement.objects.create(measurement=name.strip() or "dona")
        self._units[key] = obj
        self.created["unit"] += 1
        return obj


class ProductResolver:
    """
    Mahsulotni sku (Артикул) yoki barcode (Баркод) bo'yicha topadi/yaratadi.

    Eski sku/barcode AYNAN saqlanadi: `Product.save()` faqat sku/barcode BO'SH
    bo'lganda avtomatik generatsiya qiladi, shuning uchun qiymat berilsa
    o'zgartirmaydi (`apps/products/models.py` Product.save()).
    """

    def __init__(self, *, dicts: DictionaryResolver, dry_run=False):
        self.dicts = dicts
        self.dry_run = dry_run
        self.created_on_demand = 0

        # sku/barcode -> Product.id (instance emas, xotira tejash uchun)
        self._by_sku = {}
        self._by_barcode = {}
        for pid, sku, barcode in Product.objects.values_list("id", "sku", "barcode"):
            if sku:
                self._by_sku[sku.strip()] = pid
            if barcode:
                self._by_barcode[barcode.strip()] = pid

    def _lookup_id(self, sku: str, barcode: str):
        sku = (sku or "").strip()
        barcode = (barcode or "").strip()
        if sku and sku in self._by_sku:
            return self._by_sku[sku]
        if barcode and barcode in self._by_barcode:
            return self._by_barcode[barcode]
        return None

    def resolve_id(self, *, sku, barcode, name="", category="", brand="", unit="",
                   archived=""):
        """
        Mavjud bo'lsa Product.id qaytaradi. Aks holda — minimal Product yaratadi
        (sotuv/transfer/spisaniyeda uchragan, lekin "остатки"da yo'q mahsulotlar
        uchun). dry_run'da yangi yaratmaydi -> None.
        """
        pid = self._lookup_id(sku, barcode)
        if pid is not None:
            return pid
        if self.dry_run:
            return None

        product = self._build_product(
            sku=sku, barcode=barcode, name=name,
            category=category, brand=brand, unit=unit, archived=archived,
        )
        # Eski barcode allaqachon band bo'lsa (boshqa mahsulotda) — bo'sh qoldiramiz
        # (unique buzilmasligi uchun). bulk_create barcode'ni generatsiya qilmaydi,
        # demak shunchaki None bo'lib qoladi.
        if product.barcode and self.barcode_exists(product.barcode):
            product.barcode = None
        # bulk_create (save() EMAS): eski sku/barcode aynan saqlanadi, shtrix-rasm
        # yaratilmaydi — katalog mahsulotlari bilan bir xil (izchil), tezroq.
        product = Product.objects.bulk_create([product])[0]
        self._cache(product)
        self.created_on_demand += 1
        return product.id

    @staticmethod
    def _safe_barcode(raw):
        """Barcode'ni EAN-13'ga keltiradi; bo'sh/yaroqsiz bo'lsa None (save() avtomatik yasaydi)."""
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            return normalize_barcode(raw)
        except ValueError:
            return None

    def _build_product(self, *, sku, barcode, name, category, brand, unit, archived):
        sku = (sku or "").strip() or None
        barcode = self._safe_barcode(barcode)
        status = (
            Product.ProductStatus.INACTIVE
            if _norm(archived) in ARCHIVED_YES
            else Product.ProductStatus.ACTIVE
        )
        return Product(
            name=(name or "").strip()[:100] or "(nomsiz)",
            sku=sku,
            barcode=barcode,
            category=self.dicts.category(category),
            brand=self.dicts.brand(brand),
            unit_measurement=self.dicts.unit(unit),
            status=status,
        )

    def _cache(self, product):
        if product.sku:
            self._by_sku[product.sku.strip()] = product.id
        if product.barcode:
            self._by_barcode[product.barcode.strip()] = product.id

    def has(self, *, sku, barcode) -> bool:
        return self._lookup_id(sku, barcode) is not None

    def sku_exists(self, sku: str) -> bool:
        sku = (sku or "").strip()
        return bool(sku) and sku in self._by_sku

    def barcode_exists(self, barcode: str) -> bool:
        barcode = (barcode or "").strip()
        return bool(barcode) and barcode in self._by_barcode

    def register(self, product):
        """`bulk_create`'dan keyin yangi mahsulotni cache'ga qo'shadi."""
        self._cache(product)


def get_system_user():
    """
    Ko'chirish uchun maxsus foydalanuvchi (Sale.seller, created_by majburiy joylar).
    Parol ishlatib bo'lmaydigan qilib qo'yiladi (login qila olmaydi).
    """
    user = User.objects.filter(phone_number=SYSTEM_USER_PHONE).first()
    if user:
        return user
    user = User(
        phone_number=SYSTEM_USER_PHONE,
        full_name=SYSTEM_USER_NAME,
        is_active=False,
        is_staff=False,
    )
    user.set_unusable_password()
    user.save()
    return user
