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

# قراءة المتغيرات الحساسة من بيئة التشغيل (Railway Variables)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID'))

# مراحل المحادثة
(ADD_BANNED_WORD, ADD_BANNED_LINK, ADD_WHITELISTED_LINK,
 SET_AUTO_REPLY, BROADCAST_MESSAGE, ADMIN_REPLY,
 ADD_AUTO_REPLY_KEYWORD, ADD_AUTO_REPLY_TEXT, SET_WELCOME_MESSAGE,
 SET_WARNING_MESSAGE, SET_MUTE_DURATION_BANNED_WORD, SET_MUTE_DURATION_BANNED_LINK,
 SET_AUTO_REPLY_PRIVATE_MESSAGE, SET_WELCOME_MESSAGE_TEXT,
 MANAGE_AUTO_REPLY_KEYWORD, MANAGE_AUTO_REPLY_TEXT,
 BROADCAST_CONFIRM, BROADCAST_MESSAGE_TEXT,
 ADD_BANNED_WORD_MUTE_DURATION, ADD_BANNED_LINK_MUTE_DURATION,
 SET_WELCOME_MESSAGE_TEXT_INPUT, SET_WARNING_MESSAGE_TEXT_INPUT,
 SET_AUTO_REPLY_PRIVATE_MESSAGE_TEXT_INPUT) = range(25)

# --- دوال مساعدة ---
def escape_markdown_v2(text: str) -> str:
    """تهريب الأحرف الخاصة في MarkdownV2."""
    if not isinstance(text, str):
        return ""
    # قائمة الأحرف الخاصة في MarkdownV2 التي تحتاج إلى تهريب
    # تم تحديث القائمة لتشمل جميع الأحرف الخاصة وتجنب الأخطاء
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'(?<!\\\\)([{re.escape(escape_chars)}])', r'\\\\1', text)

def format_link_for_markdown(text: str, url: str) -> str:
    """تنسيق رابط مغلف لـ MarkdownV2."""
    return f"[{escape_markdown_v2(text)}]({escape_markdown_v2(url)})"

# --- وظائف بوت التحميل (الوظيفة الأصلية) ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_or_update_user(user.id, user.full_name, user.username)
    welcome_message = get_setting('welcome_message') or "أهلاً بك في البوت!"
    await update.message.reply_text(welcome_message)

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    sent_message = await update.message.reply_text('جاري معالجة الرابط، يرجى الانتظار...')

    # استخدام اسم ملف أقصر لتجنب مشكلة طول الاسم
    ydl_opts = {
        'format': 'best',
        'outtmpl': 'downloads/%(id)s.%(ext)s', # حفظ في مجلد downloads باسم ID الفيديو
        'noplaylist': True,
        'postprocessors': [{
            'key': 'FFmpegMetadata',
            'add_metadata': True,
        }],
        'restrictfilenames': True, # يضمن أسماء ملفات آمنة
        'trim_filenames': 200, # يقصر اسم الملف إذا كان طويلاً جداً
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)

            # إرسال الفيديو كملف
            await update.message.reply_document(document=open(filepath, 'rb'), caption=info.get('title', ''))
            os.remove(filepath) # حذف الملف بعد الإرسال
            await sent_message.delete() # حذف رسالة "جاري المعالجة"
    except Exception as e:
        logger.error(f"Error downloading {url}: {e}")
        error_message = str(e)
        if "HTTP Error 403: Forbidden" in error_message or "Sign in to confirm your age" in error_message:
            await sent_message.edit_text("عذراً، لا يمكن تحميل هذا الفيديو. قد يكون محميًا أو يتطلب تسجيل الدخول.")
        elif "File name too long" in error_message:
            await sent_message.edit_text("حدث خطأ: اسم الملف طويل جداً. يرجى المحاولة مع رابط آخر.")
        else:
            await sent_message.edit_text(f"حدث خطأ أثناء التحميل: {error_message}")

