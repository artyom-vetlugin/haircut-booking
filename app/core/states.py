"""Bot session state constants for the Telegram booking state machine.

States are stored as strings in BotSession.current_state.
The draft_payload JSON dict carries intermediate data for multi-step flows.
"""

IDLE = "idle"

# Booking flow
BOOKING_SELECT_DATE = "booking:select_date"
BOOKING_SELECT_SLOT = "booking:select_slot"
BOOKING_CONFIRM = "booking:confirm"

# Reschedule flow
RESCHEDULE_SELECT_DATE = "reschedule:select_date"
RESCHEDULE_SELECT_SLOT = "reschedule:select_slot"
RESCHEDULE_CONFIRM = "reschedule:confirm"

# Cancel flow
CANCEL_CONFIRM = "cancel:confirm"
