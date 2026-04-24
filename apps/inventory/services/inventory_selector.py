from django.shortcuts import get_object_or_404
from apps.inventory.models import InventorySession


from django.db.models import Sum, OuterRef, Subquery, IntegerField, BooleanField, CharField, Value, When, Case, F
from django.db.models.functions import Coalesce

from apps.inventory.models import (
    InventorySnapshot,
    InventoryCount,
    InventoryMovement
)



class InventorySelector:

    @staticmethod
    def get_active_session(store_id):
        return InventorySession.objects.filter(
            store_id=store_id,
            status=InventorySession.Status.ACTIVE
        ).first()

    @staticmethod
    def get_session(pk):
        return get_object_or_404(InventorySession, pk=pk)



    @staticmethod
    def get_inventory_list(session_id, statuses=None):

        counts_subquery = (
            InventoryCount.objects
            .filter(session_id=session_id, product=OuterRef("product"))
            .values("product")
            .annotate(total=Sum("counted_quantity"))
            .values("total")[:1]
        )

        is_check_subquery = (
            InventoryCount.objects
            .filter(session_id=session_id, product=OuterRef("product"))
            .values("is_check")[:1]
        )

        status_subquery = (
            InventoryCount.objects
            .filter(session_id=session_id, product=OuterRef("product"))
            .values("status")[:1]
        )

        movement_subquery = (
            InventoryMovement.objects
            .filter(session_id=session_id, product=OuterRef("product"))
            .values("product")
            .annotate(
                total=Sum(
                    Case(
                        When(type="sale", then=F("quantity")),
                        When(type="transfer_out", then=F("quantity")),
                        When(type="return", then=-F("quantity")),  # 🔥 ENG MUHIM
                        output_field=IntegerField()
                    )
                )
            )
            .values("total")[:1]
        )


        qs = (
            InventorySnapshot.objects
            .filter(session_id=session_id)
            .select_related("product")
            .annotate(
                counted=Coalesce(Subquery(counts_subquery, output_field=IntegerField()), 0),
                moved=Coalesce(Subquery(movement_subquery, output_field=IntegerField()), 0),
                is_check=Coalesce(Subquery(is_check_subquery, output_field=BooleanField()), False),
                status=Coalesce(
                    Subquery(status_subquery, output_field=CharField()),
                    Value("p")  # 🔥 FIX
                ),
            )
        )

        # 🔥 STATUS FILTER
        if statuses:
            qs = qs.filter(status__in=statuses)

        return qs
