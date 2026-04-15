from datetime import date, datetime

from pydantic import BaseModel, model_validator


class BusyInterval(BaseModel):
    """A time interval during which the master is unavailable."""

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def validate_order(self) -> "BusyInterval":
        if self.end <= self.start:
            raise ValueError("BusyInterval end must be after start")
        return self


class TimeSlot(BaseModel):
    """A single 1-hour appointment slot."""

    start: datetime
    end: datetime


class DaySlots(BaseModel):
    """Available slots grouped by date."""

    date: date
    slots: list[TimeSlot]
