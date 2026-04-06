
# ==========================  Image ===============================

def product_image_path(instance, filename):
    return f"products/{instance.category_id}/images/{filename}"


def product_barcode_path(instance, filename):
    return f"products/{instance.category_id}/barcodes/{filename}"