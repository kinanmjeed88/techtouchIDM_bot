import os
import logging
import datetime
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
    CallbackQueryHandler, ConversationHandler
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
import yt_dlp

# استيراد الوحدات المخصصة
from database import (
    init_db, add_or_update_user, get_all_active_users, set_user_blocked, get_blocked_user_count,
    db_add_item, db_get_all_items, db_delete_item, BannedWord, BannedLink, WhitelistedLink,
    get_setting, set_setting, AutoReply, get_all_auto_replies, get_user, update_user_warnings,
    mute_user, is_user_muted, save_group_message, update_message_reactions, get_top_messages_by_reactions,
    increment_user_message_count, get_top_active_users, SessionLocal, User,
    save_private_message, get_unreplied_private_messages, set_private_message_replied,
    add_or_update_group, get_all_groups
)

# إعداد نظام التسجيل (Logging)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# قراءة المتغيرات الحساسة من بيئة التشغيل
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID'))

# [FIX] إصلاح خطأ ValueError: يجب أن يتطابق عدد المتغيرات مع قيمة range
# مراحل المحادثة (23 متغيرًا)
(ADD_BANNED_WORD, ADD_BANNED_LINK, ADD_WHITELISTED_LINK,
 SET_AUTO_REPLY, BROADCAST_MESSAGE, ADMIN_REPLY,
 ADD_AUTO_REPLY_KEYWORD, ADD_AUTO_REPLY_TEXT, SET_WELCOME_MESSAGE,
 SET_WARNING_MESSAGE, SET_MUTE_DURATION_BANNED_WORD, SET_MUTE_DURATION_BANNED_LINK,
 SET_AUTO_REPLY_PRIVATE_MESSAGE, SET_WELCOME_MESSAGE_TEXT,
 MANAGE_AUTO_REPLY_KEYWORD, MANAGE_AUTO_REPLY_TEXT,
 BROADCAST_CONFIRM, BROADCAST_MESSAGE_TEXT,
 ADD_BANNED_WORD_MUTE_DURATION, ADD_BANNED_LINK_MUTE_DURATION,
 SET_WELCOME_MESSAGE_TEXT_INPUT, SET_WARNING_MESSAGE_TEXT_INPUT,
 SET_AUTO_REPLY_PRIVATE_MESSAGE_TEXT_INPUT) = range(23) # (23 قيمة)

