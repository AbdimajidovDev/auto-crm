from django.db import IntegrityError, transaction
from django.db.models import Max, Sum
from django.utils import timezone

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.inventory.models import LowStockItem
from apps.products.models import ProductBatch
from apps.store.models import Store, StoreUser
from apps.transfer.models import Notification


class LowStockService:
    """
    Detects products that reach their minimum stock threshold and maintains
    OPEN/RESOLVED LowStockItem records + one-time notifications.

    Performance notes (N+1 prevention):
      * `evaluate_batch` is the workhorse. It NEVER queries ProductBatch or
        LowStockItem inside a loop:
          - stock quantities + thresholds are fetched in ONE aggregate query,
          - existing OPEN records are fetched in ONE query and mapped by product,
          - new records use bulk_create(), resolutions use bulk_update(),
          - notification recipients use ONE values_list() query,
          - notification rows use bulk_create().
      * `evaluate(store, product)` is the documented single-item entry point and
        simply delegates to `evaluate_batch` with a one-element list.
      * `schedule_evaluation` registers the evaluation on transaction.on_commit
        so it runs only after the stock mutation has durably committed.
    """

    # =====================================================================
    # PUBLIC ENTRY POINTS
    # =====================================================================

    @staticmethod
    def schedule_evaluation(store, product_ids):
        """
        Register a low-stock evaluation to run AFTER the current transaction
        commits. Safe to call from inside any stock-mutation transaction.
        """
        ids = LowStockService._normalize_ids(product_ids)
        if not ids:
            return

        store_id = store.id if isinstance(store, Store) else store

        # on_commit guarantees we read committed stock and never block the
        # stock-mutation transaction with notification work.
        transaction.on_commit(
            lambda: LowStockService.evaluate_batch(store=store_id, product_ids=ids)
        )

    @staticmethod
    def evaluate(store, product):
        """Documented single-product entry point (spec: LowStockService.evaluate)."""
        product_id = product.id if hasattr(product, "id") else product
        return LowStockService.evaluate_batch(store=store, product_ids=[product_id])

    # =====================================================================
    # CORE
    # =====================================================================

    @staticmethod
    def evaluate_batch(*, store, product_ids):
        """
        Evaluate many products for a single store in a bounded number of queries.
        Creates OPEN records for products at/below threshold and resolves OPEN
        records for products that recovered. Notifications are dispatched once,
        only for records actually created.
        """
        ids = LowStockService._normalize_ids(product_ids)
        if not ids:
            return []

        store_obj = store if isinstance(store, Store) else Store.objects.get(id=store)

        with transaction.atomic():
            # 1 query: current stock + threshold per product (aggregated across batches).
            stock_rows = (
                ProductBatch.objects
                .filter(store=store_obj, product_id__in=ids)
                .values("product_id")
                .annotate(qty=Sum("quantity"), threshold=Max("min_stock"))
            )
            stock_map = {row["product_id"]: row for row in stock_rows}

            # 1 query: existing OPEN records mapped by product.
            open_map = {
                item.product_id: item
                for item in LowStockItem.objects.filter(
                    store=store_obj,
                    product_id__in=ids,
                    status=LowStockItem.Status.OPEN,
                )
            }

            action_type = LowStockService._action_type_for(store_obj)

            to_create = []
            to_resolve = []

            for product_id in ids:
                row = stock_map.get(product_id)
                if row is None:
                    # No batch for this product/store -> nothing to monitor.
                    continue

                threshold = row["threshold"] or 0
                quantity = row["qty"] or 0

                if threshold == 0:
                    # Monitoring disabled for this pair -> do nothing.
                    continue

                existing = open_map.get(product_id)

                if quantity <= threshold:
                    if existing is None:
                        to_create.append(
                            LowStockItem(
                                store=store_obj,
                                product_id=product_id,
                                current_quantity=quantity,
                                min_stock=threshold,
                                action_type=action_type,
                                status=LowStockItem.Status.OPEN,
                            )
                        )
                else:
                    if existing is not None:
                        existing.status = LowStockItem.Status.RESOLVED
                        existing.resolved_at = timezone.now()
                        to_resolve.append(existing)

            created = LowStockService._persist_created(to_create)

            if to_resolve:
                LowStockItem.objects.bulk_update(
                    to_resolve, ["status", "resolved_at", "updated_at"]
                )

            # Dispatch notifications once, only for freshly created OPEN records.
            if created:
                LowStockService._dispatch_notifications(store_obj, action_type, created)

        return created

    # =====================================================================
    # PERSISTENCE HELPERS
    # =====================================================================

    @staticmethod
    def _persist_created(to_create):
        """
        bulk_create the new OPEN records. The partial unique constraint is the
        source of truth against concurrent duplicates; on conflict we fall back
        to per-row get_or_create so a concurrently-created record is reused
        (and NOT re-notified).
        """
        if not to_create:
            return []

        try:
            with transaction.atomic():
                return LowStockItem.objects.bulk_create(to_create)
        except IntegrityError:
            created = []
            for obj in to_create:
                item, was_created = LowStockItem.objects.get_or_create(
                    store_id=obj.store_id,
                    product_id=obj.product_id,
                    status=LowStockItem.Status.OPEN,
                    defaults={
                        "current_quantity": obj.current_quantity,
                        "min_stock": obj.min_stock,
                        "action_type": obj.action_type,
                    },
                )
                if was_created:
                    created.append(item)
            return created

    # =====================================================================
    # NOTIFICATIONS
    # =====================================================================

    @staticmethod
    def _dispatch_notifications(store, action_type, created_items):
        """
        Create one Notification per (store user, low-stock item) and, for BASE
        stores only, push a realtime websocket message.

        Runs after the LowStockItem rows are committed (on_commit) so consumers
        never see a notification that points at a non-existent record.
        """
        is_base = store.type == Store.StoreType.BASE
        notif_type = (
            Notification.Type.LOW_STOCK_PURCHASE
            if is_base
            else Notification.Type.LOW_STOCK_TRANSFER
        )

        # 1 query: recipients (only the affected store's active users).
        user_ids = list(
            StoreUser.objects.filter(store=store, is_active=True)
            .values_list("user_id", flat=True)
        )
        if not user_ids:
            return

        # Pre-build messages once per item (avoids per-user attribute work).
        item_payloads = [
            {
                "id": item.id,
                "product_id": item.product_id,
                "title": "Mahsulot tugayapti" if is_base else "Mahsulotni to'ldirish kerak",
                "message": (
                    f"#{item.product_id} mahsulot zaxirasi {item.current_quantity} "
                    f"<= {item.min_stock}. "
                    + ("Yetkazib beruvchidan xarid qiling." if is_base else "Bazadan transfer qiling.")
                ),
            }
            for item in created_items
        ]

        def _send():
            notifications = [
                Notification(
                    user_id=user_id,
                    type=notif_type,
                    title=payload["title"],
                    message=payload["message"],
                )
                for payload in item_payloads
                for user_id in user_ids
            ]
            Notification.objects.bulk_create(notifications)

            # STORE: notification record only, NO realtime websocket.
            if not is_base:
                return

            channel_layer = get_channel_layer()
            if channel_layer is None:
                return

            for payload in item_payloads:
                for user_id in user_ids:
                    async_to_sync(channel_layer.group_send)(
                        f"user_{user_id}",
                        {
                            "type": "notify",
                            "data": {
                                "type": notif_type,
                                "title": payload["title"],
                                "message": payload["message"],
                                "low_stock_item_id": payload["id"],
                                "product_id": payload["product_id"],
                            },
                        },
                    )

        transaction.on_commit(_send)

    # =====================================================================
    # UTILITIES
    # =====================================================================

    @staticmethod
    def _action_type_for(store):
        if store.type == Store.StoreType.BASE:
            return LowStockItem.ActionType.PURCHASE
        return LowStockItem.ActionType.TRANSFER

    @staticmethod
    def _normalize_ids(product_ids):
        if not product_ids:
            return []
        # De-duplicate while preserving determinism.
        seen = set()
        ordered = []
        for pid in product_ids:
            pid = pid.id if hasattr(pid, "id") else pid
            if pid not in seen:
                seen.add(pid)
                ordered.append(pid)
        return ordered
