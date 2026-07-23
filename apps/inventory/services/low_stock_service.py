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
    # LIVE COMPUTATION (ro'yxat/eksport uchun — yozuvlarga bog'lanmaydi)
    # =====================================================================

    @staticmethod
    def compute_live(*, store_id=None, action_type=None, search=None):
        """
        Joriy kam-qoldiq ro'yxati — LowStockItem yozuvlariga bog'lanmasdan,
        ProductBatch qoldiqlaridan TO'G'RIDAN-TO'G'RI hisoblanadi (shu sabab
        ro'yxat hech qachon "bo'sh/eski" bo'lib qolmaydi):

          * min_stock > 0 bo'lsa — qoldiq <= min_stock bo'lganda kam qolgan;
          * min_stock KIRITILMAGAN bo'lsa ham — qoldiq TUGAGAN (<= 0) mahsulot
            ro'yxatga tushadi (aks holda min_stock to'ldirilmagan bazalarda
            ro'yxat doim bo'sh chiqardi);
          * mahsulot BOSHQA faol do'kon/bazada bor bo'lsa -> TRANSFER kerak
            (sources: qayerda qancha borligi bilan, ko'pdan ozga saralangan);
          * hech qayerda bo'lmasa -> XARID kerak (yetkazib beruvchidan olish).

        Bitta aggregate SQL + Python guruhlash. store filtri sources
        hisoblangandan KEYIN qo'llanadi — boshqa do'konlardagi zaxira
        ma'lumoti yo'qolmasligi uchun.
        """
        from apps.products.models import Product, ProductBatch

        rows = (
            ProductBatch.objects
            .filter(
                product__status=Product.ProductStatus.ACTIVE,
                store__is_active=True,
            )
            .values(
                "store_id", "store__name", "product_id",
                "product__name", "product__sku", "product__min_stock",
            )
            .annotate(qty=Sum("quantity"))
        )

        per_product = {}
        for r in rows:
            per_product.setdefault(r["product_id"], []).append(r)

        results = []
        for product_id, prows in per_product.items():
            for r in prows:
                qty = r["qty"] or 0
                # min_stock kiritilmagan (0) — "tugaganda" (qty <= 0) chiqadi
                threshold = r["product__min_stock"] or 0
                if qty > threshold:
                    continue
                sources = sorted(
                    (
                        {
                            "store": o["store_id"],
                            "store_name": o["store__name"],
                            "quantity": o["qty"] or 0,
                        }
                        for o in prows
                        if o["store_id"] != r["store_id"] and (o["qty"] or 0) > 0
                    ),
                    key=lambda s: -s["quantity"],
                )
                available = sum(s["quantity"] for s in sources)
                results.append({
                    "id": f"{r['store_id']}-{product_id}",
                    "store": r["store_id"],
                    "store_name": r["store__name"],
                    "product": product_id,
                    "product_name": r["product__name"],
                    "sku": r["product__sku"] or "",
                    "current_quantity": qty,
                    "min_stock": threshold,
                    "action_type": "transfer" if available > 0 else "purchase",
                    "status": "open",
                    "available_elsewhere": available,
                    "sources": sources,
                    "created_at": None,
                    "resolved_at": None,
                })

        if store_id:
            results = [x for x in results if str(x["store"]) == str(store_id)]
        if action_type in ("purchase", "transfer"):
            results = [x for x in results if x["action_type"] == action_type]
        if search:
            needle = str(search).strip().lower()
            if needle:
                results = [
                    x for x in results
                    if needle in (x["product_name"] or "").lower()
                    or needle in (x["sku"] or "").lower()
                ]

        # Eng kritigi birinchi: qoldiq/minimal nisbati o'sish tartibida
        results.sort(
            key=lambda x: (
                (x["current_quantity"] / x["min_stock"]) if x["min_stock"] else 0,
                x["product_name"] or "",
            )
        )
        return results

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

    @staticmethod
    def reevaluate_product(product):
        """
        Re-run low-stock evaluation for a product across EVERY store that carries
        it. Call this when the product-level `min_stock` threshold changes, so
        OPEN/RESOLVED records (and notifications) reflect the new threshold
        without waiting for the next stock mutation.
        """
        product_id = product.id if hasattr(product, "id") else product
        store_ids = (
            ProductBatch.objects
            .filter(product_id=product_id)
            .values_list("store_id", flat=True)
            .distinct()
        )
        # ⚠️ MUAMMO [PERF]: loop ichida store bo'yicha alohida schedule_evaluation.
        #   Har bir chaqiruv on_commit'ga bitta product_ids=[product_id] li evaluate_batch qo'yadi,
        #   ya'ni N ta store uchun N ta alohida aggregate + N ta bulk sikli. Bir mahsulot ko'p
        #   do'konda bo'lsa — bu ko'p mayda tranzaksiya. GET emas (min_stock o'zgarganda ishlaydi),
        #   shuning uchun prioritet past, lekin store'lar bo'yicha guruhlab bitta rejalashtirishga
        #   birlashtirsa bo'ladi.
        for store_id in store_ids:
            LowStockService.schedule_evaluation(store=store_id, product_ids=[product_id])

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
            # 1 query: current stock per (store, product) + the product-level
            # threshold (min_stock now lives on Product, so it is identical across
            # a product's batches — Max() just collapses the join to one value).
            stock_rows = (
                ProductBatch.objects
                .filter(store=store_obj, product_id__in=ids)
                .values("product_id")
                .annotate(qty=Sum("quantity"), threshold=Max("product__min_stock"))
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


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1  (reevaluate_product: store'lar bo'yicha loopда alohida schedule)
# Arxitektura muammolari: 0
# Umumiy baho: 9 / 10
# Izoh: ✅ YAXSHI — evaluate_batch loop ichida query qilmaydi: stock + threshold bitta aggregate,
#   mavjud OPEN yozuvlar bitta query, yaratish bulk_create, hal qilish bulk_update, xabarlar bulk_create.
#   12.5k ProductBatch muhitida ham so'rov soni CHEGARALANGAN (N+1 yo'q). Bu GET emas — yozuv/hodisa yo'li.
# Prioritet bo'yicha birinchi hal qilinishi kerak: [reevaluate_product'da store_ids bo'yicha bitta guruhlangan rejalashtirish]
# ═══════════════════════════════
