import os
import logging
import re
from datetime import timedelta
import psycopg2  # ### تعديل ###: استيراد مكتبة قاعدة البيانات
from psycopg2 import sql

# استيراد المكتبات اللازمة من python-telegram-bot
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest

# استيراد مكتبة تحميل الفيديوهات
import yt_dlp

# استيراد مكتبة تحميل متغيرات البيئة
from dotenv import load_dotenv

# تفعيل تسجيل الأخطاء
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# تحميل المتغيرات من ملف .env
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
# ### تعديل ###: قراءة رابط الاتصال بقاعدة البيانات الذي توفره Railway
DATABASE_URL = os.getenv("DATABASE_URL")

# --- ### تعديل ###: إدارة قاعدة بيانات PostgreSQL ---

def get_db_connection():
    """إنشاء اتصال بقاعدة البيانات."""
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def setup_database():
    """إنشاء الجداول في قاعدة البيانات إذا لم تكن موجودة."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # جدول لتخزين الإعدادات العامة (رسالة الترحيب)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    
    # جدول للمستخدمين (للبث)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY
        );
    """)
    
    # جدول للردود التلقائية
    cur.execute("""
        CREATE TABLE IF NOT EXISTS auto_replies (
            id SERIAL PRIMARY KEY,
            keyword TEXT NOT NULL UNIQUE,
            reply TEXT NOT NULL
        );
    """)
    
    # جدول للكلمات المحظورة
    cur.execute("""
        CREATE TABLE IF NOT EXISTS banned_words (
            id SERIAL PRIMARY KEY,
            word TEXT NOT NULL UNIQUE,
            duration_minutes INTEGER NOT NULL
        );
    """)
    
    # إضافة قيم افتراضية إذا كانت الجداول فارغة
    cur.execute("INSERT INTO settings (key, value) VALUES ('welcome_message', 'أهلاً بك في البوت!') ON CONFLICT (key) DO NOTHING;")
    cur.execute("INSERT INTO auto_replies (keyword, reply) VALUES ('مرحباً', 'أهلاً بك!') ON CONFLICT (keyword) DO NOTHING;")
    cur.execute("INSERT INTO banned_words (word, duration_minutes) VALUES ('ممنوع', 5) ON CONFLICT (word) DO NOTHING;")

    conn.commit()
    cur.close()
    conn.close()
    logger.info("تم فحص وإعداد قاعدة البيانات بنجاح.")

# --- أوامر البوت الأساسية ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /start للترحيب بالمستخدمين الجدد."""
    user = update.effective_user
    
    # ### تعديل ###: إضافة المستخدم إلى قاعدة البيانات
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING;", (user.id,))
    conn.commit()
    
    # ### تعديل ###: جلب رسالة الترحيب من قاعدة البيانات
    cur.execute("SELECT value FROM settings WHERE key = 'welcome_message';")
    welcome_message = cur.fetchone()[0]
    cur.close()
    conn.close()
        
    if user.id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("⚙️ تعديل رسالة الترحيب", callback_data="edit_welcome")],
            [InlineKeyboardButton("📝 تعديل الردود التلقائية", callback_data="edit_replies")],
            [InlineKeyboardButton("🚫 تعديل الكلمات المحظورة", callback_data="edit_banned")],
            [InlineKeyboardButton("📢 بث رسالة للجميع", callback_data="broadcast")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"{welcome_message}\n\nأنت المشرف. هذه لوحة التحكم الخاصة بك:", reply_markup=reply_markup)
    else:
        await update.message.reply_text(welcome_message)

# --- ميزات إدارة المجموعات ---

async def auto_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الرد التلقائي على الكلمات المحددة."""
    message_text = update.message.text.lower()
    
    # ### تعديل ###: جلب الردود من قاعدة البيانات
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT keyword, reply FROM auto_replies;")
    auto_replies = cur.fetchall()
    cur.close()
    conn.close()
    
    for keyword, reply in auto_replies:
        if keyword.lower() in message_text:
            await update.message.reply_text(reply)
            break

async def banned_words_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حظر الكلمات وتقييد المستخدم."""
    message = update.message
    user = update.effective_user
    chat = update.effective_chat
    
    # ### تعديل ###: جلب الكلمات المحظورة من قاعدة البيانات
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT word, duration_minutes FROM banned_words;")
    banned_words = cur.fetchall()
    cur.close()
    conn.close()

    for word, duration_minutes in banned_words:
        if re.search(r'\b' + re.escape(word.lower()) + r'\b', message.text.lower()):
            try:
                await message.delete()
                permissions = ChatPermissions(can_send_messages=False)
                await context.bot.restrict_chat_member(
                    chat_id=chat.id,
                    user_id=user.id,
                    permissions=permissions,
                    until_date=message.date + timedelta(minutes=duration_minutes)
                )
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=f"⚠️ المستخدم {user.mention_html()} تم تقييده لمدة {duration_minutes} دقيقة لاستخدامه كلمة محظورة.",
                    parse_mode=ParseMode.HTML
                )
            except BadRequest as e:
                logger.error(f"خطأ في تقييد المستخدم: {e}")
                await context.bot.send_message(chat.id, "لا يمكنني تقييد المستخدم. تأكد من أنني مشرف ولدي صلاحية حظر المستخدمين.")
            break

# --- (بقية الكود يبقى كما هو إلى حد كبير) ---
# ... (forward_to_admin, reply_to_user, media_downloader_handler, button_handler) ...

# --- ### تعديل ###: تعديل دالة المحادثات لتحديث قاعدة البيانات ---
async def conversation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج لإدارة المحادثات متعددة الخطوات (مثل تعديل الإعدادات)."""
    if 'next_step' in context.user_data:
        step = context.user_data['next_step']
        
        if step == 'set_welcome':
            new_welcome_message = update.message.text
            
            # ### تعديل ###: تحديث رسالة الترحيب في قاعدة البيانات
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE settings SET value = %s WHERE key = 'welcome_message';", (new_welcome_message,))
            conn.commit()
            cur.close()
            conn.close()
            
            await update.message.reply_text("✅ تم تحديث رسالة الترحيب بنجاح.")
            del context.user_data['next_step'] # إنهاء الخطوة

# --- الدالة الرئيسية لتشغيل البوت ---

def main():
    """الدالة الرئيسية لتشغيل البوت."""
    if not TELEGRAM_TOKEN or not ADMIN_ID or not DATABASE_URL:
        logger.error("خطأ: تأكد من وجود المتغيرات TELEGRAM_TOKEN, ADMIN_ID, DATABASE_URL.")
        return

    # ### تعديل ###: استدعاء دالة إعداد قاعدة البيانات عند البدء
    setup_database()

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # ... (بقية تعريفات المعالجات تبقى كما هي) ...
    application.add_handler(CommandHandler("start", start_command))
    # ... إلخ ...
    
    # (الكود المتبقي من دالة main يبقى كما هو في المثال السابق)
    # ...
    # ...
    
    logger.info("البوت قيد التشغيل...")
    application.run_polling()

# (الكود المتبقي من الملف يبقى كما هو)
# ...
# ...

if __name__ == "__main__":
    main()
