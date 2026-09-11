"""
Management command to migrate legacy ProductBatch stock balances into authoritative StockLots.

Cut-off Strategy:
1. Reads existing ProductBatch rows.
2. For every (store, product) with quantity > 0:
   creates one StockLot with:
     - lot_type = OPENING_BALANCE
     - store = ProductBatch.store
     - product = ProductBatch.product
     - supplier = None (historical supplier attribution remains unknown)
     - source_lot = None
     - stock_entry_item = None
     - initial_quantity = ProductBatch.quantity
     - remaining_quantity = ProductBatch.quantity
     - purchase_price = ProductBatch.purchase_price
     - created_at = cutoff timestamp
3. Does NOT create lots for quantity <= 0.
4. Does NOT create historical StockAllocation records.
5. Idempotent: safe to run multiple times without duplicating opening lots.
6. Supports --dry-run to simulate and audit before committing changes.
7. Concurrency-safe: deterministic ordering and row-level locking.
8. Reconciles ProductBatch.quantity == SUM(StockLot.remaining_quantity).

Usage:
    python manage.py migrate_legacy_stock_to_lots --dry-run
    python manage.py migrate_legacy_stock_to_lots --cutoff "2026-09-11 00:00:00"
    python manage.py migrate_legacy_stock_to_lots --cutoff "2026-09-11 00:00:00" --store 1
"""
from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction
from django.utils import dateparse, timezone

from apps.inventory.models import StockLot
from apps.inventory.services.stock_reconciliation_service import StockReconciliationService
from apps.products.models import ProductBatch


