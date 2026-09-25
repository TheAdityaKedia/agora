import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        # pool_pre_ping: a hosted DB (Neon) drops idle connections, and a long
        # scrape can leave a pooled connection idle for many minutes before
        # the next save. Ping on checkout so a dead one is replaced, not used.
        _engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    return _engine


def get_session():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


def init_db():
    Base.metadata.create_all(get_engine())
