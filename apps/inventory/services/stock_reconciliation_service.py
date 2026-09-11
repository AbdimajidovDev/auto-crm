from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.db import models

from apps.inventory.models import StockLot
from apps.products.models import ProductBatch


@dataclass
class StockReconciliationReport:
    """Read-only report summarizing stock integrity between ProductBatch and StockLot."""

    is_valid: bool
    total_checked: int
    matched_count: int
    mismatched_count: int
    quantity_mismatches: list[dict[str, Any]] = field(default_factory=list)
    orphan_lots: list[dict[str, Any]] = field(default_factory=list)
    negative_lot_balances: list[dict[str, Any]] = field(default_factory=list)
    duplicate_opening_lots: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)

    def format_summary(self) -> str:
        status_str = "VALID (All Invariants Hold)" if self.is_valid else "INVALID (Issues Detected)"
        lines = [
            "=" * 60,
            f"STOCK RECONCILIATION SUMMARY: {status_str}",
            "=" * 60,
            f"Total Batches Checked     : {self.total_checked}",
            f"Batches Fully Matched     : {self.matched_count}",
            f"Batches Mismatched        : {self.mismatched_count}",
            f"Quantity Mismatches       : {len(self.quantity_mismatches)}",
            f"Orphan Lots Found         : {len(self.orphan_lots)}",
            f"Negative Lot Balances     : {len(self.negative_lot_balances)}",
            f"Duplicate Opening Lots    : {len(self.duplicate_opening_lots)}",
            f"Total Issues Detected     : {len(self.issues)}",
            "=" * 60,
        ]
        return "\n".join(lines)