def parse_cutoff_timestamp(cutoff_str: str | None) -> datetime:
    """Parses a cutoff date/time string into a timezone-aware datetime."""
    if not cutoff_str:
        return timezone.now()

    dt = dateparse.parse_datetime(cutoff_str)
    if dt is None:
        try:
            dt = datetime.strptime(cutoff_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                dt = datetime.strptime(cutoff_str, "%Y-%m-%d")
            except ValueError as err:
                raise CommandError(
                    f"Invalid --cutoff format '{cutoff_str}'. Expected 'YYYY-MM-DD HH:MM:SS' or ISO format."
                ) from err

    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


class Command(BaseCommand):
    help = "Migrates legacy ProductBatch balances into StockLots at a specified cut-off timestamp."

    def add_arguments(self, parser):
        parser.add_argument(
            "--cutoff",
            type=str,
            default=None,
            help="Cut-off timestamp, e.g. 'YYYY-MM-DD HH:MM:SS' or ISO. Defaults to current timestamp.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate migration without modifying the database.",
        )
        parser.add_argument(
            "--store",
            type=int,
            default=None,
            help="Optional store ID to restrict migration to a specific store.",
        )

    def handle(self, *args, **options):
        cutoff_str = options.get("cutoff")
        dry_run = options.get("dry_run", False)
        store_id = options.get("store")

        cutoff_dt = parse_cutoff_timestamp(cutoff_str)

        self.stdout.write(self.style.MIGRATE_HEADING("=" * 65))
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"LEGACY STOCK → STOCKLOT CUT-OFF MIGRATION {'[DRY-RUN]' if dry_run else ''}"
            )
        )
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 65))
        self.stdout.write(f"Cut-off Timestamp : {cutoff_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        if store_id:
            self.stdout.write(f"Store Scope       : #{store_id}")
        self.stdout.write("-" * 65)

        # 1. Query existing ProductBatch rows
        batches_qs = ProductBatch.objects.select_related("store", "product")
        if store_id:
            batches_qs = batches_qs.filter(store_id=store_id)
        batches = list(batches_qs.order_by("store_id", "product_id"))

        total_batches = len(batches)
        eligible_batches = [b for b in batches if b.quantity > Decimal("0.00")]
        ineligible_batches = [b for b in batches if b.quantity <= Decimal("0.00")]
        total_eligible_qty = (
            sum((b.quantity for b in eligible_batches), Decimal("0.00"))
        )

        # 2. Existing opening lots count
        existing_opening_qs = StockLot.objects.filter(lot_type=StockLot.LotType.OPENING_BALANCE)
        if store_id:
            existing_opening_qs = existing_opening_qs.filter(store_id=store_id)
        existing_opening_count = existing_opening_qs.count()

        # 3. Check pre-migration reconciliation anomalies
        pre_recon = StockReconciliationService.reconcile_all(store_id=store_id)

        # 4. Determine what would be created
        would_create_batches: list[tuple[ProductBatch, Decimal]] = []
        already_migrated_count = 0
        already_covered_count = 0

        for b in eligible_batches:
            has_opening = StockLot.objects.filter(
                store_id=b.store_id,
                product_id=b.product_id,
                lot_type=StockLot.LotType.OPENING_BALANCE,
            ).exists()
            if has_opening:
                already_migrated_count += 1
                continue

            lot_sum = (
                StockLot.objects.filter(
                    store_id=b.store_id,
                    product_id=b.product_id,
                ).aggregate(total=models.Sum("remaining_quantity"))["total"]
                or Decimal("0.00")
            )

            if lot_sum >= b.quantity:
                already_covered_count += 1
                continue

            needed_qty = b.quantity - lot_sum
            if needed_qty > Decimal("0.00"):
                would_create_batches.append((b, needed_qty))

        would_create_count = len(would_create_batches)

        # Report audit figures
        self.stdout.write(f"Total ProductBatch rows          : {total_batches}")
        self.stdout.write(f"Eligible for migration (qty > 0) : {len(eligible_batches)}")
        self.stdout.write(f"Ineligible (qty <= 0)            : {len(ineligible_batches)}")
        self.stdout.write(f"Total eligible quantity          : {total_eligible_qty}")
        self.stdout.write(f"Existing OPENING_BALANCE lots    : {existing_opening_count}")
        self.stdout.write(f"Skipped (already migrated)       : {already_migrated_count}")
        self.stdout.write(f"Skipped (already lot-covered)    : {already_covered_count}")
        self.stdout.write(f"Lots that would be created       : {would_create_count}")
        self.stdout.write("-" * 65)

        if pre_recon.issues:
            self.stdout.write(
                self.style.WARNING(f"Pre-migration anomalies detected: {len(pre_recon.issues)}")
            )
            for issue in pre_recon.issues[:5]:
                self.stdout.write(f"  - [{issue['issue_type']}] {issue.get('message', '')}")
            if len(pre_recon.issues) > 5:
                self.stdout.write(f"  ... and {len(pre_recon.issues) - 5} more issues.")
        else:
            self.stdout.write("Pre-migration anomalies          : None")

        # Handle Dry-Run
        if dry_run:
            self.stdout.write(self.style.MIGRATE_HEADING("=" * 65))
            self.stdout.write(
                self.style.SUCCESS("DRY RUN COMPLETE — No database modifications made.")
            )
            self.stdout.write(self.style.MIGRATE_HEADING("=" * 65))
            return

        # 5. Execute Migration inside atomic transaction
        created_count = 0
        with transaction.atomic():
            for b, qty_to_create in would_create_batches:
                # Deterministic lock on this ProductBatch row
                locked_batch = ProductBatch.objects.select_for_update().get(pk=b.pk)

                # Re-verify idempotency under lock
                if StockLot.objects.filter(
                    store_id=locked_batch.store_id,
                    product_id=locked_batch.product_id,
                    lot_type=StockLot.LotType.OPENING_BALANCE,
                ).exists():
                    continue

                lot_sum = (
                    StockLot.objects.filter(
                        store_id=locked_batch.store_id,
                        product_id=locked_batch.product_id,
                    ).aggregate(total=models.Sum("remaining_quantity"))["total"]
                    or Decimal("0.00")
                )
                if lot_sum >= locked_batch.quantity:
                    continue

                actual_qty = locked_batch.quantity - lot_sum
                if actual_qty <= Decimal("0.00"):
                    continue

                lot = StockLot.objects.create(
                    store=locked_batch.store,
                    product=locked_batch.product,
                    supplier=None,
                    lot_type=StockLot.LotType.OPENING_BALANCE,
                    source_lot=None,
                    stock_entry_item=None,
                    initial_quantity=actual_qty,
                    remaining_quantity=actual_qty,
                    purchase_price=locked_batch.purchase_price or Decimal("0.00"),
                )
                # Ensure created_at strictly reflects cut-off timestamp (bypassing auto_now_add)
                StockLot.objects.filter(pk=lot.pk).update(created_at=cutoff_dt)
                created_count += 1

        self.stdout.write("-" * 65)
        self.stdout.write(self.style.SUCCESS(f"Successfully created {created_count} StockLot(s)."))

        # 6. Post-migration automated reconciliation check
        post_recon = StockReconciliationService.reconcile_all(store_id=store_id)
        if post_recon.is_valid:
            self.stdout.write(
                self.style.SUCCESS(
                    "[RECONCILIATION PASSED] Invariant verified: "
                    "ProductBatch.quantity == SUM(StockLot.remaining_quantity) across all batches."
                )
            )
        else:
            self.stdout.write(
                self.style.ERROR(
                    f"[RECONCILIATION WARNING] Detected {len(post_recon.issues)} post-migration issue(s)!"
                )
            )
            for issue in post_recon.issues[:5]:
                self.stdout.write(self.style.ERROR(f"  - [{issue['issue_type']}] {issue.get('message', '')}"))

        self.stdout.write(self.style.MIGRATE_HEADING("=" * 65))
        self.stdout.write(self.style.SUCCESS("MIGRATION COMPLETED SUCCESSFULLY."))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 65))
