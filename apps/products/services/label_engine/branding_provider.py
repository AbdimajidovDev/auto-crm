import os
from pathlib import Path
from django.conf import settings
from django.core.files.storage import default_storage


class CompanyBrandingProvider:
    """
    Company branding provider (name and logo).
    In MVP: dynamically reads from settings/env or assets/storage fallback.
    Future: when Company/Tenant model is added, this provider can be updated
    without changing any label rendering or layout logic.
    """
    FALLBACK_NAME = "AVTOYON"
    FALLBACK_LOGO_RELATIVE_PATH = "branding/company_logo.png"

    @classmethod
    def get_name(cls) -> str:
        """Returns the company brand name."""
        return getattr(settings, "COMPANY_NAME", cls.FALLBACK_NAME)

    @classmethod
    def get_logo_path_or_url(cls) -> str | None:
        """
        Returns the company logo path or URL for external consumption.
        """
        custom_path = getattr(settings, "COMPANY_LOGO_PATH", None)
        if custom_path:
            if default_storage.exists(custom_path):
                try:
                    return default_storage.url(custom_path)
                except Exception:
                    return custom_path
            if os.path.isfile(custom_path):
                return custom_path

        if default_storage.exists(cls.FALLBACK_LOGO_RELATIVE_PATH):
            try:
                return default_storage.url(cls.FALLBACK_LOGO_RELATIVE_PATH)
            except Exception:
                return cls.FALLBACK_LOGO_RELATIVE_PATH

        # Check in assets/branding/
        assets_logo = Path(settings.BASE_DIR) / "assets" / "branding" / "company_logo.png"
        if assets_logo.is_file():
            return str(assets_logo)

        return None

    @classmethod
    def get_logo_file_path(cls) -> str | None:
        """
        Returns local filesystem path for image processing (Pillow / ReportLab).
        """
        custom_path = getattr(settings, "COMPANY_LOGO_PATH", None)
        if custom_path:
            if os.path.isabs(custom_path) and os.path.isfile(custom_path):
                return custom_path
            if default_storage.exists(custom_path):
                try:
                    return default_storage.path(custom_path)
                except (NotImplementedError, AttributeError):
                    pass

        if default_storage.exists(cls.FALLBACK_LOGO_RELATIVE_PATH):
            try:
                return default_storage.path(cls.FALLBACK_LOGO_RELATIVE_PATH)
            except (NotImplementedError, AttributeError):
                pass

        assets_logo = Path(settings.BASE_DIR) / "assets" / "branding" / "company_logo.png"
        if assets_logo.is_file():
            return str(assets_logo)

        return None
