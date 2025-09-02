import os
import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, BigInteger
from sqlalchemy.orm import sessionmaker, declarative_base
import logging # استيراد مكتبة التسجيل

# إعداد المسجل (logger)
logger = logging.getLogger(__name__)

# --- إعداد الاتصال ---
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

try:
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
    logger.info("Database engine created successfully.")
except Exception as e:
    logger.error(f"Failed to create database engine: {e}")

# --- تعريف الجداول ---
class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String, unique=True)
    user_id = Column(String)
    group_id = Column(String, index=True)
    text = Column(Text)
    sentiment = Column(String)
    positive_reactions = Column(Integer, default=0)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

class AutoReply(Base):
    __tablename__ = "auto_replies"
    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String, unique=True, nullable=False)
    reply_text = Column(Text, nullable=False)

class ManagedGroup(Base):
    __tablename__ = "managed_groups"
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(String, unique=True, nullable=False)
    group_title = Column(String)

# --- دالة الحفظ التشخيصية ---
def save_message(message_id, user_id, group_id, text, sentiment):
    logger.debug("--- [DB-SAVE-1] save_message function called ---")
    db = None
    try:
        logger.debug("[DB-SAVE-2] Creating database session.")
        db = SessionLocal()
        logger.debug("[DB-SAVE-3] Session created. Creating Message object.")
        
        db_message = Message(
            message_id=message_id,
            user_id=user_id,
            group_id=group_id,
            text=text,
            sentiment=sentiment
        )
        
        logger.debug(f"[DB-SAVE-4] Message object created for message_id: {message_id}. Adding to session.")
        db.add(db_message)
        
        logger.debug("[DB-SAVE-5] Committing transaction.")
        db.commit()
        
        logger.info(f"--- [DB-SUCCESS] Successfully committed message {message_id} to database. ---")

    except Exception as e:
        logger.error(f"--- [DB-ERROR] An error occurred in save_message: {e} ---", exc_info=True)
        if db:
            logger.debug("Rolling back transaction due to error.")
            db.rollback()
    finally:
        if db:
            logger.debug("[DB-SAVE-6] Closing database session.")
            db.close()

# --- باقي الدوال تبقى كما هي ---
def init_db():
    try:
        logger.info("Initializing database tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database tables: {e}")

def add_or_update_group(group_id: str, group_title: str):
    db = SessionLocal()
    try:
        existing_group = db.query(ManagedGroup).filter(ManagedGroup.group_id == group_id).first()
        if existing_group:
            existing_group.group_title = group_title
        else:
            new_group = ManagedGroup(group_id=group_id, group_title=group_title)
            db.add(new_group)
        db.commit()
    finally:
        db.close()

def remove_group(group_id: str):
    db = SessionLocal()
    try:
        group_to_delete = db.query(ManagedGroup).filter(ManagedGroup.group_id == group_id).first()
        if group_to_delete:
            db.delete(group_to_delete)
            db.commit()
    finally:
        db.close()

def get_all_managed_groups():
    db = SessionLocal()
    try:
        return db.query(ManagedGroup).all()
    finally:
        db.close()

def update_message_reactions(message_id: str, count: int):
    db = SessionLocal()
    try:
        message = db.query(Message).filter(Message.message_id == message_id).first()
        if message:
            message.positive_reactions = count
            db.commit()
    finally:
        db.close()

def get_top_reacted_messages(group_id: str, limit: int = 5):
    db = SessionLocal()
    try:
        seven_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
        return db.query(Message).filter(
            Message.group_id == group_id,
            Message.timestamp >= seven_days_ago
        ).order_by(Message.positive_reactions.desc()).limit(limit).all()
    finally:
        db.close()

def add_or_update_reply(keyword: str, reply_text: str):
    db = SessionLocal()
    try:
        existing_reply = db.query(AutoReply).filter(AutoReply.keyword.ilike(keyword)).first()
        if existing_reply:
            existing_reply.reply_text = reply_text
        else:
            new_reply = AutoReply(keyword=keyword.lower(), reply_text=reply_text)
            db.add(new_reply)
        db.commit()
    finally:
        db.close()

def get_all_replies():
    db = SessionLocal()
    try:
        return db.query(AutoReply).all()
    finally:
        db.close()

def delete_reply(keyword: str):
    db = SessionLocal()
    try:
        reply_to_delete = db.query(AutoReply).filter(AutoReply.keyword.ilike(keyword)).first()
        if reply_to_delete:
            db.delete(reply_to_delete)
            db.commit()
            return True
        return False
    finally:
        db.close()

# تأكد من استدعاء init_db() في النهاية
init_db()