# --- دوال مساعدة ---
def escape_markdown_v2(text: str) -> str:
    if not isinstance(text, str):
        return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# --- وظائف بوت التحميل ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_or_update_user(user.id, user.full_name, user.username)
    welcome_message = get_setting('welcome_message') or "أهلاً بك في البوت!"
    await update.message.reply_text(welcome_message)

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    sent_message = await update.message.reply_text('جاري معالجة الرابط، يرجى الانتظار...')
    ydl_opts = {
        'format': 'best',
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'noplaylist': True,
        'postprocessors': [{'key': 'FFmpegMetadata', 'add_metadata': True}],
        'restrictfilenames': True,
        'trim_filenames': 200,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
            if os.path.exists(filepath):
                await update.message.reply_document(document=open(filepath, 'rb'), caption=info.get('title', ''))
                os.remove(filepath)
                await sent_message.delete()
            else:
                raise FileNotFoundError("الملف لم يتم إنشاؤه بواسطة yt-dlp.")
    except Exception as e:
        logger.error(f"Error downloading {url}: {e}")
        error_message = str(e)
        if "HTTP Error 403: Forbidden" in error_message or "Sign in to confirm your age" in error_message:
            await sent_message.edit_text("عذراً، لا يمكن تحميل هذا الفيديو. قد يكون محميًا أو يتطلب تسجيل الدخول.")
        else:
            await sent_message.edit_text(f"حدث خطأ أثناء التحميل. يرجى المحاولة مرة أخرى.")

# --- معالجة الرسائل العادية في المجموعة ---
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    message = update.message
    if not user or not message or not message.text:
        return
    add_or_update_user(user.id, user.full_name, user.username)
    increment_user_message_count(user.id)
    text_lower = message.text.lower()
    all_replies = get_all_auto_replies()
    for reply in all_replies:
        if reply.keyword.lower() in text_lower:
            try:
                await message.reply_text(reply.reply_text, parse_mode=ParseMode.MARKDOWN_V2)
            except BadRequest:
                await message.reply_text(reply.reply_text)
            return
    is_admin = False
    if user.id == ADMIN_ID:
        is_admin = True
    else:
        try:
            chat_member = await context.bot.get_chat_member(chat.id, user.id)
            if chat_member.status in ['administrator', 'creator']:
                is_admin = True
        except Exception as e:
            logger.warning(f"Could not check admin status for {user.id} in {chat.id}: {e}")
    if is_admin:
        return
    if is_user_muted(user.id):
        try:
            await message.delete()
        except Exception as e:
            logger.warning(f"Could not delete muted user's message: {e}")
        return
    warning_message_text = get_setting('warning_message') or "رسالتك تحتوي على محتوى غير مسموح به."
    escaped_warning_message = escape_markdown_v2(warning_message_text)
    banned_links = db_get_all_items(BannedLink, 'link_pattern')
    whitelisted_links = db_get_all_items(WhitelistedLink, 'link_prefix')
    urls = re.findall(r'(https?://\S+)', message.text)
    for url in urls:
        is_whitelisted = any(url.startswith(prefix) for prefix in whitelisted_links)
        if not is_whitelisted:
            for pattern in banned_links:
                if pattern in url:
                    await apply_restriction(update, context, user, escaped_warning_message, 'link', pattern)
                    return
    banned_words = db_get_all_items(BannedWord, 'word')
    for word in banned_words:
        if word.lower() in text_lower:
            await apply_restriction(update, context, user, escaped_warning_message, 'word', word)
            return

async def apply_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE, user, warning_message, violation_type, violation_item):
    try:
        await update.message.delete()
    except Forbidden:
        logger.warning(f"Bot lacks permission to delete message in chat {update.effective_chat.id}")
    except Exception as e:
        logger.error(f"Error deleting message: {e}")
    try:
        await context.bot.send_message(user.id, warning_message, parse_mode=ParseMode.MARKDOWN_V2)
    except Forbidden:
        logger.warning(f"User {user.id} has blocked the bot. Cannot send warning.")
    except Exception as e:
        logger.error(f"Error sending warning to {user.id}: {e}")
    update_user_warnings(user.id)
    db = SessionLocal()
    try:
        model = BannedLink if violation_type == 'link' else BannedWord
        column = BannedLink.link_pattern if violation_type == 'link' else BannedWord.word
        item_obj = db.query(model).filter(getattr(model, column) == violation_item).first()
        if item_obj and item_obj.mute_duration:
            mute_user(user.id, item_obj.mute_duration)
            mute_duration_text = {'day': 'يوم', 'week': 'أسبوع', 'month': 'شهر'}.get(item_obj.mute_duration, '')
            try:
                await context.bot.send_message(user.id, escape_markdown_v2(f"تم تقييدك لمدة {mute_duration_text} بسبب تكرار المخالفات."), parse_mode=ParseMode.MARKDOWN_V2)
            except Exception as e:
                logger.error(f"Error sending mute notification to {user.id}: {e}")
    finally:
        db.close()

