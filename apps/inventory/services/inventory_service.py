from django.db import transaction
from django.db.models import Sum, F, Case, When, IntegerField
from django.db.models.functions import Coalesce
from rest_framework.exceptions import ValidationError

from apps.inventory.models import (
    InventorySession,
    InventorySnapshot,
    InventoryCount,
    InventoryMovement,
)
from apps.products.models import ProductBatch


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

        obj, _ = InventoryCount.objects.get_or_create(
            session=session,
            product_id=product_id,
        )

        # 🔥 ASOSIY O‘ZGARISH
        obj.counted_quantity = quantity
        obj.save(update_fields=["counted_quantity"])


    @staticmethod
    @transaction.atomic
    def set_count(*, session_id, product_id, quantity):

        session = InventorySession.objects.select_for_update().get(id=session_id)

        if session.status != "active":
            raise ValidationError("Session yopilgan")

        count = InventoryCount.objects.select_for_update().get(
            session=session,
            product_id=product_id
        )


        snapshot = InventorySnapshot.objects.get(
            session=session,
            product_id=product_id
        )

        # 🔥 update quantity
        count.counted_quantity = quantity

        # 🔥 status recalculation
        # if real == snapshot.expected_quantity:

        if quantity == snapshot.expected_quantity:
            count.status = InventoryCount.Status.EQUAL
        elif quantity < snapshot.expected_quantity:
            count.status = InventoryCount.Status.LESS
        else:
            count.status = InventoryCount.Status.MORE

        # tekshirilganlarga o'tkazish
        # ⚠️ MUAMMO [PERFORMANCE]: Bitta count uchun ikkita alohida `save()` chaqirilmoqda.
        # Sabab: `is_check`, `counted_quantity`, `status` bitta update_fields ichida yozilmagan.
        # Natija: har scan/update uchun ortiqcha UPDATE query ishlaydi.
        # ✅ YECHIM:
        # count.is_check = True
        # count.save(update_fields=["is_check", "counted_quantity", "status"])
        if not count.is_check:
            count.is_check = True
            count.save(update_fields=["is_check"])

        count.save(update_fields=["counted_quantity", "status"])


    # 🔹 FINALIZE

    @staticmethod
    @transaction.atomic
    def finalize(*, session_id):

        session = InventorySession.objects.select_for_update().get(id=session_id)

        if session.status != InventorySession.Status.ACTIVE:
            raise ValidationError("Session yopilgan")

        movements = (
            InventoryMovement.objects
            .filter(session=session)
            .values("product")
            .annotate(
                sold_out=Coalesce(Sum(
                    Case(When(type="s", then=F("quantity")), output_field=IntegerField())
                ), 0),

                returned=Coalesce(Sum(
                    Case(When(type="r", then=F("quantity")), output_field=IntegerField())
                ), 0),

                transfer_out=Coalesce(Sum(
                    Case(When(type="to", then=F("quantity")), output_field=IntegerField())
                ), 0),

                transfer_in=Coalesce(Sum(
                    Case(When(type="ti", then=F("quantity")), output_field=IntegerField())
                ), 0),

                entry=Coalesce(Sum(
                    Case(When(type="e", then=F("quantity")), output_field=IntegerField())
                ), 0),
            )
        )

        movement_map = {m["product"]: m for m in movements}

        # 🔹 counts map
        # ⚠️ MUAMMO [PERFORMANCE]: `InventoryCount.objects.filter(session=session)` barcha model ustunlarini olib keladi.
        # Sabab: map uchun faqat `product_id` va `counted_quantity` kerak, ammo model instancelar yaratilmoqda.
        # Natija: katta inventarizatsiyada xotira va CPU sarfi oshadi.
        # ✅ YECHIM:
        # counts_map = dict(
        #     InventoryCount.objects.filter(session=session).values_list("product_id", "counted_quantity")
        # )
        # OPTIMIZATION: `counts_map` va `snapshots` uchun alohida filterlar o'rniga bitta querysetdan
        # `values_list` yoki bitta annotate bilan birlashtirish DB round-trip sonini kamaytirishi mumkin.
        counts_map = {
            c.product_id: c.counted_quantity
            for c in InventoryCount.objects.filter(session=session)
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

        # 🔥 SNAPSHOT BO‘YICHA YURAMIZ (MUHIM)
        for product_id, expected in snapshots.items():

            counted = counts_map.get(product_id, 0)

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
