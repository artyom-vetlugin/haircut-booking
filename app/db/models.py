# All ORM model classes must be imported here so that Alembic autogenerate
# can detect schema changes via Base.metadata.
#
# Example (add as models are defined):
#   from app.db._appointment import Appointment  # noqa: F401

from app.db.base import Base  # noqa: F401

__all__ = ["Base"]
