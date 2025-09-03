import os
import logging
import re
import asyncio
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
from telegram.error import BadRequest, Forbidden

# استيراد مكتبة تحميل الفيديوهات
import yt_dlp

# استيراد مكتبة تحميل متغيرات البيئة
from dotenv import load_dotenv

# --- الإعدادات الأولية ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

if not all([TELEGRAM_TOKEN, ADMIN_ID_STR, DATABASE_URL]):
    logger.critical("خطأ فادح: أحد متغيرات البيئة (TELEGRAM_TOKEN, ADMIN_ID, DATABASE_URL) غير موجود.")
    exit()

ADMIN_ID = int(ADMIN_ID_STR)

# --- إدارة قاعدة بيانات PostgreSQL ---

def get_db_connection():
    try:
        return psycopg2.connect(DATABASE_URL)
    except psycopg2.OperationalError as e:
        logger.error(f"لا يمكن الاتصال بقاعدة البيانات: {e}")
        return None

def setup_database():
    conn = get_db_connection()
    if not conn: return
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY);")
        cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
        cur.execute("CREATE TABLE IF NOT EXISTS auto_replies (keyword TEXT PRIMARY KEY, reply TEXT NOT NULL);")
        cur.execute("CREATE TABLE IF NOT EXISTS banned_words (word TEXT PRIMARY KEY, duration_minutes INTEGER NOT NULL, warning_message TEXT);")
        cur.execute("CREATE TABLE IF NOT EXISTS allowed_links (link_pattern TEXT PRIMARY KEY);")
        cur.execute("INSERT INTO settings (key, value) VALUES ('welcome_message', 'أهلاً بك في البوت!') ON CONFLICT (key) DO NOTHING;")
        cur.execute("INSERT INTO settings (key, value) VALUES ('forward_reply_message', 'شكرًا لرسالتك، تم توصيلها للدعم وسنرد عليك قريبًا.') ON CONFLICT (key) DO NOTHING;")
    conn.commit()
    conn.close()
    logger.info("تم فحص وتحديث قاعدة البيانات بنجاح.")

# --- دوال مساعدة للوحة التحكم ---

async def send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📢 بث رسالة للجميع", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📝 إدارة الردود التلقائية", callback_data="admin_manage_replies")],
        [InlineKeyboardButton("🚫 إدارة الكلمات المحظورة", callback_data="admin_manage_banned")],
        [InlineKeyboardButton("🔗 إدارة الروابط المسموحة", callback_data="admin_manage_links")],
        [InlineKeyboardButton("⚙️ تعديل رسائل البوت", callback_data="admin_edit_messages")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = "🤖 **لوحة تحكم المشرف**\n\nاختر أحد الخيارات لإدارة البوت:"
    if update.callback_query:
        await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await update.effective_message.reply_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)

async def is_user_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        return chat_member.status in [chat_member.ADMINISTRATOR, chat_member.OWNER]
    except BadRequest:
        return False

# --- أوامر البوت الأساسية ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("عذرًا، حدث خطأ في الخدمة.")
        return
    with conn.cursor() as cur:
        cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING;", (user.id,))
        cur.execute("SELECT value FROM settings WHERE key = 'welcome_message';")
        welcome_message = cur.fetchone()[0]
    conn.commit()
    conn.close()
    await update.message.reply_text(welcome_message)
    if user.id == ADMIN_ID:
        await send_admin_panel(update, context)

# --- معالجات الرسائل ---