class StockReconciliationService:
    """
    Service for validating stock integrity and invariants between ProductBatch and StockLot.

    Guarantees:
    - Pure read-only operation: NEVER modifies any database records.
    - Authoritative invariant check: ProductBatch.quantity == SUM(StockLot.remaining_quantity).
    - Anomaly detection:
      1. Quantity mismatches between ProductBatch and total remaining lots.
      2. Orphan StockLots lacking a ProductBatch in the database.
      3. Negative lot balances (remaining_quantity < 0).
      4. Duplicate OPENING_BALANCE lots for the same (store, product).
    """

    @classmethod
    def check_quantity_mismatches(
        cls,
        store_id: int | None = None,
        product_id: int | None = None,
    ) -> tuple[list[dict[str, Any]], int, int]:
        """
        Compares ProductBatch.quantity against SUM(StockLot.remaining_quantity).
        Returns (mismatches, matched_count, total_checked).
        """
        qs = ProductBatch.objects.select_related("store", "product").all()
        if store_id:
            qs = qs.filter(store_id=store_id)
        if product_id:
            qs = qs.filter(product_id=product_id)

        mismatches: list[dict[str, Any]] = []
        matched_count = 0
        total_checked = 0

        for batch in qs.order_by("store_id", "product_id"):
            total_checked += 1
            lot_sum = (
                StockLot.objects.filter(
                    store_id=batch.store_id,
                    product_id=batch.product_id,
                ).aggregate(total=models.Sum("remaining_quantity"))["total"]
                or Decimal("0.00")
            )

            # Invariant: for positive batch quantity, lot_sum must match batch.quantity.
            # For zero or negative batch quantity, lot_sum must be 0.
            expected_lot_qty = batch.quantity if batch.quantity > Decimal("0.00") else Decimal("0.00")

            if lot_sum != expected_lot_qty:
                mismatches.append({
                    "issue_type": "quantity_mismatch",
                    "store_id": batch.store_id,
                    "store_name": batch.store.name if batch.store else str(batch.store_id),
                    "product_id": batch.product_id,
                    "product_name": batch.product.name if batch.product else str(batch.product_id),
                    "batch_quantity": batch.quantity,
                    "lot_quantity": lot_sum,
                    "expected_lot_quantity": expected_lot_qty,
                    "difference": batch.quantity - lot_sum,
                    "message": (
                        f"Quantity mismatch in store #{batch.store_id} for product #{batch.product_id}: "
                        f"ProductBatch={batch.quantity}, Lots={lot_sum}, Diff={batch.quantity - lot_sum}."
                    ),
                })
            else:
                matched_count += 1

        return mismatches, matched_count, total_checked

    @classmethod
    def check_orphan_lots(
        cls,
        store_id: int | None = None,
        product_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Detects any StockLots that do not have an active ProductBatch entry."""
        batch_pairs = set(ProductBatch.objects.values_list("store_id", "product_id"))

        lot_qs = StockLot.objects.all()
        if store_id:
            lot_qs = lot_qs.filter(store_id=store_id)
        if product_id:
            lot_qs = lot_qs.filter(product_id=product_id)

        orphan_lots: list[dict[str, Any]] = []
        for lot in lot_qs.order_by("id"):
            if (lot.store_id, lot.product_id) not in batch_pairs:
                orphan_lots.append({
                    "issue_type": "orphan_lot",
                    "lot_id": lot.id,
                    "store_id": lot.store_id,
                    "product_id": lot.product_id,
                    "lot_type": lot.lot_type,
                    "remaining_quantity": lot.remaining_quantity,
                    "message": f"StockLot #{lot.id} exists for store #{lot.store_id}, product #{lot.product_id} with no ProductBatch.",
                })
        return orphan_lots

    @classmethod
    def check_negative_lot_balances(
        cls,
        store_id: int | None = None,
        product_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Detects any StockLots with negative remaining_quantity."""
        neg_qs = StockLot.objects.filter(remaining_quantity__lt=Decimal("0.00"))
        if store_id:
            neg_qs = neg_qs.filter(store_id=store_id)
        if product_id:
            neg_qs = neg_qs.filter(product_id=product_id)

        negative_lots: list[dict[str, Any]] = []
        for lot in neg_qs.order_by("id"):
            negative_lots.append({
                "issue_type": "negative_lot_balance",
                "lot_id": lot.id,
                "store_id": lot.store_id,
                "product_id": lot.product_id,
                "remaining_quantity": lot.remaining_quantity,
                "message": f"StockLot #{lot.id} has invalid negative remaining_quantity: {lot.remaining_quantity}.",
            })
        return negative_lots

    @classmethod
    def check_duplicate_opening_lots(
        cls,
        store_id: int | None = None,
        product_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Detects multiple OPENING_BALANCE lots for the same (store, product)."""
        qs = StockLot.objects.filter(lot_type=StockLot.LotType.OPENING_BALANCE)
        if store_id:
            qs = qs.filter(store_id=store_id)
        if product_id:
            qs = qs.filter(product_id=product_id)

        duplicates = (
            qs.values("store_id", "product_id")
            .annotate(cnt=models.Count("id"))
            .filter(cnt__gt=1)
        )

        dup_issues: list[dict[str, Any]] = []
        for d in duplicates:
            lot_ids = list(
                StockLot.objects.filter(
                    store_id=d["store_id"],
                    product_id=d["product_id"],
                    lot_type=StockLot.LotType.OPENING_BALANCE,
                ).values_list("id", flat=True)
            )
            dup_issues.append({
                "issue_type": "duplicate_opening_lot",
                "store_id": d["store_id"],
                "product_id": d["product_id"],
                "count": d["cnt"],
                "lot_ids": lot_ids,
                "message": (
                    f"Duplicate OPENING_BALANCE lots detected for store #{d['store_id']}, "
                    f"product #{d['product_id']}: found {d['cnt']} lots ({lot_ids})."
                ),
            })
        return dup_issues

    @classmethod
    def reconcile_all(
        cls,
        store_id: int | None = None,
        product_id: int | None = None,
    ) -> StockReconciliationReport:
        """
        Executes a full reconciliation sweep across ProductBatch and StockLot.
        Read-only: does not modify any database records.
        """
        qty_mismatches, matched_count, total_checked = cls.check_quantity_mismatches(
            store_id=store_id, product_id=product_id
        )
        orphan_lots = cls.check_orphan_lots(store_id=store_id, product_id=product_id)
        negative_lots = cls.check_negative_lot_balances(store_id=store_id, product_id=product_id)
        duplicate_opening = cls.check_duplicate_opening_lots(store_id=store_id, product_id=product_id)

        all_issues = qty_mismatches + orphan_lots + negative_lots + duplicate_opening
        is_valid = len(all_issues) == 0

        return StockReconciliationReport(
            is_valid=is_valid,
            total_checked=total_checked,
            matched_count=matched_count,
            mismatched_count=len(qty_mismatches),
            quantity_mismatches=qty_mismatches,
            orphan_lots=orphan_lots,
            negative_lot_balances=negative_lots,
            duplicate_opening_lots=duplicate_opening,
            issues=all_issues,
        )
