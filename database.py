import os
import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, desc
from sqlalchemy.orm import sessionmaker, declarative_base

# --- إعدادات الاتصال بقاعدة البيانات ---
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- تعريف الجداول ---

class Group(Base):
    """جدول لتخزين المجموعات التي يديرها البوت."""
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(String, unique=True, nullable=False)
    group_title = Column(String, nullable=False)

class Message(Base):
    """جدول لتخزين الرسائل وتحليلها."""
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String, index=True, unique=True)
    user_id = Column(String)
    group_id = Column(String, index=True)
    text = Column(String)
    sentiment = Column(String)
    positive_reactions = Column(Integer, default=0)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

class AutoReply(Base):
    """جدول لتخزين الردود التلقائية."""
    __tablename__ = "auto_replies"
    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String, unique=True, nullable=False)
    reply_text = Column(Text, nullable=False)

def init_db():
    """يقوم بإنشاء جميع الجداول في قاعدة البيانات."""
    Base.metadata.create_all(bind=engine)

# --- دوال إدارة المجموعات ---

def add_or_update_group(group_id: str, group_title: str):
    """إضافة مجموعة جديدة أو تحديث اسمها."""
    db = SessionLocal()
    try:
        group = db.query(Group).filter(Group.group_id == str(group_id)).first()
        if group:
            group.group_title = group_title
        else:
            new_group = Group(group_id=str(group_id), group_title=group_title)
            db.add(new_group)
        db.commit()
    finally:
        db.close()

def remove_group(group_id: str):
    """حذف مجموعة من قاعدة البيانات."""
    db = SessionLocal()
    try:
        group = db.query(Group).filter(Group.group_id == str(group_id)).first()
        if group:
            db.delete(group)
            db.commit()
    finally:
        db.close()

def get_all_managed_groups():
    """جلب كل المجموعات التي يديرها البوت."""
    db = SessionLocal()
    try:
        return db.query(Group).all()
    finally:
        db.close()

# --- دوال إدارة الرسائل والتفاعلات ---

def save_message(message_id: str, user_id: str, group_id: str, text: str, sentiment: str):
    """حفظ رسالة جديدة في قاعدة البيانات."""
    db = SessionLocal()
    try:
        existing_message = db.query(Message).filter(Message.message_id == str(message_id)).first()
        if existing_message: return
        db_message = Message(message_id=str(message_id), user_id=str(user_id), group_id=str(group_id), text=text, sentiment=sentiment)
        db.add(db_message)
        db.commit()
    finally:
        db.close()

def update_message_reactions(message_id: str, reaction_count: int):
    """تحديث عدد التفاعلات على رسالة معينة."""
    db = SessionLocal()
    try:
        message = db.query(Message).filter(Message.message_id == str(message_id)).first()
        if message:
            message.positive_reactions = reaction_count
            db.commit()
    finally:
        db.close()

def get_top_reacted_messages(group_id: str, limit: int = 5):
    """جلب أكثر الرسائل تفاعلاً في مجموعة معينة."""
    db = SessionLocal()
    try:
        seven_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
        top_messages = db.query(Message).filter(
            Message.group_id == str(group_id),
            Message.timestamp >= seven_days_ago
        ).order_by(desc(Message.positive_reactions)).limit(limit).all()
        return top_messages
    finally:
        db.close()

# --- دوال إدارة الردود التلقائية ---

def add_or_update_reply(keyword: str, reply_text: str):
    """إضافة أو تحديث رد تلقائي."""
    db = SessionLocal()
    try:
        existing_reply = db.query(AutoReply).filter(AutoReply.keyword == keyword.lower()).first()
        if existing_reply:
            existing_reply.reply_text = reply_text
        else:
            new_reply = AutoReply(keyword=keyword.lower(), reply_text=reply_text)
            db.add(new_reply)
        db.commit()
    finally:
        db.close()

def get_all_replies():
    """جلب كل الردود التلقائية."""
    db = SessionLocal()
    try:
        return db.query(AutoReply).all()
    finally:
        db.close()

def delete_reply(keyword: str):
    """حذف رد تلقائي."""
    db = SessionLocal()
    try:
        reply_to_delete = db.query(AutoReply).filter(AutoReply.keyword == keyword.lower()).first()
        if reply_to_delete:
            db.delete(reply_to_delete)
            db.commit()
            return True
        return False
    finally:
        db.close()

# --- تهيئة قاعدة البيانات عند بدء التشغيل ---
init_db()
