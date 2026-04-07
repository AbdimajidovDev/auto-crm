from django.db import models

from apps.common.models.timestamp_mixin import TimestampMixin


# Create your models here.



class StockTransfer(TimestampMixin):
    from_store = models.ForeignKey("store.Store", on_delete=models.CASCADE, related_name="outgoing")
    to_store = models.ForeignKey("store.Store", on_delete=models.CASCADE, related_name="incoming")

    product = models.ForeignKey("products.Product", on_delete=models.CASCADE)

    quantity = models.IntegerField()

    purchase_price = models.DecimalField(max_digits=12, decimal_places=2)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)

    barcode = models.CharField(max_length=12)

    class Meta:
        db_table = "stock_transfer"
