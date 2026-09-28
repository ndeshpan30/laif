import uuid
from sqlalchemy.types import TypeDecorator, Uuid
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from pgvector.sqlalchemy import Vector


class UniversalUUID(TypeDecorator):
    """
    Cross-dialect UUID type compatible with both PostgreSQL and SQLite.
    Safely accepts both str and uuid.UUID objects, coercing str into uuid.UUID
    to avoid 'str has no attribute hex' errors during SQLite parameter binding.
    """
    impl = Uuid
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(Uuid(as_uuid=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return uuid.UUID(value)
            except ValueError:
                return value
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except ValueError:
            return value


UniversalJSON = JSON().with_variant(JSONB, "postgresql")
Vector1536 = Vector(1536)


def default_uuid():
    return uuid.uuid4()

