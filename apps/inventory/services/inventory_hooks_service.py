from apps.inventory.models import InventorySession, InventoryMovement
from apps.inventory.services.low_stock_service import LowStockService


def _get_active_session(store):
    """Umumiy helper — har funksiyada takrorlanishni oldini oladi."""
    return InventorySession.objects.filter(
        store=store,
        status=InventorySession.Status.ACTIVE  # ✅ konstanta
    ).first()


def handle_sale_item(sale_item):
    session = _get_active_session(sale_item.sale.store)
    if not session:
        return

    InventoryMovement.objects.create(
        session=session,
        product=sale_item.product,
        quantity=sale_item.quantity,
        type=InventoryMovement.Type.SALE,
        ref_id=sale_item.sale_id,
    )


def handle_transfer_approved(transfer):
    # 🔻 LOW STOCK: source store quantity decreased -> may cross threshold.
    # Runs independently of an active inventory session (single query for ids).
    LowStockService.schedule_evaluation(
        store=transfer.from_store_id,
        product_ids=list(transfer.items.values_list("product_id", flat=True)),
    )

    session = _get_active_session(transfer.from_store)
    if not session:
        return

    # ✅ select_related + bulk_create — N+1 yo'q
    items = transfer.items.select_related("product").all()
    InventoryMovement.objects.bulk_create([
        InventoryMovement(
            session=session,
            product=item.product,
            quantity=item.quantity,
            type=InventoryMovement.Type.TRANSFER_OUT,
            ref_id=transfer.id,
        )
        for item in items
    ])


def handle_transfer_in(transfer):
    # 🔺 LOW STOCK: destination store quantity increased -> may resolve an OPEN record.
    LowStockService.schedule_evaluation(
        store=transfer.to_store_id,
        product_ids=list(transfer.items.values_list("product_id", flat=True)),
    )

    session = _get_active_session(transfer.to_store)
    if not session:
        return

    items = transfer.items.select_related("product").all()
    InventoryMovement.objects.bulk_create([
        InventoryMovement(
            session=session,
            product=item.product,
            quantity=item.quantity,
            type=InventoryMovement.Type.TRANSFER_IN,
            ref_id=transfer.id,
        )
        for item in items
    ])


def handle_sale_return(return_obj, sale_item, quantity):
    session = _get_active_session(return_obj.store)
    if not session:
        return

    InventoryMovement.objects.create(
        session=session,
        product=sale_item.product,
        quantity=quantity,
        type=InventoryMovement.Type.RETURN,
        ref_id=return_obj.id,
    )


def handle_stock_entry(entry, items=None):
    # 🔺 LOW STOCK: incoming stock increased quantity -> may resolve OPEN records.
    if items is not None:
        entry_product_ids = [item.product_id for item in items]
    else:
        entry_product_ids = list(entry.items.values_list("product_id", flat=True))
    LowStockService.schedule_evaluation(store=entry.store_id, product_ids=entry_product_ids)

    session = _get_active_session(entry.store)
    if not session:
        return

    # Agar items berilmagan bo'lsa (tashqaridan chaqirilganda) — DB dan o'qi
    if items is None:
        items = entry.items.select_related("product").all()

    InventoryMovement.objects.bulk_create([
        InventoryMovement(
            session=session,
            product=item.product,
            quantity=item.quantity,
            type=InventoryMovement.Type.ENTRY,
            ref_id=entry.id,
        )
        for item in items
    ])
