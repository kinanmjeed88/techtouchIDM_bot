import os
import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, func, BigInteger
from sqlalchemy.orm import sessionmaker, declarative_base

# --- إعداد الاتصال بقاعدة البيانات ---
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- تعريف الجداول (Models) ---

class Message(Base):
    """جدول لتخزين رسائل المجموعات لتحليلها."""
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String, unique=True)
    user_id = Column(String)
    group_id = Column(String, index=True)
    text = Column(Text)
    sentiment = Column(String)
    positive_reactions = Column(Integer, default=0)
    timestamp = Column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC))

class AutoReply(Base):
    """جدول لتخزين الردود التلقائية."""
    __tablename__ = "auto_replies"
    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String, unique=True, nullable=False)
    reply_text = Column(Text, nullable=False)

class ManagedGroup(Base):
    """جدول لتخزين المجموعات التي يديرها البوت."""
    __tablename__ = "managed_groups"
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(String, unique=True, nullable=False)
    group_title = Column(String)

class PrivateMessage(Base):
    """جدول لتخزين الرسائل الخاصة الموجهة للبوت من المستخدمين."""
    __tablename__ = "private_messages"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    message_text = Column(Text)
    timestamp = Column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC))

# --- دوال التعامل مع قاعدة البيانات ---

def init_db():
    """إنشاء جميع الجداول في قاعدة البيانات."""
    Base.metadata.create_all(bind=engine)

def save_message(message_id: str, user_id: str, group_id: str, text: str, sentiment: str):
    """حفظ رسالة مجموعة جديدة."""
    db = SessionLocal()
    try:
        db_message = Message(message_id=message_id, user_id=user_id, group_id=group_id, text=text, sentiment=sentiment)
        db.add(db_message)
        db.commit()
    finally:
        db.close()

# --- !! الدالة المفقودة التي تم إضافتها الآن !! ---
def save_private_message(user_id: str, text: str):
    """حفظ رسالة خاصة جديدة من مستخدم."""
    db = SessionLocal()
    try:
        db_message = PrivateMessage(user_id=user_id, message_text=text)
        db.add(db_message)
        db.commit()
    finally:
        db.close()
# --- نهاية الإضافة ---

def add_or_update_group(group_id: str, group_title: str):
    """إضافة مجموعة جديدة أو تحديث اسمها."""
    db = SessionLocal()
    existing_group = db.query(ManagedGroup).filter(ManagedGroup.group_id == group_id).first()
    if existing_group:
        existing_group.group_title = group_title
    else:
        new_group = ManagedGroup(group_id=group_id, group_title=group_title)
        db.add(new_group)
    db.commit()
    db.close()

def remove_group(group_id: str):
    """حذف مجموعة من قائمة الإدارة."""
    db = SessionLocal()
    group_to_delete = db.query(ManagedGroup).filter(ManagedGroup.group_id == group_id).first()
    if group_to_delete:
        db.delete(group_to_delete)
        db.commit()
    db.close()

def get_all_managed_groups():
    """الحصول على قائمة بكل المجموعات المدارة."""
    db = SessionLocal()
    groups = db.query(ManagedGroup).all()
    db.close()
    return groups

def add_or_update_reply(keyword: str, reply_text: str):
    """إضافة رد تلقائي جديد أو تحديث رد موجود."""
    db = SessionLocal()
    existing_reply = db.query(AutoReply).filter(AutoReply.keyword.ilike(keyword.lower())).first()
    if existing_reply:
        existing_reply.reply_text = reply_text
    else:
        new_reply = AutoReply(keyword=keyword.lower(), reply_text=reply_text)
        db.add(new_reply)
    db.commit()
    db.close()

def get_all_replies():
    """الحصول على كل الردود التلقائية."""
    db = SessionLocal()
    replies = db.query(AutoReply).all()
    db.close()
    return replies

def delete_reply(keyword: str):
    """حذف رد تلقائي."""
    db = SessionLocal()
    reply_to_delete = db.query(AutoReply).filter(AutoReply.keyword.ilike(keyword.lower())).first()
    if reply_to_delete:
        db.delete(reply_to_delete)
        db.commit()
        db.close()
        return True
    db.close()
    return False

def update_message_reactions(message_id: str, positive_count: int):
    """تحديث عدد التفاعلات الإيجابية لرسالة."""
    db = SessionLocal()
    message = db.query(Message).filter(Message.message_id == message_id).first()
    if message:
        message.positive_reactions = positive_count
        db.commit()
    db.close()

def get_top_reacted_messages(group_id: str, limit: int = 5):
    """الحصول على أكثر الرسائل تفاعلاً في مجموعة معينة."""
    db = SessionLocal()
    messages = db.query(Message).filter(Message.group_id == group_id).order_by(Message.positive_reactions.desc()).limit(limit).all()
    db.close()
    return messages

# استدعاء الدالة لإنشاء الجداول عند بدء تشغيل التطبيق
init_db()
