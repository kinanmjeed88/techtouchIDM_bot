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

# استيراد مكتبة yt-dlp
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
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY);")
            cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
            cur.execute("CREATE TABLE IF NOT EXISTS auto_replies (keyword TEXT PRIMARY KEY, reply TEXT NOT NULL);")
            cur.execute("CREATE TABLE IF NOT EXISTS banned_words (word TEXT PRIMARY KEY, duration_minutes INTEGER NOT NULL, warning_message TEXT);")
            cur.execute("CREATE TABLE IF NOT EXISTS allowed_links (link_pattern TEXT PRIMARY KEY);")
            cur.execute("INSERT INTO settings (key, value) VALUES ('welcome_message', 'أهلاً بك في البوت!') ON CONFLICT (key) DO NOTHING;")
            cur.execute("INSERT INTO settings (key, value) VALUES ('forward_reply_message', 'شكرًا لرسالتك، تم توصيلها للدعم وسنرد عليك قريبًا.') ON CONFLICT (key) DO NOTHING;")
        conn.commit()
        logger.info("تم فحص وتحديث قاعدة البيانات بنجاح.")
    finally:
        if conn:
            conn.close()

# --- دوال مساعدة ---

async def send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📢 بث رسالة للجميع", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📝 إدارة الردود التلقائية", callback_data="admin_manage_replies")],
        [InlineKeyboardButton("🚫 إدارة الكلمات المحظورة", callback_data="admin_manage_banned")],
        [InlineKeyboardButton("🔗 إدارة الروابط المسموحة", callback_data="admin_manage_links")],
        [InlineKeyboardButton("⚙️ تعديل رسائل البوت", callback_data="admin_edit_messages")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = "🤖 لوحة تحكم المشرف\n\nاختر أحد الخيارات لإدارة البوت:"
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup)
        except BadRequest:
            pass
    else:
        await update.effective_message.reply_text(message_text, reply_markup=reply_markup)

async def is_user_group_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if user_id == ADMIN_ID:
        return True
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        return chat_member.status in [chat_member.ADMINISTRATOR, chat_member.OWNER]
    except (BadRequest, Forbidden):
        return False

# --- أوامر البوت الأساسية ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("عذرًا، حدث خطأ في الخدمة.")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING;", (user.id,))
            cur.execute("SELECT value FROM settings WHERE key = 'welcome_message';")
            welcome_message = cur.fetchone()[0]
        conn.commit()
        await update.message.reply_text(welcome_message)
        if user.id == ADMIN_ID:
            await send_admin_panel(update, context)
    finally:
        if conn:
            conn.close()

# --- معالجات الرسائل ---

async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not (message.text or message.caption): return
    
    user = update.effective_user
    chat = update.effective_chat
    message_text = (message.text or message.caption).lower()
    
    user_is_admin = await is_user_group_admin(chat.id, user.id, context)

    conn = get_db_connection()
    if not conn: return
    
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING;", (user.id,))
            conn.commit()

            if not user_is_admin and re.search(r'https?://|t\.me/|www\.', message_text):
                cur.execute("SELECT link_pattern FROM allowed_links;")
                allowed_links = [row[0] for row in cur.fetchall()]
                if not any(pattern in message_text for pattern in allowed_links):
                    try:
                        await message.delete()
                        await context.bot.send_message(chat.id, f"⚠️ {user.mention_html()}، يمنع إرسال الروابط.", parse_mode=ParseMode.HTML)
                    except Exception as e: 
                        logger.error(f"خطأ في حذف رابط: {e}")
                    return

            cur.execute("SELECT word, duration_minutes, warning_message FROM banned_words;")
            banned_words = cur.fetchall()
            for word, duration, warning in banned_words:
                if re.search(r'\b' + re.escape(word.lower()) + r'\b', message_text):
                    try:
                        await message.delete()
                        final_warning = warning.replace("{user}", user.mention_html())
                        await context.bot.send_message(chat.id, final_warning, parse_mode=ParseMode.HTML)
                        if duration > 0:
                            await context.bot.restrict_chat_member(chat.id, user.id, permissions=ChatPermissions(can_send_messages=False), until_date=message.date + timedelta(minutes=duration))
                    except Exception as e: 
                        logger.error(f"خطأ في حظر كلمة: {e}")
                    return

            cur.execute("SELECT keyword, reply FROM auto_replies;")
            auto_replies = cur.fetchall()
            for keyword, reply in auto_replies:
                if keyword.lower() in message_text:
                    await message.reply_text(reply)
                    break
    finally:
        if conn:
            conn.close()

