"""
Exceptions for the Barcode Label Designer & Printing Engine.
"""

class LabelEngineError(Exception):
    """Base exception for all label engine errors."""
    pass


class LabelValidationError(LabelEngineError):
    """Raised when label template layout or parameters fail validation."""
    def __init__(self, message: str, errors: dict | list | None = None):
        super().__init__(message)
        self.message = message
        self.errors = errors or {}


class LabelResolutionError(LabelEngineError):
    """Raised when data resolution fails (e.g. invalid store, product)."""
    pass


class LabelRenderingError(LabelEngineError):
    """Raised when rendering PDF or PNG preview fails."""
    pass