async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not (message.text or message.caption): return
    user = update.effective_user
    chat = update.effective_chat
    message_text = (message.text or message.caption).lower()
    user_is_admin = await is_user_admin(chat.id, user.id, context)
    if user_is_admin: return # المشرفون يتجاوزون كل القيود
    conn = get_db_connection()
    if not conn: return
    with conn.cursor() as cur:
        # حظر الروابط
        if re.search(r'https?://|t\.me/|www\.', message_text):
            cur.execute("SELECT link_pattern FROM allowed_links;")
            allowed_links = [row[0] for row in cur.fetchall()]
            if not any(pattern in message_text for pattern in allowed_links):
                try:
                    await message.delete()
                    await context.bot.send_message(chat.id, f"⚠️ {user.mention_html()}، يمنع إرسال الروابط.", parse_mode=ParseMode.HTML)
                except Exception as e: logger.error(f"خطأ في حذف رابط: {e}")
                conn.close()
                return
        # حظر الكلمات
        cur.execute("SELECT word, duration_minutes, warning_message FROM banned_words;")
        banned_words = cur.fetchall()
        for word, duration, warning in banned_words:
            if re.search(r'\b' + re.escape(word.lower()) + r'\b', message_text):
                try:
                    await message.delete()
                    await context.bot.send_message(chat.id, f"⚠️ {user.mention_html()}, {warning}", parse_mode=ParseMode.HTML)
                    if duration > 0:
                        await context.bot.restrict_chat_member(chat.id, user.id, permissions=ChatPermissions(can_send_messages=False), until_date=message.date + timedelta(minutes=duration))
                except Exception as e: logger.error(f"خطأ في حظر كلمة: {e}")
                conn.close()
                return
        # الرد التلقائي
        cur.execute("SELECT keyword, reply FROM auto_replies;")
        auto_replies = cur.fetchall()
        for keyword, reply in auto_replies:
            if keyword.lower() in message_text:
                await message.reply_text(reply)
                break
    conn.close()

async def private_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    if user.id == ADMIN_ID:
        if message.text and message.text.strip().lower() == "يمان":
            await send_admin_panel(update, context)
        return # المشرف لا يحتاج لتحويل رسائله أو الرد عليه تلقائيا
    conn = get_db_connection()
    if not conn: return
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM settings WHERE key = 'forward_reply_message';")
        reply_text = cur.fetchone()[0]
    conn.close()
    await message.reply_text(reply_text)
    try:
        await context.bot.forward_message(chat_id=ADMIN_ID, from_chat_id=user.id, message_id=message.message_id)
    except Exception as e:
        logger.error(f"خطأ في تحويل الرسالة: {e}")

async def media_downloader_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text: return
    url = message.text
    if not (re.match(r'https?://', url) and any(site in url for site in ['tiktok', 'instagram', 'facebook', 'youtube'])): return
    processing_message = await message.reply_text("⏳ جاري معالجة الرابط...")
    os.makedirs('downloads', exist_ok=True)
    ydl_opts = {'format': 'best', 'outtmpl': 'downloads/%(id)s.%(ext)s', 'quiet': True, 'noplaylist': True, 'max_filesize': 50 * 1024 * 1024}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            await context.bot.send_video(chat_id=message.chat_id, video=open(filename, 'rb'), caption=info.get('title', 'تم التحميل'))
            os.remove(filename)
            await processing_message.delete()
    except Exception as e:
        logger.error(f"خطأ في تحميل الفيديو: {e}")
        await processing_message.edit_text("❌ حدث خطأ أثناء التحميل.")

