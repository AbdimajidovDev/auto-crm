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
                    Case(When(type="sale", then=F("quantity")), output_field=IntegerField())
                ), 0),

                returned=Coalesce(Sum(
                    Case(When(type="return", then=F("quantity")), output_field=IntegerField())
                ), 0),

                transfer_out=Coalesce(Sum(
                    Case(When(type="transfer_out", then=F("quantity")), output_field=IntegerField())
                ), 0),

                transfer_in=Coalesce(Sum(
                    Case(When(type="transfer_in", then=F("quantity")), output_field=IntegerField())
                ), 0),

                entry=Coalesce(Sum(
                    Case(When(type="entry", then=F("quantity")), output_field=IntegerField())
                ), 0),
            )
        )

        movement_map = {m["product"]: m for m in movements}

        # 🔹 counts map
        counts_map = {
            c.product_id: c.counted_quantity
            for c in InventoryCount.objects.filter(session=session)
        }

        # 🔹 snapshots map
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