# --- معالجة الرسائل العادية في المجموعة ---
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    message = update.message

    if not user or not message or not message.text:
        return

    add_or_update_user(user.id, user.full_name, user.username)
    increment_user_message_count(user.id) # زيادة عداد رسائل المستخدم

    text = message.text # لا نحولها إلى lower() هنا للحفاظ على الكلمة الأصلية

    # 1. فحص الردود التلقائية أولاً (تطابق جزئي)
    all_replies = get_all_auto_replies()
    for reply in all_replies:
        if reply.keyword.lower() in text.lower(): # التحقق من الكلمة المفتاحية بحروف صغيرة
            await message.reply_text(reply.reply_text, parse_mode=ParseMode.MARKDOWN_V2)
            return # نتوقف إذا وجدنا رداً

    # 2. فحص الحظر والتقييد (إذا لم يكن هناك رد تلقائي)
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

    # الأدمن والمشرفون مستثنون من الحظر
    if is_admin:
        return

    # إذا كان المستخدم مقيداً
    if is_user_muted(user.id):
        try:
            await message.delete()
            # يمكن إرسال رسالة خاصة للمستخدم المقيد إذا أردت
        except Exception as e:
            logger.warning(f"Could not delete muted user's message: {e}")
        return

    warning_message_text = get_setting('warning_message') or "رسالتك تحتوي على محتوى غير مسموح به."

    # فحص الروابط
    banned_links = db_get_all_items(BannedLink, 'link_pattern')
    whitelisted_links = db_get_all_items(WhitelistedLink, 'link_prefix')
    urls = re.findall(r'(https?://\S+)\b', text) # البحث عن الروابط بشكل أفضل
    for url in urls:
        is_whitelisted = any(url.startswith(prefix) for prefix in whitelisted_links)
        if not is_whitelisted:
            for pattern in banned_links:
                if pattern in url:
                    try:
                        await message.delete()
                    except Forbidden: # البوت ليس لديه صلاحية حذف الرسالة
                        logger.warning(f"Bot lacks permission to delete message in chat {chat.id}")

                    # إرسال رسالة التنبيه مع تهريب الأحرف الخاصة
                    escaped_warning_message = escape_markdown_v2(warning_message_text)
                    await context.bot.send_message(user.id, escaped_warning_message, parse_mode=ParseMode.MARKDOWN_V2)
                    
                    warnings_count = update_user_warnings(user.id)
                    
                    # تطبيق التقييد بناءً على إعدادات الرابط المحظور
                    db = SessionLocal()
                    try:
                        banned_link_obj = db.query(BannedLink).filter(BannedLink.link_pattern == pattern).first()
                        if banned_link_obj and banned_link_obj.mute_duration:
                            mute_user(user.id, banned_link_obj.mute_duration)
                            mute_duration_text = {
                                'day': 'يوم', 'week': 'أسبوع', 'month': 'شهر'
                            }.get(banned_link_obj.mute_duration, 'غير محدد')
                            await context.bot.send_message(user.id, escape_markdown_v2(f"تم تقييدك لمدة {mute_duration_text} بسبب تكرار المخالفات."), parse_mode=ParseMode.MARKDOWN_V2)
                    finally:
                        db.close()
                    return

    # فحص الكلمات
    banned_words = db_get_all_items(BannedWord, 'word')
    if any(word.lower() in text.lower() for word in banned_words): # التحقق من الكلمات المحظورة بحروف صغيرة
        try:
            await message.delete()
        except Forbidden: # البوت ليس لديه صلاحية حذف الرسالة
            logger.warning(f"Bot lacks permission to delete message in chat {chat.id}")

        # إرسال رسالة التنبيه مع تهريب الأحرف الخاصة
        escaped_warning_message = escape_markdown_v2(warning_message_text)
        await context.bot.send_message(user.id, escaped_warning_message, parse_mode=ParseMode.MARKDOWN_V2)

        warnings_count = update_user_warnings(user.id)
        
        # تطبيق التقييد بناءً على إعدادات الكلمة المحظورة
        db = SessionLocal()
        try:
            # البحث عن الكلمة المحظورة التي تطابقت
            matched_word = next((w for w in banned_words if w.lower() in text.lower()), None)
            if matched_word:
                banned_word_obj = db.query(BannedWord).filter(BannedWord.word == matched_word).first()
                if banned_word_obj and banned_word_obj.mute_duration:
                    mute_user(user.id, banned_word_obj.mute_duration)
                    mute_duration_text = {
                        'day': 'يوم', 'week': 'أسبوع', 'month': 'شهر'
                    }.get(banned_word_obj.mute_duration, 'غير محدد')
                    await context.bot.send_message(user.id, escape_markdown_v2(f"تم تقييدك لمدة {mute_duration_text} بسبب تكرار المخالفات."), parse_mode=ParseMode.MARKDOWN_V2)
        finally:
            db.close()
        return

    # حفظ الرسالة لتقارير التفاعل (إذا لم يتم حذفها)
    # save_group_message(message.message_id, user.id, chat.id, message.text)

