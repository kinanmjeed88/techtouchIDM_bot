from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.sql import func
import datetime
import os

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set.")

# For Railway PostgreSQL, ensure the URL is correctly formatted
# It might come as postgres://, but SQLAlchemy prefers postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, unique=True, index=True)
    full_name = Column(String)
    username = Column(String, nullable=True)
    is_blocked = Column(Boolean, default=False)
    warnings = Column(Integer, default=0)
    muted_until = Column(DateTime, nullable=True)
    message_count = Column(Integer, default=0) # لعد الرسائل للمستخدمين

class BannedWord(Base):
    __tablename__ = "banned_words"
    id = Column(Integer, primary_key=True, index=True)
    word = Column(String, unique=True, index=True)
    mute_duration = Column(String, nullable=True) # \'day\', \'week\', \'month\', None

class BannedLink(Base):
    __tablename__ = "banned_links"
    id = Column(Integer, primary_key=True, index=True)
    link_pattern = Column(String, unique=True, index=True)
    mute_duration = Column(String, nullable=True)

class WhitelistedLink(Base):
    __tablename__ = "whitelisted_links"
    id = Column(Integer, primary_key=True, index=True)
    link_prefix = Column(String, unique=True, index=True)

class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True)
    value = Column(Text)

class AutoReply(Base):
    __tablename__ = "auto_replies"
    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String, unique=True, index=True)
    reply_text = Column(Text)

class PrivateMessage(Base):
    __tablename__ = "private_messages"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True) # ID المستخدم الذي أرسل الرسالة
    username = Column(String, nullable=True)
    full_name = Column(String, nullable=True)
    message_text = Column(Text)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    replied = Column(Boolean, default=False)

class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, unique=True, index=True)
    title = Column(String)
    last_activity = Column(DateTime, default=datetime.datetime.utcnow)

class GroupMessage(Base):
    __tablename__ = "group_messages"
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String, index=True) # Telegram message ID
    user_id = Column(String, index=True)
    group_id = Column(String, index=True)
    text = Column(Text)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    positive_reactions = Column(Integer, default=0)

def init_db():
    Base.metadata.create_all(bind=engine)

def add_or_update_user(telegram_id: str, full_name: str, username: str = None):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == str(telegram_id)).first()
        if user:
            user.full_name = full_name
            user.username = username
            user.is_blocked = False # إعادة تفعيل المستخدم إذا كان محظوراً سابقاً
        else:
            user = User(telegram_id=str(telegram_id), full_name=full_name, username=username)
            db.add(user)
        db.commit()
        db.refresh(user)
    except Exception as e:
        db.rollback()
        print(f"Error adding/updating user: {e}")
    finally:
        db.close()

def get_user(telegram_id: str):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.telegram_id == str(telegram_id)).first()
    finally:
        db.close()

def get_all_active_users():
    db = SessionLocal()
    try:
        # جلب المستخدمين الذين لم يحظروا البوت
        return db.query(User).filter(User.is_blocked == False).all()
    finally:
        db.close()

def set_user_blocked(telegram_id: str, is_blocked: bool):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == str(telegram_id)).first()
        if user:
            user.is_blocked = is_blocked
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error setting user blocked status: {e}")
    finally:
        db.close()

def get_blocked_user_count():
    db = SessionLocal()
    try:
        return db.query(User).filter(User.is_blocked == True).count()
    finally:
        db.close()

def update_user_warnings(telegram_id: str) -> int:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == str(telegram_id)).first()
        if user:
            user.warnings += 1
            db.commit()
            db.refresh(user)
            return user.warnings
        return 0
    except Exception as e:
        db.rollback()
        print(f"Error updating user warnings: {e}")
        return 0
    finally:
        db.close()

def mute_user(telegram_id: str, duration: str):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == str(telegram_id)).first()
        if user:
            if duration == \'day\':
                user.muted_until = datetime.datetime.utcnow() + datetime.timedelta(days=1)
            elif duration == \'week\':
                user.muted_until = datetime.datetime.utcnow() + datetime.timedelta(weeks=1)
            elif duration == \'month\':
                user.muted_until = datetime.datetime.utcnow() + datetime.timedelta(days=30) # تقريبي
            else:
                user.muted_until = None # لإلغاء التقييد أو بدون تقييد
            db.commit()
            db.refresh(user)
    except Exception as e:
        db.rollback()
        print(f"Error muting user: {e}")
    finally:
        db.close()

def is_user_muted(telegram_id: str) -> bool:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == str(telegram_id)).first()
        if user and user.muted_until and user.muted_until > datetime.datetime.utcnow():
            return True
        return False
    finally:
        db.close()

def db_add_item(item_data: dict, model, unique_column: str):
    db = SessionLocal()
    try:
        # التحقق مما إذا كان العنصر موجودًا بالفعل
        existing_item = db.query(model).filter(getattr(model, unique_column) == item_data[unique_column]).first()
        if existing_item:
            # إذا كان موجودًا، قم بتحديثه (خاصة مدة التقييد)
            for key, value in item_data.items():
                setattr(existing_item, key, value)
            db.commit()
            db.refresh(existing_item)
            return True # تم التحديث بنجاح
        else:
            # إذا لم يكن موجودًا، قم بإضافته
            item = model(**item_data)
            db.add(item)
            db.commit()
            db.refresh(item)
            return True
    except Exception as e:
        db.rollback()
        print(f"Error adding item to {model.__tablename__}: {e}")
        return False
    finally:
        db.close()

