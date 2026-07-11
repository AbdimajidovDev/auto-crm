"""
Sotuv to'lov turi (payment_type) ni aniqlashning YAGONA markaziy qoidasi.

Bu modul atayin "pure" (ORM/Djangoga bog'liq emas) qilib yozilgan:
  - Sale.recalculate_payment_type()  (runtime oqimlari: sotuv, qarz, qaytarish)
  - data migration (eski ma'lumotlarni backfill qilish)
ikkalasi ham AYNAN SHU funksiyani chaqiradi — qoida hech qayerda takrorlanmaydi.
"""

from decimal import Decimal

# Sale.PaymentType bilan bir xil qiymatlar (circular import bo'lmasligi uchun shu yerda)
PAYMENT_TYPE_CASH = "cash"
PAYMENT_TYPE_CARD = "card"
PAYMENT_TYPE_MIXED = "mixed"
PAYMENT_TYPE_DEBT = "debt"


def compute_payment_type(cash_total: Decimal, card_total: Decimal) -> str:
    """
    cash_total va card_total — mijozdan kelgan NET to'lovlar
    (kirim to'lovlar minus shu usuldagi qaytarimlar).

      cash == 0, card == 0  → DEBT
      cash  > 0, card == 0  → CASH
      cash == 0, card  > 0  → CARD
      cash  > 0, card  > 0  → MIXED
    """
    cash_total = cash_total or Decimal("0")
    card_total = card_total or Decimal("0")

    if cash_total > 0 and card_total > 0:
        return PAYMENT_TYPE_MIXED
    if cash_total > 0:
        return PAYMENT_TYPE_CASH
    if card_total > 0:
        return PAYMENT_TYPE_CARD
    return PAYMENT_TYPE_DEBT