async def private_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    
    conn = get_db_connection()
    if not conn: return
    
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING;", (user.id,))
            conn.commit()

            if user.id == ADMIN_ID:
                if message.text and message.text.strip().lower() == "يمان":
                    await send_admin_panel(update, context)
                return

            cur.execute("SELECT value FROM settings WHERE key = 'forward_reply_message';")
            reply_text = cur.fetchone()[0]
        
        await message.reply_text(reply_text)
        
        keyboard = [[InlineKeyboardButton("✍️ رد على الرسالة", callback_data=f"admin_reply_to_{user.id}")]]
        await context.bot.forward_message(chat_id=ADMIN_ID, from_chat_id=user.id, message_id=message.message_id)
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"👆 رسالة من {user.full_name} ({user.id})", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"خطأ في معالجة الرسالة الخاصة: {e}")
    finally:
        if conn:
            conn.close()

async def media_downloader_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text: return
    url = message.text.strip()
    
    if not re.match(r'https?://', url):
        return

    processing_message = await message.reply_text("⏳ جاري معالجة الرابط...")
    
    download_folder = "downloads"
    os.makedirs(download_folder, exist_ok=True)
    
    ydl_opts = {
        'format': 'best',
        'outtmpl': os.path.join(download_folder, '%(id)s.%(ext)s'),
        'quiet': True,
        'noplaylist': True,
        'max_filesize': 50 * 1024 * 1024,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            await context.bot.send_video(chat_id=message.chat_id, video=open(filename, 'rb'), caption=info.get('title', '✅ تم التحميل'))
            
            os.remove(filename)
            await processing_message.delete()

    except Exception as e:
        logger.error(f"خطأ في تحميل الفيديو باستخدام yt-dlp: {e}")
        await processing_message.edit_text("❌ حدث خطأ أثناء التحميل. قد يكون الفيديو خاصًا، محذوفًا، أو من منصة غير مدعومة حاليًا.")
        for f in os.listdir(download_folder):
            try:
                os.remove(os.path.join(download_folder, f))
            except OSError:
                pass

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    conn = get_db_connection()
    if not conn: return
    
    try:
        with conn.cursor() as cur:
            if data == "admin_panel_main": await send_admin_panel(update, context)
            elif data == "admin_broadcast":
                await query.edit_message_text("أرسل الآن الرسالة التي تود بثها للجميع (نص، صورة، فيديو...). للإلغاء أرسل /cancel.")
                context.user_data['next_step'] = 'broadcast_message'
            elif data.startswith("admin_reply_to_"):
                user_id = data.split('_')[3]
                context.user_data['user_to_reply'] = user_id
                await query.edit_message_text(f"أنت الآن ترد على المستخدم {user_id}. أرسل رسالتك.")
                context.user_data['next_step'] = 'reply_to_user_message'
            elif data == "admin_manage_banned":
                kb = [[InlineKeyboardButton("➕ إضافة كلمة", callback_data="banned_add")], [InlineKeyboardButton("➖ حذف كلمة", callback_data="banned_delete")], [InlineKeyboardButton("📋 عرض الكل", callback_data="banned_list")], [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel_main")]]
                await query.edit_message_text("🚫 إدارة الكلمات المحظورة:", reply_markup=InlineKeyboardMarkup(kb))
            elif data == "banned_add":
                await query.edit_message_text("أرسل الكلمة التي تريد حظرها.")
                context.user_data['next_step'] = 'banned_add_word'
            elif data.startswith("banned_set_duration_"):
                parts = data.split('_')
                word, duration = parts[3], int(parts[4])
                context.user_data.update({'banned_word': word, 'banned_duration': duration, 'next_step': 'banned_add_warning'})
                await query.edit_message_text(f"الكلمة: {word}\nالمدة: {duration} دقيقة.\n\nالآن أرسل رسالة التحذير.")
            elif data == "banned_delete":
                await query.edit_message_text("أرسل الكلمة التي تريد حذفها من الحظر.")
                context.user_data['next_step'] = 'banned_delete_word'
            elif data == "banned_list":
                cur.execute("SELECT word, duration_minutes FROM banned_words;")
                words = cur.fetchall()
                text = "قائمة الكلمات المحظورة:\n" + "\n".join([f"- {w} ({d} د)" for w, d in words]) if words else "لا توجد كلمات محظورة."
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_manage_banned")]]))
            elif data == "admin_manage_replies":
                kb = [[InlineKeyboardButton("➕ إضافة رد", callback_data="reply_add")], [InlineKeyboardButton("➖ حذف رد", callback_data="reply_delete")], [InlineKeyboardButton("📋 عرض الكل", callback_data="reply_list")], [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel_main")]]
                await query.edit_message_text("📝 إدارة الردود التلقائية:", reply_markup=InlineKeyboardMarkup(kb))
            elif data == "reply_add":
                await query.edit_message_text("أرسل الكلمة المفتاحية للرد الجديد.")
                context.user_data['next_step'] = 'reply_add_keyword'
            elif data == "reply_delete":
                await query.edit_message_text("أرسل الكلمة المفتاحية للرد الذي تريد حذفه.")
                context.user_data['next_step'] = 'reply_delete_keyword'
            elif data == "reply_list":
                cur.execute("SELECT keyword FROM auto_replies;")
                replies = cur.fetchall()
                text = "قائمة الردود التلقائية:\n" + "\n".join([f"- {r[0]}" for r in replies]) if replies else "لا توجد ردود تلقائية."
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_manage_replies")]]))
            elif data == "admin_manage_links":
                kb = [[InlineKeyboardButton("➕ إضافة رابط", callback_data="link_add")], [InlineKeyboardButton("➖ حذف رابط", callback_data="link_delete")], [InlineKeyboardButton("📋 عرض الكل", callback_data="link_list")], [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel_main")]]
                await query.edit_message_text("🔗 إدارة الروابط المسموحة:", reply_markup=InlineKeyboardMarkup(kb))
            elif data == "link_add":
                await query.edit_message_text("أرسل جزءًا من الرابط للسماح به (مثلاً: youtube.com).")
                context.user_data['next_step'] = 'link_add_pattern'
            elif data == "link_delete":
                await query.edit_message_text("أرسل جزء الرابط الذي تريد حذفه.")
                context.user_data['next_step'] = 'link_delete_pattern'
            elif data == "link_list":
                cur.execute("SELECT link_pattern FROM allowed_links;")
                links = cur.fetchall()
                text = "قائمة الروابط المسموحة:\n" + "\n".join([f"- {l[0]}" for l in links]) if links else "لا توجد روابط مسموحة."
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_manage_links")]]))
            elif data == "admin_edit_messages":
                kb = [[InlineKeyboardButton("تعديل رسالة الترحيب", callback_data="msg_edit_welcome")], [InlineKeyboardButton("تعديل رسالة الرد على التواصل", callback_data="msg_edit_forward")], [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel_main")]]
                await query.edit_message_text("⚙️ تعديل رسائل البوت:", reply_markup=InlineKeyboardMarkup(kb))
            elif data == "msg_edit_welcome":
                await query.edit_message_text("أرسل رسالة الترحيب الجديدة.")
                context.user_data['next_step'] = 'msg_set_welcome'
            elif data == "msg_edit_forward":
                await query.edit_message_text("أرسل رسالة الرد التلقائي الجديدة عند التواصل مع البوت.")
                context.user_data['next_step'] = 'msg_set_forward'
    finally:
        if conn:
            conn.close()

async def conversation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or 'next_step' not in context.user_data: return
    step = context.user_data.pop('next_step', None)
    message = update.message
    if message.text and message.text == '/cancel':
        await message.reply_text("تم الإلغاء."); return
    
    conn = get_db_connection()
    if not conn: return
    
    try:
        with conn.cursor() as cur:
            if step == 'broadcast_message':
                await message.reply_text("⏳ جاري بدء البث...")
                cur.execute("SELECT user_id FROM users;")
                users = [r[0] for r in cur.fetchall()]
                s, f = 0, 0
                
                text = message.text or message.caption
                entities = message.entities or message.caption_entities
                photo = message.photo[-1].file_id if message.photo else None
                video = message.video.file_id if message.video else None
                
                for uid in users:
                    try:
                        if photo:
                            await context.bot.send_photo(uid, photo, caption=text, caption_entities=entities)
                        elif video:
                            await context.bot.send_video(uid, video, caption=text, caption_entities=entities)
                        elif text:
                            await context.bot.send_message(uid, text, entities=entities)
                        s += 1
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        logger.error(f"فشل البث للمستخدم {uid}: {e}")
                        f += 1
                await message.reply_text(f"✅ انتهى البث!\nنجح: {s}, فشل: {f}")

            elif step == 'reply_to_user_message':
                uid = context.user_data.pop('user_to_reply')
                try: 
                    await context.bot.copy_message(uid, ADMIN_ID, message.message_id)
                    await message.reply_text("✅ تم إرسال ردك بنجاح.")
                except Exception as e: 
                    await message.reply_text(f"❌ فشل إرسال الرد: {e}")
            elif step == 'banned_add_word':
                word = message.text.strip()
                kb = [[InlineKeyboardButton("حذف فقط", callback_data=f"banned_set_duration_{word}_0"), InlineKeyboardButton("ساعة", callback_data=f"banned_set_duration_{word}_60")], [InlineKeyboardButton("يوم", callback_data=f"banned_set_duration_{word}_1440"), InlineKeyboardButton("شهر", callback_data=f"banned_set_duration_{word}_43200")], [InlineKeyboardButton("سنة", callback_data=f"banned_set_duration_{word}_525600")]]
                await message.reply_text(f"اختر مدة التقييد للكلمة: {word}", reply_markup=InlineKeyboardMarkup(kb))
            elif step == 'banned_add_warning':
                word, dur, warn = context.user_data.pop('banned_word'), context.user_data.pop('banned_duration'), message.text
                cur.execute("INSERT INTO banned_words (word, duration_minutes, warning_message) VALUES (%s, %s, %s) ON CONFLICT (word) DO UPDATE SET duration_minutes = EXCLUDED.duration_minutes, warning_message = EXCLUDED.warning_message;", (word, dur, warn))
                await message.reply_text(f"✅ تم حفظ الكلمة المحظورة: {word}.")
            elif step == 'banned_delete_word':
                word = message.text.strip()
                cur.execute("DELETE FROM banned_words WHERE word = %s;", (word,));
                await message.reply_text(f"✅ تم حذف {word}." if cur.rowcount > 0 else f"لم أجد {word}.")
            elif step == 'reply_add_keyword':
                context.user_data['keyword'] = message.text.strip(); context.user_data['next_step'] = 'reply_add_text'
                await message.reply_text("الآن أرسل نص الرد.")
            elif step == 'reply_add_text':
                keyword, reply = context.user_data.pop('keyword'), message.text
                cur.execute("INSERT INTO auto_replies (keyword, reply) VALUES (%s, %s) ON CONFLICT (keyword) DO UPDATE SET reply = EXCLUDED.reply;", (keyword, reply))
                await message.reply_text("✅ تم حفظ الرد التلقائي.")
            elif step == 'reply_delete_keyword':
                keyword = message.text.strip()
                cur.execute("DELETE FROM auto_replies WHERE keyword = %s;", (keyword,));
                await message.reply_text(f"✅ تم حذف الرد {keyword}." if cur.rowcount > 0 else f"لم أجد الرد {keyword}.")
            elif step == 'link_add_pattern':
                pattern = message.text.strip()
                cur.execute("INSERT INTO allowed_links (link_pattern) VALUES (%s) ON CONFLICT DO NOTHING;", (pattern,))
                await message.reply_text(f"✅ تم إضافة النمط {pattern} للقائمة البيضاء.")
            elif step == 'link_delete_pattern':
                pattern = message.text.strip()
                cur.execute("DELETE FROM allowed_links WHERE link_pattern = %s;", (pattern,));
                await message.reply_text(f"✅ تم حذف {pattern}." if cur.rowcount > 0 else f"لم أجد {pattern}.")
            elif step == 'msg_set_welcome':
                cur.execute("UPDATE settings SET value = %s WHERE key = 'welcome_message';", (message.text,))
                await message.reply_text("✅ تم تحديث رسالة الترحيب.")
            elif step == 'msg_set_forward':
                cur.execute("UPDATE settings SET value = %s WHERE key = 'forward_reply_message';", (message.text,))
                await message.reply_text("✅ تم تحديث رسالة الرد على التواصل.")
        conn.commit()
    finally:
        if conn:
            conn.close()

def main():
    setup_database()
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.User(ADMIN_ID), conversation_handler), group=-1)
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, media_downloader_handler), group=1)
    application.add_handler(MessageHandler(filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, group_message_handler), group=2)
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, private_message_handler), group=3)
    
    logger.info("البوت قيد التشغيل (الإصدار 4.4 - تحسين تسجيل المستخدمين)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
