class AppError(Exception):
    """Base class for all application-level errors."""


class BookingConflictError(AppError):
    """The requested time slot overlaps with an existing appointment."""


class NoAppointmentError(AppError):
    """The client has no active appointment when one was expected."""


class SlotUnavailableError(AppError):
    """The requested slot violates a booking rule (outside hours, horizon, or notice window)."""


class TooManyAppointmentsError(AppError):
    """The client already has one active future appointment."""


class CalendarSyncError(AppError):
    """A Google Calendar operation failed."""


class TelegramDeliveryError(AppError):
    """A Telegram message could not be delivered."""


class FlowExpiredError(AppError):
    """The user's multi-step flow session has expired or is in an unexpected state."""