def db_get_all_items(model, column_name: str):
    db = SessionLocal()
    try:
        # جلب القيم الفريدة من العمود المحدد
        items = db.query(getattr(model, column_name)).distinct().all()
        return [item[0] for item in items] # إرجاع قائمة بالقيم فقط
    finally:
        db.close()

def db_delete_item(item_value: str, model, column_name: str):
    db = SessionLocal()
    try:
        item = db.query(model).filter(getattr(model, column_name) == item_value).first()
        if item:
            db.delete(item)
            db.commit()
            return True
        return False
    except Exception as e:
        db.rollback()
        print(f"Error deleting item from {model.__tablename__}: {e}")
        return False
    finally:
        db.close()

def get_setting(key: str):
    db = SessionLocal()
    try:
        setting = db.query(Setting).filter(Setting.key == key).first()
        return setting.value if setting else None
    finally:
        db.close()

def set_setting(key: str, value: str):
    db = SessionLocal()
    try:
        setting = db.query(Setting).filter(Setting.key == key).first()
        if setting:
            setting.value = value
        else:
            setting = Setting(key=key, value=value)
            db.add(setting)
        db.commit()
        db.refresh(setting)
    except Exception as e:
        db.rollback()
        print(f"Error setting value: {e}")
    finally:
        db.close()

def get_all_auto_replies():
    db = SessionLocal()
    try:
        return db.query(AutoReply).all()
    finally:
        db.close()

def save_private_message(user_id: str, username: str, full_name: str, message_text: str):
    db = SessionLocal()
    try:
        msg = PrivateMessage(user_id=str(user_id), username=username, full_name=full_name, message_text=message_text)
        db.add(msg)
        db.commit()
        db.refresh(msg)
    except Exception as e:
        db.rollback()
        print(f"Error saving private message: {e}")
    finally:
        db.close()

def get_unreplied_private_messages():
    db = SessionLocal()
    try:
        return db.query(PrivateMessage).filter(PrivateMessage.replied == False).order_by(PrivateMessage.timestamp.asc()).all()
    finally:
        db.close()

def set_private_message_replied(message_id: int):
    db = SessionLocal()
    try:
        msg = db.query(PrivateMessage).filter(PrivateMessage.id == message_id).first()
        if msg:
            msg.replied = True
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error setting private message as replied: {e}")
    finally:
        db.close()

def add_or_update_group(telegram_id: str, title: str):
    db = SessionLocal()
    try:
        group = db.query(Group).filter(Group.telegram_id == str(telegram_id)).first()
        if group:
            group.title = title
            group.last_activity = datetime.datetime.utcnow()
        else:
            group = Group(telegram_id=str(telegram_id), title=title)
            db.add(group)
        db.commit()
        db.refresh(group)
    except Exception as e:
        db.rollback()
        print(f"Error adding/updating group: {e}")
    finally:
        db.close()

def get_all_groups():
    db = SessionLocal()
    try:
        return db.query(Group).order_by(Group.title.asc()).all()
    finally:
        db.close()

def save_group_message(message_id: str, user_id: str, group_id: str, text: str):
    db = SessionLocal()
    try:
        msg = GroupMessage(message_id=str(message_id), user_id=str(user_id), group_id=str(group_id), text=text)
        db.add(msg)
        db.commit()
        db.refresh(msg)
    except Exception as e:
        db.rollback()
        print(f"Error saving group message: {e}")
    finally:
        db.close()

def update_message_reactions(message_id: str, group_id: str, positive_reactions: int):
    db = SessionLocal()
    try:
        msg = db.query(GroupMessage).filter(GroupMessage.message_id == str(message_id), GroupMessage.group_id == str(group_id)).first()
        if msg:
            msg.positive_reactions = positive_reactions
            db.commit()
            db.refresh(msg)
    except Exception as e:
        db.rollback()
        print(f"Error updating message reactions: {e}")
    finally:
        db.close()

def get_top_messages_by_reactions(group_id: str, limit: int = 5):
    db = SessionLocal()
    try:
        # جلب الرسائل من آخر 7 أيام فقط
        seven_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
        return db.query(GroupMessage).filter(
            GroupMessage.group_id == str(group_id),
            GroupMessage.timestamp >= seven_days_ago
        ).order_by(GroupMessage.positive_reactions.desc()).limit(limit).all()
    finally:
        db.close()

def increment_user_message_count(telegram_id: str):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == str(telegram_id)).first()
        if user:
            user.message_count += 1
            db.commit()
            db.refresh(user)
    except Exception as e:
        db.rollback()
        print(f"Error incrementing message count for user: {e}")
    finally:
        db.close()

def get_top_active_users(limit: int = 5):
    db = SessionLocal()
    try:
        return db.query(User).order_by(User.message_count.desc()).limit(limit).all()
    finally:
        db.close()
