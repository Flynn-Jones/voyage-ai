import os
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker

DATABASE_URL = "sqlite:///" + os.getenv("DB_FILE", "accommodation-db.sqlite")

engine = create_engine(DATABASE_URL, echo=False)


# Enforce SQLite foreign keys
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = scoped_session(
    sessionmaker(bind=engine, autoflush=False, autocommit=False)
)


class Base(DeclarativeBase):
    pass


def get_db():
    return SessionLocal()
