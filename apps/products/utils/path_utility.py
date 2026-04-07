
# ==========================  Image ===============================

def product_image_path(instance, filename):
    return f"products/{instance.product.category.slug}/images/{filename}"


def product_barcode_path(instance, filename):
    return f"products/{instance.product.category.slug}/barcodes/{filename}"