from apps.inventory.models import InventorySession, InventoryMovement


# Invetarizatsiya jarayonida mahsulot sotilganda miqdordan ayirish
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


# Invetarizatsiya jarayonida boshqa do'konga transfer qilinganda miqdordan ayirish
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


# Invetarizatsiya jarayonida Do'konga transfer qilinganda miqdorgaga qo'shish
def handle_transfer_in(transfer):

    session = InventorySession.objects.filter(
        store=transfer.to_store,
        status="active"
    ).first()

    if not session:
        return

    for item in transfer.items.all():
        InventoryMovement.objects.create(
            session=session,
            product=item.product,
            quantity=item.quantity,
            type=InventoryMovement.Type.TRANSFER_IN,
            ref_id=transfer.id
        )


# Invetarizatsiya jarayonida mahsulot vazvrat qilinganda miqdorga qo'shish
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


# Invetarizatsiya jarayonida Kirim qilinganda miqdorgaga qo'shish
def handle_stock_entry(entry):

    session = InventorySession.objects.filter(
        store=entry.store,
        status="active"
    ).first()

    if not session:
        return

    for item in entry.items.all():
        InventoryMovement.objects.create(
            session=session,
            product=item.product,
            quantity=item.quantity,
            type=InventoryMovement.Type.ENTRY,
            ref_id=entry.id
        )