# --- لوحة تحكم الأدمن ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        await update.message.reply_text("هذا الأمر مخصص للأدمن فقط.")
        return
    keyboard = [
        [InlineKeyboardButton("🚫 إدارة الحظر", callback_data="manage_banning")],
        [InlineKeyboardButton("💬 إدارة الردود التلقائية", callback_data="manage_auto_replies")],
        [InlineKeyboardButton("📢 إدارة البث", callback_data="manage_broadcast")],
        [InlineKeyboardButton("📊 تقارير التفاعل", callback_data="manage_reports")],
        [InlineKeyboardButton("⚙️ إعدادات أخرى", callback_data="manage_settings")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="close_panel")]
    ]
    await update.message.reply_text("لوحة تحكم الأدمن:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- معالج الأزرار الرئيسي ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    menu_map = {
        "main_menu": ("لوحة تحكم الأدمن:", [
            [InlineKeyboardButton("🚫 إدارة الحظر", callback_data="manage_banning")],
            [InlineKeyboardButton("💬 إدارة الردود التلقائية", callback_data="manage_auto_replies")],
            [InlineKeyboardButton("📢 إدارة البث", callback_data="manage_broadcast")],
            [InlineKeyboardButton("📊 تقارير التفاعل", callback_data="manage_reports")],
            [InlineKeyboardButton("⚙️ إعدادات أخرى", callback_data="manage_settings")],
            [InlineKeyboardButton("❌ إغلاق", callback_data="close_panel")]
        ]),
        "manage_banning": ("إدارة الحظر:", [
            [InlineKeyboardButton("📝 الكلمات المحظورة", callback_data="banned_words_menu")],
            [InlineKeyboardButton("🔗 الروابط المحظورة", callback_data="banned_links_menu")],
            [InlineKeyboardButton("✅ الروابط المسموحة", callback_data="whitelisted_links_menu")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ]),
        "manage_auto_replies": ("إدارة الردود التلقائية:", [
            [InlineKeyboardButton("➕ إضافة رد تلقائي", callback_data="add_auto_reply_start")],
            [InlineKeyboardButton("🗑️ حذف رد تلقائي", callback_data="delete_auto_reply_menu")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ]),
        "manage_broadcast": ("إدارة البث:", [
            [InlineKeyboardButton("✍️ إرسال بث جديد", callback_data="broadcast_start")],
            [InlineKeyboardButton(f"👥 فحص المحظورين ({get_blocked_user_count()})", callback_data="check_blocked")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ]),
        "manage_reports": ("تقارير التفاعل:", [
            [InlineKeyboardButton("📈 أكثر 5 متفاعلين", callback_data="top_active_users_report")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ]),
        "manage_settings": ("إعدادات أخرى:", [
            [InlineKeyboardButton("🤖 تعديل الرد التلقائي للخاص", callback_data="set_auto_reply_start")],
            [InlineKeyboardButton("👋 تعديل رسالة الترحيب", callback_data="set_welcome_start")],
            [InlineKeyboardButton("⚠️ رسالة التنبيه عند الحظر", callback_data="set_warning_message_start")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ])
    }
    if data in menu_map:
        text, keyboard_data = menu_map[data]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard_data))
    elif data == "close_panel":
        await query.message.delete()
    elif data == "check_blocked":
        await query.answer(f"عدد المستخدمين المحظورين: {get_blocked_user_count()}", show_alert=True)
    elif data == "top_active_users_report":
        await show_top_active_users_report(query)
    # ... (بقية معالجات الأزرار)

async def show_top_active_users_report(query: Update.callback_query):
    top_users = get_top_active_users()
    report_text = """📈 *أكثر 5 مستخدمين تفاعلاً:*\n\n"""
    if top_users:
        for i, user_obj in enumerate(top_users):
            user_display = escape_markdown_v2(user_obj.full_name or user_obj.username or str(user_obj.telegram_id))
            user_id_escaped = escape_markdown_v2(str(user_obj.telegram_id))
            report_text += f"""{i+1}\\. {user_display} \\(`{user_id_escaped}`\\) \\- {user_obj.message_count} رسالة\n"""
    else:
        report_text += "لا يوجد مستخدمون متفاعلون بعد\\."
    try:
        await query.edit_message_text(report_text, parse_mode=ParseMode.MARKDOWN_V2)
    except BadRequest as e:
        logger.error(f"Failed to send top users report with MarkdownV2: {e}")
        plain_report = re.sub(r'[\\*`]', '', report_text)
        await query.edit_message_text(plain_report)

# --- دوال إدارة القوائم ---
async def manage_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, model, column: str, add_cb: str, del_cb: str, back_cb: str):
    # ... (هذه الدالة موجودة في الكود الأصلي)
    pass

# --- معالجات المحادثات والبث ---
# ... (جميع دوال المحادثات مثل save_item_and_ask_mute, broadcast_start, etc. تبقى هنا)
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم إلغاء العملية.")
    context.user_data.clear()
    return ConversationHandler.END

# [FIX] إصلاح خطأ NameError: إضافة الدالة المفقودة هنا
async def private_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        return
    if user.id == ADMIN_ID:
        await admin_panel(update, context)
        return
    auto_reply_text = get_setting('auto_reply') or "أهلاً بك! أنا بوت إدارة مجموعة. لا يمكنني الرد على الرسائل الخاصة حالياً."
    await update.message.reply_text(auto_reply_text)

# --- الدالة الرئيسية ---
def main():
    if not TELEGRAM_TOKEN or not ADMIN_ID:
        logger.critical("FATAL: TELEGRAM_TOKEN or ADMIN_ID environment variables are not set.")
        return

    init_db()

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # --- معالج المحادثات الكامل ---
    conv_handler = ConversationHandler(
        entry_points=[
            # ... (نقاط الدخول من الكود الأصلي)
        ],
        states={
            # ... (الحالات من الكود الأصلي)
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        conversation_timeout=300
    )

    # --- إضافة المعالجات ---
    application.add_handler(CommandHandler("start", start_command, filters=filters.ChatType.PRIVATE))
    application.add_handler(MessageHandler(filters.Regex(r'^يمان$') & filters.User(user_id=ADMIN_ID), admin_panel))
    
    application.add_handler(conv_handler)

    application.add_handler(CallbackQueryHandler(button_handler))

    # معالجات الرسائل
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, private_message_handler))
    application.add_handler(MessageHandler((filters.ChatType.GROUP | filters.ChatType.SUPERGROUP) & filters.TEXT & ~filters.COMMAND, message_handler))
    application.add_handler(MessageHandler(filters.Regex(r'https?://') & ~filters.COMMAND, handle_link))

    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