# --- لوحة تحكم الأدمن ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID): # التأكد من أن ADMIN_ID هو string للمقارنة
        await update.message.reply_text("هذا الأمر مخصص للأدمن فقط.")
        return

    keyboard = [
        [InlineKeyboardButton("🚫 إدارة الحظر", callback_data="manage_banning")],
        [InlineKeyboardButton("💬 إدارة الردود التلقائية", callback_data="manage_auto_replies")],
        [InlineKeyboardButton("📢 إدارة البث", callback_data="manage_broadcast")],
        [InlineKeyboardButton("📊 تقارير التفاعل", callback_data="manage_reports")], # جديد
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
            [InlineKeyboardButton("❤️ أكثر التعليقات إعجاباً", callback_data="top_reactions_report")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ]),
        "manage_settings": ("إعدادات أخرى:", [
            [InlineKeyboardButton("🤖 تعديل الرد التلقائي للخاص", callback_data="set_auto_reply_start")],
            [InlineKeyboardButton("👋 تعديل رسالة الترحيب", callback_data="set_welcome_start")],
            [InlineKeyboardButton("⚠️ رسالة التنبيه عند الحظر", callback_data="set_warning_message_start")], # جديد
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ])
    }

    if data in menu_map:
        text, keyboard_data = menu_map[data]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard_data), parse_mode=ParseMode.MARKDOWN_V2)

    elif data == "check_blocked":
        blocked_users_count = get_blocked_user_count()
        await query.answer(f"عدد المستخدمين المحظورين: {blocked_users_count}", show_alert=True)

    elif data.startswith("reply_"):
        user_id = data.split("_")[1]
        context.user_data['reply_user_id'] = user_id
        await query.message.reply_text(f"أرسل الآن ردك للمستخدم صاحب الـ ID: {user_id}")
        return ADMIN_REPLY

    elif data == "close_panel":
        await query.message.delete()

    # --- معالجة تقارير التفاعل ---
    elif data == "top_active_users_report":
        top_users = get_top_active_users()
        report_text = "📈 *أكثر 5 مستخدمين تفاعلاً:*
"
        if top_users:
            for i, user_obj in enumerate(top_users):
                # استخدام escape_markdown_v2 لكل من الاسم والمعرف
                report_text += f"{i+1}\. {escape_markdown_v2(user_obj.full_name or user_obj.username or user_obj.telegram_id)} (`{escape_markdown_v2(user_obj.telegram_id)}`) - {user_obj.message_count} رسالة\n"
        else:
            report_text += "لا يوجد مستخدمون متفاعلون بعد."
        await query.edit_message_text(report_text, parse_mode=ParseMode.MARKDOWN_V2)

    elif data == "top_reactions_report":
        # هذا يتطلب حفظ الرسائل وتفاعلاتها، وهو غير مفعل حاليا في message_handler
        # ستحتاج إلى تفعيل save_group_message وتحديث update_message_reactions
        await query.edit_message_text("هذه الميزة تتطلب تفعيل حفظ الرسائل وتفاعلاتها أولاً.")

    # --- معالجة قوائم الحظر والروابط ---
    elif data == "banned_words_menu":
        await manage_list_menu(update, context, "الكلمات المحظورة", BannedWord, "word", "add_banned_word_start", "delete_banned_word_menu", "manage_banning")
    elif data == "banned_links_menu":
        await manage_list_menu(update, context, "الروابط المحظورة", BannedLink, "link_pattern", "add_banned_link_start", "delete_banned_link_menu", "manage_banning")
    elif data == "whitelisted_links_menu":
        await manage_list_menu(update, context, "الروابط المسموحة", WhitelistedLink, "link_prefix", "add_whitelisted_link_start", "delete_whitelisted_link_menu", "manage_banning")

    # --- معالجة حذف العناصر من القوائم ---
    elif data.startswith("delete_banned_word_"):
        word_to_delete = data.split("_", 3)[3]
        await confirm_delete_item(update, context, BannedWord, "word", "كلمة محظورة", "add_banned_word_start", "delete_banned_word_menu", "manage_banning")
    elif data.startswith("delete_banned_link_"):
        link_to_delete = data.split("_", 3)[3]
        await confirm_delete_item(update, context, BannedLink, "link_pattern", "رابط محظور", "add_banned_link_start", "delete_banned_link_menu", "manage_banning")
    elif data.startswith("delete_whitelisted_link_"):
        link_to_delete = data.split("_", 3)[3]
        await confirm_delete_item(update, context, WhitelistedLink, "link_prefix", "رابط مسموح", "add_whitelisted_link_start", "delete_whitelisted_link_menu", "manage_banning")
    elif data.startswith("delete_auto_reply_"):
        keyword_to_delete = data.split("_", 3)[3]
        await confirm_delete_item(update, context, AutoReply, "keyword", "رد تلقائي", "add_auto_reply_start", "delete_auto_reply_menu", "manage_auto_replies")

