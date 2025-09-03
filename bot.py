import os
import logging
import re
from datetime import timedelta

# استيراد مكتبة قاعدة البيانات PostgreSQL
import psycopg2
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

# استيراد مكتبة تحميل متغيرات البيئة (مفيدة للاختبار المحلي)
from dotenv import load_dotenv

# --- الإعدادات الأولية ---

# تفعيل تسجيل الأخطاء والملاحظات (مهم جدًا للمراقبة على Railway)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# تحميل المتغيرات من ملف .env (إذا كان موجودًا)
load_dotenv()

# قراءة متغيرات البيئة
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

# التحقق من وجود المتغيرات الأساسية
if not all([TELEGRAM_TOKEN, ADMIN_ID_STR, DATABASE_URL]):
    logger.critical("خطأ فادح: أحد متغيرات البيئة (TELEGRAM_TOKEN, ADMIN_ID, DATABASE_URL) غير موجود.")
    exit()

ADMIN_ID = int(ADMIN_ID_STR)

# --- إدارة قاعدة بيانات PostgreSQL ---

def get_db_connection():
    """إنشاء وإرجاع اتصال بقاعدة البيانات."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except psycopg2.OperationalError as e:
        logger.error(f"لا يمكن الاتصال بقاعدة البيانات: {e}")
        return None

def setup_database():
    """
    إنشاء الجداول الأساسية في قاعدة البيانات عند بدء تشغيل البوت.
    هذه الدالة آمنة للتشغيل عدة مرات.
    """
    conn = get_db_connection()
    if not conn: return

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS auto_replies (
                keyword TEXT PRIMARY KEY,
                reply TEXT NOT NULL
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS banned_words (
                word TEXT PRIMARY KEY,
                duration_minutes INTEGER NOT NULL
            );
        """)
        cur.execute("INSERT INTO settings (key, value) VALUES ('welcome_message', 'أهلاً بك في البوت!') ON CONFLICT (key) DO NOTHING;")
        cur.execute("INSERT INTO auto_replies (keyword, reply) VALUES ('مرحباً', 'أهلاً بك!') ON CONFLICT (keyword) DO NOTHING;")
        cur.execute("INSERT INTO banned_words (word, duration_minutes) VALUES ('ممنوع', 5) ON CONFLICT (word) DO NOTHING;")

    conn.commit()
    conn.close()
    logger.info("تم فحص وإعداد قاعدة البيانات بنجاح.")

# --- دوال مساعدة ---

