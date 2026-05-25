
# ==========================  Image ===============================

def product_image_path(instance, filename):
    return f"products/{instance.product.category.slug}/images/{filename}"


# ==========================  Barcode ===============================
def product_barcode_path(instance, filename):
    return f"products/{instance.category.slug}/barcodes/{filename}"


# ==========================  SKU ===============================
