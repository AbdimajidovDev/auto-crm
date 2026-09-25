import os
from typing import Any
from decimal import Decimal
from django.core.files.storage import default_storage

from apps.store.models import Store, StoreUser
from apps.products.models import Product, ProductBatch
from .branding_provider import CompanyBrandingProvider
from .exceptions import LabelResolutionError


class LabelDataResolver:
    """
    Resolves data for a Product according to the authenticated user and store context.
    - Deterministic store and price resolution
    - Whitelisted data field extraction
    - Local path resolution for safe image rendering
    """

    @classmethod
    def resolve_store(cls, user, store_id: int | None = None) -> Store | None:
        """
        Resolves store according to user permissions and hierarchy:
        1. Explicit store_id:
           - Superuser: directly gets store.
           - Regular user: checks StoreUser membership.
        2. No store_id:
           - User with single store link: that store.
           - User with multiple links or superuser: BASE warehouse store, fallback to first active.
        """
        if store_id:
            try:
                store_id = int(store_id)
            except (ValueError, TypeError):
                raise LabelResolutionError(f"Noto'g'ri store_id formati: {store_id}")

            store = Store.objects.filter(id=store_id, is_active=True).first()
            if not store:
                raise LabelResolutionError(f"Do'kon topilmadi yoki faol emas (ID: {store_id}).")

            if getattr(user, "is_superuser", False):
                return store

            has_access = StoreUser.objects.filter(user=user, store=store, is_active=True).exists()
            if not has_access:
                raise LabelResolutionError(f"Foydalanuvchida ushbu do'konga ruxsat yo'q (Store ID: {store_id}).")
            return store

        # No store_id provided
        if getattr(user, "is_superuser", False):
            base_store = Store.objects.filter(type=Store.StoreType.BASE, is_active=True).first()
            if base_store:
                return base_store
            return Store.objects.filter(is_active=True).first()

        user_store_ids = list(
            StoreUser.objects.filter(user=user, is_active=True).values_list("store_id", flat=True)
        )
        if len(user_store_ids) == 1:
            return Store.objects.filter(id=user_store_ids[0], is_active=True).first()

        if len(user_store_ids) > 1:
            # Check if one of them is BASE
            base_store = Store.objects.filter(id__in=user_store_ids, type=Store.StoreType.BASE, is_active=True).first()
            if base_store:
                return base_store
            return Store.objects.filter(id=user_store_ids[0], is_active=True).first()

        # Fallback if user has no store links
        base_store = Store.objects.filter(type=Store.StoreType.BASE, is_active=True).first()
        if base_store:
            return base_store
        return Store.objects.filter(is_active=True).first()

    @classmethod
    def resolve_price(cls, product: Product, store: Store | None) -> str:
        """
        Resolves selling price for a product in the given store.
        Returns formatted string (e.g. '150 000 so\'m') or empty string '' if no batch found.
        Never crashes.
        """
        if not store:
            return ""

        batch = ProductBatch.objects.filter(product=product, store=store, is_active=True).first()
        price_val = None
        if batch and batch.selling_price:
            price_val = batch.selling_price
        else:
            from apps.inventory.services.stock_allocation_service import StockAllocationService
            price_val = StockAllocationService.resolve_selling_price(store, product)

        if not price_val:
            return ""
        try:
            if price_val % 1 == 0:
                formatted = f"{int(price_val):,}".replace(",", " ")
            else:
                formatted = f"{price_val:,.2f}".replace(",", " ")
            return f"{formatted} so'm"
        except Exception:
            return f"{price_val} so'm"

    @classmethod
    def resolve_uploaded_image_path(cls, image_url: str | None) -> str | None:
        """
        Resolves a safe absolute local filesystem path for an uploaded image URL.
        Enforces strict boundary within settings.MEDIA_ROOT and prevents path traversal.
        """
        if not image_url or not isinstance(image_url, str):
            return None

        # Disallow directory traversal characters and URI schemes
        if ".." in image_url or "\\" in image_url:
            return None
        if "://" in image_url or image_url.startswith(("data:", "blob:", "file:")):
            return None

        from django.conf import settings
        media_url = getattr(settings, "MEDIA_URL", "/media/")

        clean_url = image_url.strip()
        if clean_url.startswith(media_url):
            rel_path = clean_url[len(media_url):]
        elif clean_url.startswith("/media/"):
            rel_path = clean_url[7:]
        elif clean_url.startswith("media/"):
            rel_path = clean_url[6:]
        elif not clean_url.startswith("/"):
            rel_path = clean_url
        else:
            return None

        media_root = os.path.abspath(str(settings.MEDIA_ROOT))
        full_path = os.path.abspath(os.path.join(media_root, rel_path.lstrip("/")))

        # Boundary check: ensure full_path is strictly within media_root
        if not full_path.startswith(media_root + os.sep) and full_path != media_root:
            return None

        if os.path.isfile(full_path):
            return full_path
        return None

    @classmethod
    def _get_file_path(cls, file_field) -> str | None:
        """Helper to get safe local file path from ImageField/FileField."""
        if not file_field:
            return None
        try:
            if hasattr(file_field, "path") and os.path.isfile(file_field.path):
                return file_field.path
        except (NotImplementedError, AttributeError, ValueError):
            pass

        if hasattr(file_field, "name") and file_field.name:
            if default_storage.exists(file_field.name):
                try:
                    return default_storage.path(file_field.name)
                except (NotImplementedError, AttributeError):
                    pass
        return None

    @classmethod
    def resolve_context(cls, product: Product, store: Store | None = None) -> dict[str, Any]:
        """
        Builds dictionary of resolved values for all whitelisted fields.
        """
        brand_name = product.brand.name if product.brand else ""
        brand_logo = cls._get_file_path(product.brand.logo) if (product.brand and product.brand.logo) else None

        category_name = product.category.name if product.category else ""
        unit_name = product.unit_measurement.measurement if product.unit_measurement else ""

        # Product main image
        product_image = None
        first_img = product.images.first()
        if first_img and first_img.image:
            product_image = cls._get_file_path(first_img.image)

        price_str = cls.resolve_price(product, store)

        company_name = CompanyBrandingProvider.get_name()
        company_logo = CompanyBrandingProvider.get_logo_file_path()

        return {
            "product.name": product.name or "",
            "product.sku": product.sku or "",
            "product.barcode": product.barcode or "",
            "product.brand.name": brand_name,
            "product.brand.logo": brand_logo,
            "product.category.name": category_name,
            "product.unit": unit_name,
            "product.image": product_image,
            "product.batch.selling_price": price_str,
            "company.name": company_name,
            "company.logo": company_logo,
        }
