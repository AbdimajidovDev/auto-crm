from apps.inventory.models import InventorySession, InventoryMovement


def handle_sale_item(sale_item):

    session = InventorySession.objects.filter(
        store=sale_item.sale.store,
        status="active"
    ).first()

    if not session:
        return

    InventoryMovement.objects.create(
        session=session,
        product=sale_item.product,
        quantity=sale_item.quantity,
        type=InventoryMovement.Type.SALE,
        ref_id=sale_item.sale_id
    )


def handle_transfer_approved(transfer):

    session = InventorySession.objects.filter(
        store=transfer.from_store,
        status="active"
    ).first()

    if not session:
        return

    for item in transfer.items.all():
        InventoryMovement.objects.create(
            session=session,
            product=item.product,
            quantity=item.quantity,
            type=InventoryMovement.Type.TRANSFER_OUT,
            ref_id=transfer.id
        )


def handle_sale_return(return_obj, sale_item, quantity):

    session = InventorySession.objects.filter(
        store=return_obj.store,
        status="active"
    ).first()

    if not session:
        return

    InventoryMovement.objects.create(
        session=session,
        product=sale_item.product,
        quantity=quantity,
        type=InventoryMovement.Type.RETURN,
        ref_id=return_obj.id
    )
