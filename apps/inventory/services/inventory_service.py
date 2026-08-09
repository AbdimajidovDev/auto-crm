from django.db import transaction
from django.db.models import Sum, F, Case, When, IntegerField
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.common.quantity import validate_quantity_step
from apps.inventory.models import (
    InventorySession,
    InventorySnapshot,
    InventoryCount,
    InventoryMovement,
)
from apps.products.models import Product, ProductBatch


def _validate_counted_quantity(product_id, quantity):
    """Sanoq miqdorini mahsulot turiga (is_pair) qarab tekshiradi. 0 joiz."""
    product = Product.objects.filter(id=product_id).only("name", "is_pair").first()
    if product is None:
        raise ValidationError("Mahsulot topilmadi")
    return validate_quantity_step(
        quantity, is_pair=product.is_pair, product_name=product.name, allow_zero=True
    )


class InventoryService:

    # 🔹 START SESSION
    @staticmethod
    @transaction.atomic
    def start_session(*, user, store_id):

        # ✅ YAXSHI: Session yaratish va snapshot/count yozish bitta transaction ichida bajariladi.
        if InventorySession.objects.filter(
                store_id=store_id,
                status="active"
        ).exists():
            raise ValidationError("Active session mavjud")

        session = InventorySession.objects.create(
            store_id=store_id,
            started_by=user
        )

        batches = (
            ProductBatch.objects
            .filter(store_id=store_id)
            .values("product_id")
            .annotate(total=Sum("quantity"))
        )

        snapshots = []
        counts = []

        for b in batches:
            snapshots.append(
                InventorySnapshot(
                    session=session,
                    product_id=b["product_id"],
                    store_id=store_id,
                    expected_quantity=b["total"]
                )
            )

            counts.append(
                InventoryCount(
                    session=session,
                    product_id=b["product_id"],
                    counted_quantity=0,
                    status=InventoryCount.Status.PENDING
                )
            )

        # ✅ YAXSHI: Snapshot va count yozuvlari `bulk_create` bilan yozilyapti.
        InventorySnapshot.objects.bulk_create(snapshots)
        InventoryCount.objects.bulk_create(counts)

        return session


    # 🔹 COUNT (SCAN)

    @staticmethod
    @transaction.atomic
    def scan_product(*, session_id, product_id, quantity):

        session = InventorySession.objects.select_for_update().get(id=session_id)

        if session.status != InventorySession.Status.ACTIVE:
            raise ValidationError("Session yopilgan")

        # Qadam: juft mahsulotda 0.5, oddiyda faqat butun son
        quantity = _validate_counted_quantity(product_id, quantity)

        obj, _ = InventoryCount.objects.get_or_create(
            session=session,
            product_id=product_id,
        )

        # 🔥 ASOSIY O‘ZGARISH
        obj.counted_quantity = quantity
        obj.is_check = True
        obj.counted_at = timezone.now()
        obj.save(update_fields=["counted_quantity", "is_check", "counted_at"])


    @staticmethod
    @transaction.atomic
    def set_count(*, session_id, product_id, quantity):

        session = InventorySession.objects.select_for_update().get(id=session_id)

        if session.status != "active":
            raise ValidationError("Session yopilgan")

        # Qadam: juft mahsulotda 0.5, oddiyda faqat butun son
        quantity = _validate_counted_quantity(product_id, quantity)

        # Sessiya boshlangandan keyin do'konga kirim qilingan mahsulotning
        # count/snapshot qatori bo'lmaydi — `get()` bunda 500 berardi.
        count, _ = InventoryCount.objects.select_for_update().get_or_create(
            session=session,
            product_id=product_id,
            defaults={"counted_quantity": 0, "status": InventoryCount.Status.PENDING},
        )

        snapshot, _ = InventorySnapshot.objects.get_or_create(
            session=session,
            product_id=product_id,
            defaults={"store_id": session.store_id, "expected_quantity": 0},
        )

        count.counted_quantity = quantity

        if quantity == snapshot.expected_quantity:
            count.status = InventoryCount.Status.EQUAL
        elif quantity < snapshot.expected_quantity:
            count.status = InventoryCount.Status.LESS
        else:
            count.status = InventoryCount.Status.MORE

        count.is_check = True
        count.counted_at = timezone.now()
        count.save(update_fields=["counted_quantity", "status", "is_check", "counted_at"])


    # 🔹 FINALIZE

    @staticmethod
    @transaction.atomic
    def finalize(*, session_id):

        session = InventorySession.objects.select_for_update().get(id=session_id)

        if session.status != InventorySession.Status.ACTIVE:
            raise ValidationError("Session yopilgan")

        # Sanalgan mahsulotlar: (counted_quantity, sanoq vaqti).
        # `is_check=False` — mahsulot umuman sanalmagan; uni "0 topildi" deb
        # hisoblash butun qoldiqni nolga tushirib, ulkan kamomad yozardi,
        # shuning uchun bunday mahsulotlar finalize'da chetlab o'tiladi.
        counted_rows = {
            row["product_id"]: row
            for row in InventoryCount.objects
            .filter(session=session, is_check=True)
            .values("product_id", "counted_quantity", "counted_at")
        }

        # Harakatlar SANOQDAN KEYIN sodir bo'lganlari bo'yicha jamlanadi.
        #
        # Ilgari butun sessiya davomidagi harakatlar ayirilardi, holbuki
        # `counted` — javonda AYNAN SANOQ PAYTIDA ko'rilgan miqdor, ya'ni undan
        # oldingi sotuv/transferlar allaqachon hisobga olingan. Natijada ular
        # ikkinchi marta ayirilib, mavjud tovar kamomad sifatida spisaniye
        # qilinardi (dushanba ochilgan sessiyada 50 dona sotilib, juma kuni 200
        # dona sanalsa, qoldiq 150 qilib yozilardi).
        #
        # Kesim vaqti har mahsulot uchun har xil bo'lgani sababli jamlash
        # Python'da bajariladi — DB'ga baribir bitta so'rov ketadi.
        movement_map = {}
        movement_field = {
            "s": "sold_out",
            "r": "returned",
            "to": "transfer_out",
            "ti": "transfer_in",
            "e": "entry",
        }
        for mv in (
            InventoryMovement.objects
            .filter(session=session)
            .values("product_id", "type", "quantity", "created_at")
        ):
            counted_row = counted_rows.get(mv["product_id"])
            if counted_row is None:
                continue  # sanalmagan mahsulot — pastda baribir chetlab o'tiladi
            counted_at = counted_row["counted_at"]
            if counted_at is not None and mv["created_at"] <= counted_at:
                continue  # sanoqda allaqachon aks etgan harakat
            field = movement_field.get(mv["type"])
            if field is None:
                continue
            bucket = movement_map.setdefault(
                mv["product_id"],
                {"sold_out": 0, "returned": 0, "transfer_out": 0, "transfer_in": 0, "entry": 0},
            )
            bucket[field] += mv["quantity"]

        counts_map = {
            product_id: row["counted_quantity"]
            for product_id, row in counted_rows.items()
        }

        # 🔹 snapshots map
        # ⚠️ MUAMMO [PERFORMANCE]: Snapshot map ham model instancelar orqali qurilmoqda.
        # Sabab: faqat ikkita ustun kerak bo'lsa ham barcha maydonlar select qilinadi.
        # Natija: mahsulotlar ko'p bo'lganda finalize jarayoni ortiqcha xotira ishlatadi.
        # ✅ YECHIM:
        # snapshots = dict(
        #     InventorySnapshot.objects.filter(session=session).values_list("product_id", "expected_quantity")
        # )
        snapshots = {
            s.product_id: s.expected_quantity
            for s in InventorySnapshot.objects.filter(session=session)
        }

        # Narxlar xaritasi — kamomad spisaniyesi uchun (tannarx/sotuv narxi).
        price_map = {
            pb["product_id"]: (pb["purchase_price"], pb["selling_price"])
            for pb in ProductBatch.objects
            .filter(store=session.store, product_id__in=list(snapshots.keys()))
            .values("product_id", "purchase_price", "selling_price")
        }

        # Kam chiqqan (topilmagan) tovarlar — finalize oxirida avtomatik spisaniye qilinadi.
        shortages = []

        # 🔥 SNAPSHOT BO‘YICHA YURAMIZ (MUHIM)
        for product_id, expected in snapshots.items():

            if product_id not in counts_map:
                # Sanalmagan mahsulot: qoldiq o'zgartirilmaydi va kamomad
                # yozilmaydi. "Sanalmagan" — "0 topildi" degani emas.
                continue

            counted = counts_map[product_id]

            data = movement_map.get(product_id, {})

            sold_out = data.get("sold_out", 0)
            returned = data.get("returned", 0)
            transfer_out = data.get("transfer_out", 0)
            transfer_in = data.get("transfer_in", 0)
            entry = data.get("entry", 0)

            final = (
                    counted
                    - sold_out
                    - transfer_out
                    + transfer_in
                    + entry
                    + returned
            )
            
            # ❗ NEGATIVE CHECK
            if final < 0:
                raise ValidationError(f"Negative stock: product_id={product_id}")

            diff = final - expected

            # Kamomad (kam chiqqan) — keyin avtomatik spisaniye uchun yig'amiz.
            if diff < 0:
                p_price, s_price = price_map.get(product_id, (0, 0))
                shortages.append({
                    "product_id": product_id,
                    "quantity": -diff,
                    "purchase_price": p_price,
                    "selling_price": s_price,
                })

            if diff != 0: # diff
                # ⚠️ MUAMMO [KRITIK/PERFORMANCE]: Loop ichida har product uchun alohida UPDATE bajariladi.
                # Sabab: o'zgaradigan batchlar oldindan yig'ilib `bulk_update` qilinmagan.
                # Natija: productlar soni ko'p bo'lsa transaction uzoq lock ushlab turadi.
                # ✅ YECHIM:
                # updated_batches.append(ProductBatch(id=batch_id, quantity=final))
                # ProductBatch.objects.bulk_update(updated_batches, ["quantity"])
                ProductBatch.objects.select_for_update().filter(
                    store=session.store,
                    product_id=product_id
                ).update(
                    quantity= final
                )

        # 🔻 KAMOMAD SPISANIYESI: inventarizatsiyada topilmagan tovarlar uchun
        # avtomatik audit yozuvi. Qoldiq yuqorida allaqachon to'g'rilangani sababli
        # bu yerda stock QAYTA kamaytirilmaydi (record-only).
        if shortages:
            from apps.writeoff.services import WriteOffService
            WriteOffService.record_inventory_shortage(
                session=session,
                shortages=shortages,
                user=session.started_by,
            )

        session.status = InventorySession.Status.COMPLETED
        session.save(update_fields=["status"])


    # 🔹 CANCEL

    @staticmethod
    @transaction.atomic
    def cancel(*, session_id):

        session = InventorySession.objects.select_for_update().get(id=session_id)

        if session.status != InventorySession.Status.ACTIVE:
            raise ValidationError("Cancel qilib bo‘lmaydi")

        session.status = InventorySession.Status.CANCELLED
        session.save(update_fields=["status"])

        InventoryMovement.objects.filter(session=session).delete()
        InventoryCount.objects.filter(session=session).delete()
        InventorySnapshot.objects.filter(session=session).delete()


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 3
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [finalize loopidagi ProductBatch update strategiyasini bulk updatega o'tkazish]
# ═══════════════════════════════
