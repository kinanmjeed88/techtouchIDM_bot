# database.py (النسخة النهائية والمحسّنة)
import os
from sqlalchemy import create_engine, Column, Integer, String, Text, BigInteger, Boolean, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

try:
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
except Exception as e:
    logger.error(f"Failed to connect to database: {e}")
    exit()

# --- نماذج الجداول ---

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True, index=True)
    full_name = Column(String)
    username = Column(String, nullable=True)
    is_blocked = Column(Boolean, default=False)

class BannedWord(Base):
    __tablename__ = "banned_words"
    id = Column(Integer, primary_key=True, index=True)
    word = Column(String, unique=True, nullable=False)
    restriction_duration = Column(String, default="none") # 'day', 'week', 'month', 'none'

class BannedLink(Base):
    __tablename__ = "banned_links"
    id = Column(Integer, primary_key=True, index=True)
    link_pattern = Column(String, unique=True, nullable=False)
    restriction_duration = Column(String, default="none") # 'day', 'week', 'month', 'none'

class WhitelistedLink(Base):
    __tablename__ = "whitelisted_links"
    id = Column(Integer, primary_key=True, index=True)
    link_prefix = Column(String, unique=True, nullable=False)

class AutoReply(Base):
    __tablename__ = "auto_replies"
    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String, unique=True, nullable=False)
    reply_text = Column(Text, nullable=False)

class Settings(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)

# --- دوال التعامل مع قاعدة البيانات ---

def init_db():
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified.")

def add_or_update_user(user_id: int, full_name: str, username: str):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.full_name = full_name
            user.username = username
            user.is_blocked = False
        else:
            user = User(id=user_id, full_name=full_name, username=username)
            db.add(user)
        db.commit()
    finally:
        db.close()

def get_all_active_users():
    db = SessionLocal()
    try:
        return db.query(User).filter(User.is_blocked == False).all()
    finally:
        db.close()

def set_user_blocked(user_id: int):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.is_blocked = True
            db.commit()
    finally:
        db.close()

def get_blocked_user_count():
    db = SessionLocal()
    try:
        return db.query(User).filter(User.is_blocked == True).count()
    finally:
        db.close()

# --- دوال مشتركة للقوائم ---

def db_add_item(item_data, model, column_name):
    db = SessionLocal()
    try:
        # Check if item_data is a dictionary (for BannedWord/Link with restriction_duration)
        if isinstance(item_data, dict):
            # Check if an item with the same unique column value already exists
            existing_item = db.query(model).filter(getattr(model, column_name) == item_data[column_name]).first()
            if existing_item:
                return False # Item already exists
            new_item = model(**item_data)
        else:
            # For models without restriction_duration or when only the main column is provided
            existing_item = db.query(model).filter(getattr(model, column_name) == item_data).first()
            if existing_item:
                return False # Item already exists
            new_item = model(**{column_name: item_data})
        
        db.add(new_item)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Error adding item to DB: {e}")
        return False
    finally:
        db.close()

def db_get_all_items(model, column_name):
    db = SessionLocal()
    try:
        column_attr = getattr(model, column_name)
        # Return a list of tuples if multiple columns are needed, otherwise just the column value
        if hasattr(model, 'restriction_duration') and column_name != 'restriction_duration':
            return [(item[0], item[1]) for item in db.query(column_attr, model.restriction_duration).all()]
        return [item[0] for item in db.query(column_attr).all()]
    finally:
        db.close()

def db_delete_item(item_to_delete, model, column_name):
    db = SessionLocal()
    try:
        column_attr = getattr(model, column_name)
        item = db.query(model).filter(column_attr == item_to_delete).first()
        if item:
            db.delete(item)
            db.commit()
            return True
        return False
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting item from DB: {e}")
        return False
    finally:
        db.close()

def get_all_auto_replies():
    db = SessionLocal()
    try:
        return db.query(AutoReply).all()
    finally:
        db.close()

# --- دوال الإعدادات ---

def get_setting(key: str):
    db = SessionLocal()
    try:
        setting = db.query(Settings).filter(Settings.key == key).first()
        return setting.value if setting else None
    finally:
        db.close()

def set_setting(key: str, value: str):
    db = SessionLocal()
    try:
        setting = db.query(Settings).filter(Settings.key == key).first()
        if setting:
            setting.value = value
        else:
            setting = Settings(key=key, value=value)
            db.add(setting)
        db.commit()
    finally:
        db.close()