async def send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, welcome_message: str):
    """دالة مخصصة لإرسال رسالة الترحيب ولوحة التحكم للمشرف."""
    keyboard = [
        [InlineKeyboardButton("⚙️ تعديل رسالة الترحيب", callback_data="edit_welcome")],
        [InlineKeyboardButton("📝 تعديل الردود التلقائية", callback_data="edit_replies")],
        [InlineKeyboardButton("🚫 تعديل الكلمات المحظورة", callback_data="edit_banned")],
        [InlineKeyboardButton("📢 بث رسالة للجميع", callback_data="broadcast")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    # استخدام `effective_message` لضمان وجود رسالة يمكن الرد عليها أو تعديلها
    await update.effective_message.reply_text(
        f"{welcome_message}\n\nأنت المشرف. هذه لوحة التحكم الخاصة بك:",
        reply_markup=reply_markup
    )

# --- أوامر البوت الأساسية ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /start للترحيب بالمستخدمين وإظهار لوحة التحكم للمشرف."""
    user = update.effective_user
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("عذرًا، حدث خطأ في الاتصال بالخدمة. يرجى المحاولة لاحقًا.")
        return

    with conn.cursor() as cur:
        cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING;", (user.id,))
        cur.execute("SELECT value FROM settings WHERE key = 'welcome_message';")
        result = cur.fetchone()
        welcome_message = result[0] if result else "أهلاً بك!"
    
    conn.commit()
    conn.close()
        
    if user.id == ADMIN_ID:
        await send_admin_panel(update, context, welcome_message)
    else:
        await update.message.reply_text(welcome_message)

# --- معالجات الرسائل ---

async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج للرسائل في المجموعات للتحقق من الكلمات المحظورة والردود التلقائية."""
    if not update.message or not update.message.text: return

    message_text = update.message.text.lower()
    chat = update.effective_chat
    user = update.effective_user
    
    conn = get_db_connection()
    if not conn: return

    with conn.cursor() as cur:
        # 1. التحقق من الكلمات المحظورة
        cur.execute("SELECT word, duration_minutes FROM banned_words;")
        banned_words = cur.fetchall()
        for word, duration_minutes in banned_words:
            if re.search(r'\b' + re.escape(word.lower()) + r'\b', message_text):
                try:
                    await update.message.delete()
                    permissions = ChatPermissions(can_send_messages=False)
                    await context.bot.restrict_chat_member(
                        chat_id=chat.id, user_id=user.id, permissions=permissions,
                        until_date=update.message.date + timedelta(minutes=duration_minutes)
                    )
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text=f"⚠️ المستخدم {user.mention_html()} تم تقييده لمدة {duration_minutes} دقيقة لاستخدامه كلمة محظورة.",
                        parse_mode=ParseMode.HTML
                    )
                    conn.close()
                    return
                except BadRequest as e:
                    if "user is an administrator" not in str(e):
                        logger.error(f"خطأ في تقييد المستخدم: {e}")
                except Exception as e:
                    logger.error(f"خطأ غير متوقع في حظر الكلمات: {e}")
                break

        # 2. التحقق من الردود التلقائية
        cur.execute("SELECT keyword, reply FROM auto_replies;")
        auto_replies = cur.fetchall()
        for keyword, reply in auto_replies:
            if keyword.lower() in message_text:
                await update.message.reply_text(reply)
                break
    conn.close()

async def private_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج للرسائل الخاصة: إما تحويلها للمشرف أو معالجتها كأوامر منه."""
    user = update.effective_user
    message = update.message
    if not message or not message.text: return

    # إذا كانت الرسالة من المشرف
    if user.id == ADMIN_ID:
        # إذا كتب المشرف كلمة "يمان"، أظهر له لوحة التحكم
        if message.text.strip().lower() == "يمان":
            conn = get_db_connection()
            if not conn: return
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM settings WHERE key = 'welcome_message';")
                welcome_message = cur.fetchone()[0]
            conn.close()
            await send_admin_panel(update, context, welcome_message)
            return

        # تحقق مما إذا كانت ردًا على رسالة محولة
        if message.reply_to_message and message.reply_to_message.forward_from:
            user_to_reply = message.reply_to_message.forward_from
            try:
                await context.bot.send_message(chat_id=user_to_reply.id, text=f"✉️ رد من الدعم:\n\n{message.text}")
                await update.message.reply_text("✅ تم إرسال ردك بنجاح.")
            except Exception as e:
                await update.message.reply_text(f"❌ لم أتمكن من إرسال الرد. خطأ: {e}")
        return

    # إذا كانت الرسالة من مستخدم عادي، حولها للمشرف
    try:
        await context.bot.forward_message(chat_id=ADMIN_ID, from_chat_id=user.id, message_id=message.message_id)
    except Exception as e:
        logger.error(f"خطأ في تحويل الرسالة من {user.id}: {e}")

async def media_downloader_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحميل الفيديو من الروابط المدعومة."""
    message = update.message
    if not message or not message.text: return

    url = message.text
    supported_sites = ['tiktok', 'instagram', 'facebook', 'youtube']
    
    if not (re.match(r'https?://', url) and any(site in url for site in supported_sites)):
        return

    processing_message = await message.reply_text("⏳ جاري معالجة الرابط...")
    os.makedirs('downloads', exist_ok=True)
    
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'quiet': True, 'noplaylist': True,
        'max_filesize': 50 * 1024 * 1024,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            await context.bot.send_video(
                chat_id=message.chat_id, video=open(filename, 'rb'),
                caption=info.get('title', 'تم التحميل'),
                read_timeout=120, write_timeout=120
            )
            await processing_message.delete()
            os.remove(filename)
    except Exception as e:
        logger.error(f"خطأ في تحميل الفيديو: {e}")
        await processing_message.edit_text("❌ حدث خطأ أثناء التحميل. قد يكون الرابط غير صالح أو حجمه كبير جدًا.")
        # تنظيف الملفات الفاشلة
        if 'info' in locals() and 'id' in info:
            for f in os.listdir('downloads'):
                if info['id'] in f:
                    os.remove(os.path.join('downloads', f))

# --- معالجات الأزرار والمحادثات ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج لجميع الأزرار الداخلية."""
    query = update.callback_query
    await query.answer()

    if query.data == "edit_welcome":
        await query.edit_message_text("أرسل الآن رسالة الترحيب الجديدة. للإلغاء أرسل 'إلغاء'.")
        context.user_data['next_step'] = 'set_welcome'
    elif query.data == "edit_replies":
        await query.edit_message_text("أرسل الكلمة المفتاحية للرد التلقائي. للإلغاء أرسل 'إلغاء'.")
        context.user_data['next_step'] = 'set_reply_keyword'
    # يمكن إضافة المزيد من المنطق هنا للكلمات المحظورة والبث

async def conversation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج لإدارة المحادثات متعددة الخطوات مع المشرف."""
    if update.effective_user.id != ADMIN_ID or 'next_step' not in context.user_data:
        return

    step = context.user_data.pop('next_step', None)
    text = update.message.text

    if text.strip() == 'إلغاء':
        await update.message.reply_text("تم الإلغاء.")
        return

    conn = get_db_connection()
    if not conn: return

    with conn.cursor() as cur:
        if step == 'set_welcome':
            cur.execute("UPDATE settings SET value = %s WHERE key = 'welcome_message';", (text,))
            await update.message.reply_text("✅ تم تحديث رسالة الترحيب بنجاح.")
        
        elif step == 'set_reply_keyword':
            context.user_data['keyword'] = text
            context.user_data['next_step'] = 'set_reply_text'
            await update.message.reply_text(f"الآن أرسل نص الرد لكلمة '{text}'.")
        
        elif step == 'set_reply_text':
            keyword = context.user_data.pop('keyword')
            cur.execute(
                "INSERT INTO auto_replies (keyword, reply) VALUES (%s, %s) ON CONFLICT (keyword) DO UPDATE SET reply = EXCLUDED.reply;",
                (keyword, text)
            )
            await update.message.reply_text("✅ تم حفظ الرد التلقائي بنجاح.")

    conn.commit()
    conn.close()

# --- الدالة الرئيسية لتشغيل البوت ---

def main():
    """الدالة الرئيسية لإعداد وتشغيل البوت."""
    setup_database()
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # إضافة المعالجات بالترتيب الصحيح للأولوية
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # مجموعة 0: معالج المحادثات مع المشرف (أعلى أولوية للرسائل النصية الخاصة)
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.User(ADMIN_ID) & filters.TEXT & ~filters.COMMAND, conversation_handler), group=0)

    # مجموعة 1: معالج تحميل الوسائط (يجب أن يأتي قبل المعالجات العامة للنصوص)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, media_downloader_handler), group=1)
    
    # مجموعة 2: معالج الرسائل في المجموعات
    application.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, group_message_handler), group=2)

    # مجموعة 3: معالج الرسائل الخاصة (للتواصل مع المشرف وكلمة "يمان")
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, private_message_handler), group=3)

    logger.info("البوت قيد التشغيل...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