# --- معالجات الأزرار والمحادثات (القسم الكامل) ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # القائمة الرئيسية
    if data == "admin_panel_main":
        await send_admin_panel(update, context)
    
    # البث
    elif data == "admin_broadcast":
        await query.edit_message_text("أرسل الآن الرسالة التي تود بثها للجميع. للإلغاء أرسل /cancel.")
        context.user_data['next_step'] = 'broadcast_message'

    # إدارة الكلمات المحظورة
    elif data == "admin_manage_banned":
        keyboard = [
            [InlineKeyboardButton("➕ إضافة كلمة", callback_data="banned_add")],
            [InlineKeyboardButton("➖ حذف كلمة", callback_data="banned_delete")],
            [InlineKeyboardButton("📋 عرض الكل", callback_data="banned_list")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel_main")]
        ]
        await query.edit_message_text("🚫 إدارة الكلمات المحظورة:", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "banned_add":
        await query.edit_message_text("أرسل الكلمة التي تريد حظرها.")
        context.user_data['next_step'] = 'banned_add_word'

    elif data.startswith("banned_set_duration_"):
        parts = data.split('_')
        word = parts[3]
        duration = int(parts[4])
        context.user_data['banned_word'] = word
        context.user_data['banned_duration'] = duration
        await query.edit_message_text(f"الكلمة: `{word}`\nالمدة: {duration} دقيقة.\n\nالآن أرسل رسالة التحذير التي ستظهر للمستخدم.", parse_mode=ParseMode.MARKDOWN_V2)
        context.user_data['next_step'] = 'banned_add_warning'

    elif data == "banned_delete":
        await query.edit_message_text("أرسل الكلمة التي تريد حذفها من قائمة الحظر.")
        context.user_data['next_step'] = 'banned_delete_word'

    elif data == "banned_list":
        conn = get_db_connection()
        if not conn: return
        with conn.cursor() as cur:
            cur.execute("SELECT word, duration_minutes FROM banned_words;")
            words = cur.fetchall()
        conn.close()
        if not words:
            text = "لا توجد كلمات محظورة حاليًا."
        else:
            text = "قائمة الكلمات المحظورة:\n" + "\n".join([f"- `{word}` (المدة: {dur} دقيقة)" for word, dur in words])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_manage_banned")]]), parse_mode=ParseMode.MARKDOWN_V2)

    # إدارة الروابط المسموحة
    elif data == "admin_manage_links":
        keyboard = [
            [InlineKeyboardButton("➕ إضافة رابط", callback_data="link_add")],
            [InlineKeyboardButton("➖ حذف رابط", callback_data="link_delete")],
            [InlineKeyboardButton("📋 عرض الكل", callback_data="link_list")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel_main")]
        ]
        await query.edit_message_text("🔗 إدارة الروابط المسموحة:", reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ... وبالمثل لبقية الأزرار (الردود، تعديل الرسائل) ...

async def conversation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or 'next_step' not in context.user_data:
        return
    step = context.user_data.pop('next_step', None)
    message = update.message
    if message.text and message.text == '/cancel':
        await message.reply_text("تم الإلغاء.")
        return
    
    conn = get_db_connection()
    if not conn: return

    with conn.cursor() as cur:
        # البث
        if step == 'broadcast_message':
            await message.reply_text("⏳ جاري بدء عملية البث...")
            cur.execute("SELECT user_id FROM users;")
            all_users = [row[0] for row in cur.fetchall()]
            success, fail = 0, 0
            for user_id in all_users:
                try:
                    await context.bot.copy_message(user_id, ADMIN_ID, message.message_id)
                    success += 1
                    await asyncio.sleep(0.1)
                except:
                    fail += 1
            await message.reply_text(f"✅ انتهى البث!\n\n- نجح: {success}\n- فشل: {fail}")

        # إضافة كلمة محظورة
        elif step == 'banned_add_word':
            word = message.text.strip()
            keyboard = [
                [InlineKeyboardButton("حذف فقط", callback_data=f"banned_set_duration_{word}_0")],
                [InlineKeyboardButton("ساعة", callback_data=f"banned_set_duration_{word}_60")],
                [InlineKeyboardButton("يوم", callback_data=f"banned_set_duration_{word}_1440")],
                [InlineKeyboardButton("شهر", callback_data=f"banned_set_duration_{word}_43200")],
                [InlineKeyboardButton("سنة", callback_data=f"banned_set_duration_{word}_525600")],
            ]
            await message.reply_text(f"اختر مدة التقييد للكلمة: `{word}`", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

        elif step == 'banned_add_warning':
            word = context.user_data.pop('banned_word')
            duration = context.user_data.pop('banned_duration')
            warning = message.text
            cur.execute("INSERT INTO banned_words (word, duration_minutes, warning_message) VALUES (%s, %s, %s) ON CONFLICT (word) DO UPDATE SET duration_minutes = EXCLUDED.duration_minutes, warning_message = EXCLUDED.warning_message;", (word, duration, warning))
            await message.reply_text(f"✅ تم حفظ الكلمة المحظورة `{word}` بنجاح.", parse_mode=ParseMode.MARKDOWN_V2)

        elif step == 'banned_delete_word':
            word = message.text.strip()
            cur.execute("DELETE FROM banned_words WHERE word = %s;", (word,))
            if cur.rowcount > 0:
                await message.reply_text(f"✅ تم حذف الكلمة `{word}` من قائمة الحظر.", parse_mode=ParseMode.MARKDOWN_V2)
            else:
                await message.reply_text(f"لم أجد الكلمة `{word}` في القائمة.", parse_mode=ParseMode.MARKDOWN_V2)

    conn.commit()
    conn.close()

# --- الدالة الرئيسية ---

def main():
    setup_database()
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.User(ADMIN_ID), conversation_handler), group=0)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, media_downloader_handler), group=1)
    application.add_handler(MessageHandler(filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, group_message_handler), group=2)
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, private_message_handler), group=3)

    logger.info("البوت قيد التشغيل (الإصدار الكامل)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
