class StockAllocationError(Exception):
    """Base exception for inventory stock allocation and lot management."""
    pass


class InsufficientStockError(StockAllocationError):
    """Raised when available stock in lots is insufficient to fulfill allocation."""
    pass


class InvalidAllocationError(StockAllocationError):
    """Raised when allocation parameters or operations violate domain invariants."""
    pass


class InvalidReversalError(StockAllocationError):
    """Raised when a return reversal cannot be performed (e.g. over-return or mismatch)."""
    pass


class AllocationConflictError(StockAllocationError):
    """Raised when a duplicate allocation is attempted for the same source item."""
    pass
