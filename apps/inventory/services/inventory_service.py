from django.db import transaction
from django.db.models import Sum, F
from django.core.exceptions import ValidationError

from apps.inventory.models import (
    InventorySession,
    InventorySnapshot,
    InventoryCount,
    InventoryMovement,
    InventoryAdjustment
)
from apps.products.models import ProductBatch


class InventoryService:

    # 🔹 START SESSION
    @staticmethod
    @transaction.atomic
    def start_session(*, user, store_id):

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

        InventorySnapshot.objects.bulk_create(snapshots)
        InventoryCount.objects.bulk_create(counts)

        return session

    # @staticmethod
    # @transaction.atomic
    # def start_session(*, user, store_id):
    #
    #     # ❗ CHECK: faqat bitta active session
    #     if InventorySession.objects.filter(
    #         store_id=store_id,
    #         status=InventorySession.Status.ACTIVE
    #     ).exists():
    #         raise ValidationError("Active session mavjud")
    #
    #     session = InventorySession.objects.create(
    #         store_id=store_id,
    #         started_by=user
    #     )
    #
    #     # 🔥 SNAPSHOT (OPTIMIZED)
    #     batches = (
    #         ProductBatch.objects
    #         .filter(store_id=store_id)
    #         .values("product_id")
    #         .annotate(total=Sum("quantity"))
    #     )
    #
    #     InventorySnapshot.objects.bulk_create([
    #         InventorySnapshot(
    #             session=session,
    #             product_id=b["product_id"],
    #             store_id=store_id,
    #             expected_quantity=b["total"]
    #         )
    #         for b in batches
    #     ])
    #
    #     session.snapshot_taken = True
    #     session.save(update_fields=["snapshot_taken"])
    #
    #     return session

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
        if quantity == snapshot.expected_quantity:
            count.status = InventoryCount.Status.EQUAL
        elif quantity < snapshot.expected_quantity:
            count.status = InventoryCount.Status.LESS
        else:
            count.status = InventoryCount.Status.MORE

        # tekshirilganlarga o'tkazish
        if not count.is_check:
            count.is_check = True
            count.save(update_fields=["is_check"])

        count.save(update_fields=["counted_quantity", "status"])


    # @staticmethod
    # @transaction.atomic
    # def scan_product(*, session_id, product_id, quantity):
    #
    #     session = InventorySession.objects.select_for_update().get(id=session_id)
    #
    #     if session.status != InventorySession.Status.ACTIVE:
    #         raise ValidationError("Session yopilgan")
    #
    #     obj, _ = InventoryCount.objects.get_or_create(
    #         session=session,
    #         product_id=product_id,
    #         defaults={"counted_quantity": 0}
    #     )
    #
    #     obj.counted_quantity = F("counted_quantity") + quantity
    #     obj.save(update_fields=["counted_quantity"])

    # 🔹 FINALIZE
    @staticmethod
    @transaction.atomic
    def finalize(*, session_id):

        session = InventorySession.objects.select_for_update().get(id=session_id)

        if session.status != "active":
            raise ValidationError("Session yopilgan")

        movements = (
            InventoryMovement.objects
            .filter(session=session)
            .values("product")
            .annotate(total=Sum("quantity"))
        )

        movement_map = {m["product"]: m["total"] for m in movements}

        counts = InventoryCount.objects.select_related("product").filter(session=session)

        for count in counts:

            moved = movement_map.get(count.product_id, 0)

            final = count.counted_quantity - moved

            snapshot = InventorySnapshot.objects.get(
                session=session,
                product=count.product
            )

            diff = final - snapshot.expected_quantity

            if diff != 0:
                ProductBatch.objects.filter(
                    store=session.store,
                    product=count.product
                ).update(
                    quantity=F("quantity") + diff
                )

        session.status = "completed"
        session.save(update_fields=["status"])


    # @staticmethod
    # @transaction.atomic
    # def finalize(*, session_id):
    #
    #     session = InventorySession.objects.select_for_update().get(id=session_id)
    #
    #     if session.status != InventorySession.Status.ACTIVE:
    #         raise ValidationError("Session yopilgan")
    #
    #     snapshots = InventorySnapshot.objects.filter(session=session)
    #
    #     movements = (
    #         InventoryMovement.objects
    #         .filter(session=session)
    #         .values("product")
    #         .annotate(total=Sum("quantity"))
    #     )
    #     movement_map = {m["product"]: m["total"] for m in movements}
    #
    #     counts = {
    #         c.product_id: c.counted_quantity
    #         for c in InventoryCount.objects.filter(session=session)
    #     }
    #
    #     adjustments = []
    #
    #     for snap in snapshots:
    #
    #         counted = counts.get(snap.product_id, 0)
    #         moved = movement_map.get(snap.product_id, 0)
    #
    #         final_stock = counted - moved
    #         diff = final_stock - snap.expected_quantity
    #
    #         if diff != 0:
    #             adjustments.append(
    #                 InventoryAdjustment(
    #                     session=session,
    #                     product=snap.product,
    #                     difference=diff
    #                 )
    #             )
    #
    #     InventoryAdjustment.objects.bulk_create(adjustments)
    #
    #     # 🔥 APPLY STOCK (BULK)
    #     for adj in adjustments:
    #         ProductBatch.objects.filter(
    #             store=session.store,
    #             product=adj.product
    #         ).update(
    #             quantity=F("quantity") + adj.difference
    #         )
    #
    #     session.status = InventorySession.Status.COMPLETED
    #     session.save(update_fields=["status"])
    #
    #     return session

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