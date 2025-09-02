import os
import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String)
    text = Column(String)
    sentiment = Column(String)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

class AutoReply(Base):
    __tablename__ = "auto_replies"
    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String, unique=True, nullable=False)
    reply_text = Column(Text, nullable=False)

def init_db():
    Base.metadata.create_all(bind=engine)

def save_message(user_id, text, sentiment):
    db = SessionLocal()
    try:
        db_message = Message(user_id=str(user_id), text=text, sentiment=sentiment)
        db.add(db_message)
        db.commit()
    finally:
        db.close()

def add_or_update_reply(keyword: str, reply_text: str):
    db = SessionLocal()
    existing_reply = db.query(AutoReply).filter(AutoReply.keyword == keyword.lower()).first()
    if existing_reply:
        existing_reply.reply_text = reply_text
    else:
        new_reply = AutoReply(keyword=keyword.lower(), reply_text=reply_text)
        db.add(new_reply)
    db.commit()
    db.close()

def get_all_replies():
    db = SessionLocal()
    replies = db.query(AutoReply).all()
    db.close()
    return replies

def delete_reply(keyword: str):
    db = SessionLocal()
    reply_to_delete = db.query(AutoReply).filter(AutoReply.keyword == keyword.lower()).first()
    if reply_to_delete:
        db.delete(reply_to_delete)
        db.commit()
        db.close()
        return True
    db.close()
    return False

def get_reply_for_keyword(text: str):
    db = SessionLocal()
    reply = db.query(AutoReply).filter(AutoReply.keyword == text.lower()).first()
    db.close()
    if reply:
        return reply.reply_text
    return None

init_db()

