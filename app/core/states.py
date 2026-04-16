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

# Master — booking a walk-in client
MASTER_BOOKING_ENTER_NAME  = "master_booking:enter_name"
MASTER_BOOKING_SELECT_DATE = "master_booking:select_date"
MASTER_BOOKING_SELECT_SLOT = "master_booking:select_slot"
MASTER_BOOKING_CONFIRM     = "master_booking:confirm"

# Master — rescheduling an existing appointment
MASTER_RESCHEDULE_SELECT_APPT = "master_reschedule:select_appt"
MASTER_RESCHEDULE_SELECT_DATE = "master_reschedule:select_date"
MASTER_RESCHEDULE_SELECT_SLOT = "master_reschedule:select_slot"
MASTER_RESCHEDULE_CONFIRM     = "master_reschedule:confirm"

# Master — cancelling an existing appointment
MASTER_CANCEL_SELECT_APPT = "master_cancel:select_appt"
MASTER_CANCEL_CONFIRM     = "master_cancel:confirm"
