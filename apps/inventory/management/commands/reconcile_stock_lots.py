"""
Management command to reconcile ProductBatch quantities with StockLot remaining quantities.

Checks invariants and anomalies:
1. ProductBatch.quantity == SUM(StockLot.remaining_quantity)
2. Orphan lots (StockLots with no corresponding ProductBatch)
3. Negative lot balances (remaining_quantity < 0)
4. Duplicate OPENING_BALANCE lots for the same (store, product)

Guarantees:
- Read-only: does not modify stock data.

Usage:
    python manage.py reconcile_stock_lots
    python manage.py reconcile_stock_lots --store 1
    python manage.py reconcile_stock_lots --product 42
"""
from django.core.management.base import BaseCommand

from apps.inventory.services.stock_reconciliation_service import StockReconciliationService


class Command(BaseCommand):
    help = "Reconciles ProductBatch quantities against authoritative StockLot quantities (read-only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--store",
            type=int,
            default=None,
            help="Filter reconciliation to a specific store ID.",
        )
        parser.add_argument(
            "--product",
            type=int,
            default=None,
            help="Filter reconciliation to a specific product ID.",
        )

    def handle(self, *args, **options):
        store_id = options.get("store")
        product_id = options.get("product")

        self.stdout.write(self.style.MIGRATE_HEADING("Starting Stock Reconciliation..."))

        report = StockReconciliationService.reconcile_all(
            store_id=store_id,
            product_id=product_id,
        )

        self.stdout.write(report.format_summary())

        if report.issues:
            self.stdout.write(self.style.ERROR(f"\nFound {len(report.issues)} issue(s):"))
            for idx, issue in enumerate(report.issues, start=1):
                self.stdout.write(
                    self.style.WARNING(f"  [{idx}] [{issue['issue_type']}] {issue.get('message', '')}")
                )
        else:
            self.stdout.write(
                self.style.SUCCESS("\n[OK] Invariant verified: All ProductBatch rows match StockLot balances.")
            )