# --- دوال إدارة القوائم (محدثة لدعم التقييد) ---
async def manage_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, model, column_name: str, add_cb: str, del_cb: str, back_cb: str):
    query = update.callback_query
    
    db = SessionLocal()
    try:
        items_with_details = db.query(model).all()
    finally:
        db.close()

    text = f"قائمة {item_type}:\n"
    if items_with_details:
        for item_obj in items_with_details:
            item_value = getattr(item_obj, column_name)
            mute_info = f" (تقييد: {item_obj.mute_duration})" if hasattr(item_obj, 'mute_duration') and item_obj.mute_duration else ""
            text += f"- `{escape_markdown_v2(item_value)}`{escape_markdown_v2(mute_info)}\n"
    else:
        text += "لا يوجد عناصر."
    
    keyboard = [
        [InlineKeyboardButton("➕ إضافة", callback_data=add_cb)],
        [InlineKeyboardButton("🗑️ حذف", callback_data=del_cb)],
        [InlineKeyboardButton("⬅️ عودة", callback_data=back_cb)]
    ]
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    except BadRequest as e:
        logger.error(f"BadRequest in manage_list_menu: {e}")
        # Fallback to plain text if MarkdownV2 fails
        text_plain = f"قائمة {item_type}:\n"
        if items_with_details:
            for item_obj in items_with_details:
                item_value = getattr(item_obj, column_name)
                mute_info = f" (تقييد: {item_obj.mute_duration})" if hasattr(item_obj, 'mute_duration') and item_obj.mute_duration else ""
                text_plain += f"- {item_value}{mute_info}\n"
        else:
            text_plain += "لا يوجد عناصر."
        await query.edit_message_text(text_plain, reply_markup=InlineKeyboardMarkup(keyboard))

async def add_item_start(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, state):
    query = update.callback_query
    await query.edit_message_text(f"أرسل {item_type} الذي تريد إضافته.")
    context.user_data['item_type_for_mute'] = item_type # لحفظ نوع العنصر
    return state

async def delete_item_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, model, column_name: str, back_callback: str):
    query = update.callback_query
    items = db_get_all_items(model, column_name)
    if not items:
        await query.answer("لا يوجد عناصر لحذفها!", show_alert=True)
        return
    
    keyboard = [[InlineKeyboardButton(f"🗑️ {escape_markdown_v2(item)}", callback_data=f"delete_{model.__tablename__}_{item}")] for item in items]
    keyboard.append([InlineKeyboardButton("⬅️ عودة", callback_data=back_callback)])
    await query.edit_message_text(f"اختر {item_type} الذي تريد حذفه:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

async def confirm_delete_item(update: Update, context: ContextTypes.DEFAULT_TYPE, model, column_name: str, item_type: str, add_cb: str, del_cb: str, back_cb: str):
    query = update.callback_query
    item_value = query.data.split("_", 3)[3]
    
    if db_delete_item(item_value, model, column_name):
        await query.answer("تم الحذف بنجاح!")
        await manage_list_menu(update, context, item_type, model, column_name, add_cb, del_cb, back_cb)
    else:
        await query.answer("فشل الحذف.", show_alert=True)

# --- معالجات المحادثات (محدثة لدعم التقييد) ---
async def save_item_and_ask_mute(update: Update, context: ContextTypes.DEFAULT_TYPE, model, column_name: str, next_state):
    item_value = update.message.text.strip()
    context.user_data['item_value'] = item_value
    context.user_data['model'] = model
    context.user_data['column_name'] = column_name

    keyboard = [
        [InlineKeyboardButton("بدون تقييد", callback_data="mute_none")],
        [InlineKeyboardButton("يوم", callback_data="mute_day")],
        [InlineKeyboardButton("أسبوع", callback_data="mute_week")],
        [InlineKeyboardButton("شهر", callback_data="mute_month")]
    ]
    await update.message.reply_text("اختر مدة التقييد عند مخالفة هذا العنصر:", reply_markup=InlineKeyboardMarkup(keyboard))
    return next_state

async def save_item_with_mute_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mute_duration = query.data.replace("mute_", "")

    item_value = context.user_data.pop('item_value')
    model = context.user_data.pop('model')
    column_name = context.user_data.pop('column_name')

    item_data = {column_name: item_value, 'mute_duration': mute_duration if mute_duration != 'none' else None}

    if db_add_item(item_data, model, column_name):
        await query.edit_message_text(f"✅ تم حفظ العنصر \'{escape_markdown_v2(item_value)}\' مع مدة تقييد \'{escape_markdown_v2(mute_duration)}\'.", parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await query.edit_message_text(f"⚠️ العنصر \'{escape_markdown_v2(item_value)}\' موجود بالفعل.", parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationHandler.END

async def save_auto_reply_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['keyword'] = update.message.text.strip()
    await update.message.reply_text("الآن أرسل نص الرد.")
    return ADD_AUTO_REPLY_TEXT

async def save_auto_reply_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyword = context.user_data.pop('keyword')
    reply_text = update.message.text
    if db_add_item({'keyword': keyword, 'reply_text': reply_text}, AutoReply, 'keyword'):
        await update.message.reply_text("✅ تم حفظ الرد التلقائي.")
    else:
        await update.message.reply_text("⚠️ فشل حفظ الرد.")
    return ConversationHandler.END

async def set_welcome_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting('welcome_message', update.message.text)
    await update.message.reply_text("✅ تم حفظ رسالة الترحيب الجديدة.")
    return ConversationHandler.END

async def set_auto_reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting('auto_reply', update.message.text)
    await update.message.reply_text("✅ تم حفظ الرد التلقائي الجديد للرسائل الخاصة.")
    return ConversationHandler.END

async def set_warning_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting('warning_message', update.message.text)
    await update.message.reply_text("✅ تم حفظ رسالة التنبيه الجديدة.")
    return ConversationHandler.END

async def admin_reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = context.user_data.pop('reply_user_id', None)
    if not user_id: return ConversationHandler.END
    try:
        await context.bot.send_message(chat_id=user_id, text=update.message.text)
        await update.message.reply_text("✅ تم إرسال ردك.")
    except Exception as e:
        await update.message.reply_text(f"❌ فشل إرسال الرد: {e}")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم إلغاء العملية.")
    context.user_data.clear()
    return ConversationHandler.END

# --- معالجة الرسائل الخاصة (للبث والرد) ---
async def private_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message

    if not user or not message or not message.text:
        return

    # إذا كان الأدمن، يعرض لوحة التحكم
    if user.id == ADMIN_ID:
        await admin_panel(update, context) # يعرض لوحة التحكم الرئيسية للأدمن
        return

    # إذا لم يكن الأدمن، يعرض الرد التلقائي للخاص
    auto_reply_text = get_setting('auto_reply') or "أهلاً بك! أنا بوت إدارة مجموعة. لا يمكنني الرد على الرسائل الخاصة حالياً."
    await message.reply_text(auto_reply_text)

# --- وظائف البث ---
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("أرسل الرسالة التي تريد بثها لجميع المستخدمين.")
    return BROADCAST_MESSAGE

async def send_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    broadcast_text = update.message.text
    users = get_all_active_users() # يجلب جميع المستخدمين غير المحظورين
    sent_count = 0
    blocked_count = 0

    for user in users:
        try:
            await context.bot.send_message(chat_id=user.telegram_id, text=broadcast_text, parse_mode=ParseMode.MARKDOWN_V2)
            sent_count += 1
        except Forbidden: # المستخدم حظر البوت
            set_user_blocked(user.telegram_id, True)
            blocked_count += 1
        except Exception as e:
            logger.error(f"Failed to send broadcast to {user.telegram_id}: {e}")

    await update.message.reply_text(f"✅ تم إرسال البث إلى {sent_count} مستخدم.\n❌ فشل الإرسال إلى {blocked_count} مستخدم (تم حظر البوت).", parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationHandler.END

# --- الدالة الرئيسية ---
def main():
    if not TELEGRAM_TOKEN or not ADMIN_ID:
        logger.error("Missing TELEGRAM_TOKEN or ADMIN_ID.")
        return

    init_db()

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # --- محادثات ---
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "كلمة محظورة", ADD_BANNED_WORD), pattern="^add_banned_word_start$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "نمط رابط محظور", ADD_BANNED_LINK), pattern="^add_banned_link_start$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "بادئة رابط مسموح", ADD_WHITELISTED_LINK), pattern="^add_whitelisted_link_start$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "الرد التلقائي للخاص", SET_AUTO_REPLY), pattern="^set_auto_reply_start$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "رسالة الترحيب", SET_WELCOME_MESSAGE), pattern="^set_welcome_start$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "رسالة التنبيه", SET_WARNING_MESSAGE), pattern="^set_warning_message_start$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "الكلمة المفتاحية", ADD_AUTO_REPLY_KEYWORD), pattern="^add_auto_reply_start$"),
            CallbackQueryHandler(broadcast_start, pattern="^broadcast_start$"), # إضافة معالج بدء البث
            CallbackQueryHandler(button_handler, pattern="^reply_")
        ],
        states={
            ADD_BANNED_WORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: save_item_and_ask_mute(u, c, BannedWord, 'word', SET_MUTE_DURATION_BANNED_WORD))],
            ADD_BANNED_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: save_item_and_ask_mute(u, c, BannedLink, 'link_pattern', SET_MUTE_DURATION_BANNED_LINK))],
            ADD_WHITELISTED_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: save_item(u, c, WhitelistedLink, 'link_prefix'))],
            SET_MUTE_DURATION_BANNED_WORD: [CallbackQueryHandler(save_item_with_mute_duration, pattern="^mute_")],
            SET_MUTE_DURATION_BANNED_LINK: [CallbackQueryHandler(save_item_with_mute_duration, pattern="^mute_")],
            SET_AUTO_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_auto_reply_message)],
            SET_WELCOME_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_welcome_message)],
            SET_WARNING_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_warning_message)],
            ADD_AUTO_REPLY_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_auto_reply_keyword)],
            ADD_AUTO_REPLY_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_auto_reply_text)],
            BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_broadcast_message)], # معالج رسالة البث
            ADMIN_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply_message)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        conversation_timeout=300
    )

    # --- إضافة المعالجات ---
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.Regex(r'^يمان$') & filters.User(user_id=ADMIN_ID), admin_panel))

    application.add_handler(conv_handler)

    # --- معالجات الأزرار الديناميكية ---
    application.add_handler(CallbackQueryHandler(button_handler))

    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, private_message_handler))
    application.add_handler(MessageHandler((filters.ChatType.GROUP | filters.ChatType.SUPERGROUP) & filters.TEXT, message_handler))
    application.add_handler(MessageHandler(filters.Regex(r'^https?://\S+$') & ~filters.COMMAND, handle_link))

    logger.info("Bot is starting with new management features...")
    application.run_polling()

if __name__ == "__main__":
    main()
