import os
import logging
import re
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
    CallbackQueryHandler, ConversationHandler
)
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest

# استيراد الوحدات المخصصة
from database import (
    init_db, add_or_update_user, get_all_active_users, set_user_blocked, get_blocked_user_count,
    db_add_item, db_get_all_items, db_delete_item, BannedWord, BannedLink, WhitelistedLink,
    get_setting, set_setting, AutoReply, get_all_auto_replies
)

# --- الإعدادات الأولية ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID'))

# --- مراحل المحادثات ---
(ADD_BANNED_WORD, ADD_BANNED_LINK, ADD_WHITELISTED_LINK, 
 SET_AUTO_REPLY, BROADCAST_MESSAGE, ADMIN_REPLY, 
 ADD_AUTO_REPLY_KEYWORD, ADD_AUTO_REPLY_TEXT, SET_WELCOME_MESSAGE,
 SELECT_RESTRICTION_DURATION_WORD, SELECT_RESTRICTION_DURATION_LINK,
 ADMIN_REPLY_PHOTO_TEXT) = range(12)

# --- دوال مساعدة ---
def escape_markdown(text: str) -> str:
    """تهريب الأحرف الخاصة في MarkdownV2."""
    if not isinstance(text, str):
        return ""
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\\1', text)

# --- معالجات الرسائل ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر /start."""
    user = update.effective_user
    add_or_update_user(user.id, user.full_name, user.username)
    welcome_message = get_setting('welcome_message') or "أهلاً بك في البوت!"
    await update.message.reply_text(welcome_message)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """المعالج الرئيسي للرسائل في المجموعات."""
    user = update.effective_user
    chat = update.effective_chat
    message = update.message

    if not user or not message:
        return

    add_or_update_user(user.id, user.full_name, user.username)
    text = message.text.lower() if message.text else ""

    # 1. فحص الردود التلقائية أولاً
    all_replies = get_all_auto_replies()
    for reply in all_replies:
        if reply.keyword.lower() in text:
            await message.reply_text(reply.reply_text)
            return # نتوقف إذا وجدنا رداً

    # 2. فحص الحظر (إذا لم يكن هناك رد تلقائي)
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

    # فحص الروابط
    banned_links_data = db_get_all_items(BannedLink, 'link_pattern')
    banned_links = [item[0] for item in banned_links_data] # Extract patterns
    whitelisted_links = db_get_all_items(WhitelistedLink, 'link_prefix')
    urls = re.findall(r'(https?://\S+)', text)
    for url in urls:
        is_whitelisted = any(url.startswith(prefix) for prefix in whitelisted_links)
        if not is_whitelisted:
            for pattern, duration in banned_links_data:
                if pattern in url:
                    await message.delete()
                    await context.bot.send_message(chat.id, f"تم حذف رسالة من {user.mention_html()} لاحتوائها على رابط محظور.", parse_mode=ParseMode.HTML)
                    # TODO: Implement restriction logic based on 'duration'
                    return

    # فحص الكلمات
    banned_words_data = db_get_all_items(BannedWord, 'word')
    banned_words = [item[0] for item in banned_words_data] # Extract words
    if any(word in text for word in banned_words):
        await message.delete()
        await context.bot.send_message(chat.id, f"تم حذف رسالة من {user.mention_html()} لاحتوائها على كلمة محظورة.", parse_mode=ParseMode.HTML)
        # TODO: Implement restriction logic based on 'duration'
        return

async def private_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الرسائل الخاصة من المستخدمين."""
    user = update.effective_user
    add_or_update_user(user.id, user.full_name, user.username)

    auto_reply = get_setting('auto_reply')
    if auto_reply:
        await update.message.reply_text(auto_reply)

    user_info = escape_markdown(f"{user.full_name} (@{user.username})" if user.username else user.full_name)
    
    text_to_forward = (
        f"📩 *رسالة جديدة من:* {user_info}\n"
        f"*ID:* `{user.id}`\n\n"
    )
    
    if update.message.text:
        text_to_forward += f"```{escape_markdown(update.message.text)}```"
    elif update.message.caption:
        text_to_forward += f"```{escape_markdown(update.message.caption)}```"
    
    keyboard = [[InlineKeyboardButton("✍️ رد على المستخدم", callback_data=f"reply_{user.id}")]]
    
    if update.message.photo:
        # Forward photo to admin
        photo_file_id = update.message.photo[-1].file_id
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_file_id,
            caption=text_to_forward,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=text_to_forward,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN_V2
        )

# --- لوحة تحكم الأدمن ---

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض لوحة تحكم الأدمن."""
    keyboard = [
        [InlineKeyboardButton("🚫 إدارة الحظر", callback_data="manage_banning")],
        [InlineKeyboardButton("💬 إدارة الردود التلقائية", callback_data="manage_auto_replies")],
        [InlineKeyboardButton("📢 إدارة البث", callback_data="manage_broadcast")],
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
            [InlineKeyboardButton("⚙️ إعدادات أخرى", callback_data="manage_settings")],
            [InlineKeyboardButton("❌ إغلاق", callback_data="close_panel")]
        ]),
        "manage_banning": ("إدارة الحظر:", [
            [InlineKeyboardButton("📝 الكلمات المحظورة", callback_data="banned_words")],
            [InlineKeyboardButton("🔗 الروابط المحظورة", callback_data="banned_links")],
            [InlineKeyboardButton("✅ الروابط المسموحة", callback_data="whitelisted_links")],
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
        "manage_settings": ("إعدادات أخرى:", [
            [InlineKeyboardButton("🤖 تعديل الرد التلقائي للخاص", callback_data="set_auto_reply_start")],
            [InlineKeyboardButton("👋 تعديل رسالة الترحيب", callback_data="set_welcome_start")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ])
    }

    if data in menu_map:
        text, keyboard_data = menu_map[data]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard_data))

    elif data == "check_blocked":
        blocked_count = get_blocked_user_count()
        if blocked_count > 0:
            await query.answer(f"عدد المستخدمين الذين قاموا بحظر البوت: {blocked_count}", show_alert=True)
        else:
            await query.answer("لا يوجد مستخدمون قاموا بحظر البوت.", show_alert=True)

    elif data.startswith("reply_"):
        user_id = data.split("_")[1]
        context.user_data['reply_user_id'] = user_id
        keyboard = [[InlineKeyboardButton("إرسال صورة مع نص", callback_data="send_photo_with_text")] ]
        await query.message.reply_text(f"أرسل الآن ردك للمستخدم صاحب الـ ID: {user_id} (نص فقط أو اضغط على الزر لإرسال صورة مع نص)", reply_markup=InlineKeyboardMarkup(keyboard))
        return ADMIN_REPLY

    elif data == "send_photo_with_text":
        await query.message.reply_text("أرسل الصورة الآن مع النص الذي تريده في الوصف (caption).")
        return ADMIN_REPLY_PHOTO_TEXT

    elif data == "close_panel":
        await query.message.delete()

    # Restriction duration selection
    elif data.startswith("select_duration_word_"):
        word = data.replace("select_duration_word_", "")
        context.user_data['current_item_to_add'] = word
        context.user_data['item_model'] = BannedWord
        context.user_data['item_column'] = 'word'
        await query.edit_message_text("اختر مدة التقييد للكلمة المحظورة:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("يوم", callback_data="duration_day")],
            [InlineKeyboardButton("أسبوع", callback_data="duration_week")],
            [InlineKeyboardButton("شهر", callback_data="duration_month")],
            [InlineKeyboardButton("بدون تقييد", callback_data="duration_none")]
        ]))
        return SELECT_RESTRICTION_DURATION_WORD

    elif data.startswith("select_duration_link_"):
        link = data.replace("select_duration_link_", "")
        context.user_data['current_item_to_add'] = link
        context.user_data['item_model'] = BannedLink
        context.user_data['item_column'] = 'link_pattern'
        await query.edit_message_text("اختر مدة التقييد للرابط المحظور:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("يوم", callback_data="duration_day")],
            [InlineKeyboardButton("أسبوع", callback_data="duration_week")],
            [InlineKeyboardButton("شهر", callback_data="duration_month")],
            [InlineKeyboardButton("بدون تقييد", callback_data="duration_none")]
        ]))
        return SELECT_RESTRICTION_DURATION_LINK

    elif data.startswith("duration_"):
        duration = data.replace("duration_", "")
        item_to_add = context.user_data.pop('current_item_to_add')
        model = context.user_data.pop('item_model')
        column = context.user_data.pop('item_column')

        if db_add_item({column: item_to_add, 'restriction_duration': duration}, model, column):
            await query.message.reply_text("✅ تم الحفظ بنجاح مع مدة التقييد.")
        else:
            await query.message.reply_text("⚠️ هذا العنصر موجود بالفعل أو فشل الحفظ.")
        return ConversationHandler.END

# --- دوال إدارة القوائم ---

async def manage_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, model, column_name: str, add_cb: str, del_cb: str, back_cb: str):
    query = update.callback_query
    items_data = db_get_all_items(model, column_name)
    
    text_lines = []
    if items_data:
        for item in items_data:
            if isinstance(item, tuple) and len(item) == 2: # For BannedWord/Link with duration
                text_lines.append(f"- `{escape_markdown(item[0])}` (تقييد: {item[1]}) ")
            else:
                text_lines.append(f"- `{escape_markdown(item)}`")
        text = f"قائمة {item_type}:\n" + "\n".join(text_lines)
    else:
        text = f"قائمة {item_type}:\nلا يوجد عناصر."
    
    keyboard = [
        [InlineKeyboardButton("➕ إضافة", callback_data=add_cb)],
        [InlineKeyboardButton("🗑️ حذف", callback_data=del_cb)],
        [InlineKeyboardButton("⬅️ عودة", callback_data=back_cb)]
    ]
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    except BadRequest as e:
        if "Can't parse entities" in str(e):
            await query.edit_message_text(f"قائمة {item_type}:\n" + ("\n".join(f"- {item[0] if isinstance(item, tuple) else item}" for item in items_data) if items_data else "لا يوجد عناصر."), reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            raise e

async def add_item_start(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, state):
    query = update.callback_query
    await query.edit_message_text(f"أرسل {item_type} الذي تريد إضافته.")
    return state

async def delete_item_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, model, column_name: str, back_callback: str):
    query = update.callback_query
    items = db_get_all_items(model, column_name)
    if not items:
        await query.answer("لا يوجد عناصر لحذفها!", show_alert=True)
        return
    
    keyboard = [[InlineKeyboardButton(f"🗑️ {item[0] if isinstance(item, tuple) else item}", callback_data=f"confirm_delete_{model.__tablename__}_{item[0] if isinstance(item, tuple) else item}")] for item in items]
    keyboard.append([InlineKeyboardButton("⬅️ عودة", callback_data=back_callback)])
    await query.edit_message_text(f"اختر {item_type} الذي تريد حذفه:", reply_markup=InlineKeyboardMarkup(keyboard))

async def confirm_delete_item(update: Update, context: ContextTypes.DEFAULT_TYPE, model, column_name: str, item_type: str, add_cb: str, del_cb: str, back_cb: str):
    query = update.callback_query
    item_to_delete = query.data.split("_", 3)[3]
    
    if db_delete_item(item_to_delete, model, column_name):
        await query.answer("تم الحذف بنجاح!")
        await manage_list_menu(update, context, item_type, model, column_name, add_cb, del_cb, back_cb)
    else:
        await query.answer("فشل الحذف.", show_alert=True)

# --- معالجات المحادثات ---

async def save_item_with_duration_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, model, column_name: str, next_state):
    item = update.message.text.strip()
    context.user_data['current_item_to_add'] = item
    context.user_data['item_model'] = model
    context.user_data['item_column'] = column_name

    keyboard = [
        [InlineKeyboardButton("يوم", callback_data="duration_day")],
        [InlineKeyboardButton("أسبوع", callback_data="duration_week")],
        [InlineKeyboardButton("شهر", callback_data="duration_month")],
        [InlineKeyboardButton("بدون تقييد", callback_data="duration_none")]
    ]
    await update.message.reply_text("اختر مدة التقييد:", reply_markup=InlineKeyboardMarkup(keyboard))
    return next_state

async def save_item_without_duration(update: Update, context: ContextTypes.DEFAULT_TYPE, model, column_name: str):
    item = update.message.text.lower().strip()
    if db_add_item(item, model, column_name):
        await update.message.reply_text("✅ تم الحفظ بنجاح.")
    else:
        await update.message.reply_text("⚠️ هذا العنصر موجود بالفعل.")
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
    new_message = update.message.text
    set_setting('welcome_message', new_message)
    await update.message.reply_text(f"✅ تم حفظ رسالة الترحيب الجديدة:\n\n{new_message}")
    return ConversationHandler.END

async def set_auto_reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_message = update.message.text
    set_setting('auto_reply', new_message)
    await update.message.reply_text(f"✅ تم حفظ الرد التلقائي الجديد للرسائل الخاصة:\n\n{new_message}")
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

async def admin_reply_photo_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = context.user_data.pop('reply_user_id', None)
    if not user_id: return ConversationHandler.END

    photo_file_id = update.message.photo[-1].file_id if update.message.photo else None
    caption = update.message.caption if update.message.caption else ""

    if not photo_file_id:
        await update.message.reply_text("❌ لم يتم إرسال صورة. يرجى إرسال الصورة مع النص في الوصف (caption).")
        return ADMIN_REPLY_PHOTO_TEXT # Stay in this state until a photo is received

    try:
        await context.bot.send_photo(chat_id=user_id, photo=photo_file_id, caption=caption)
        await update.message.reply_text("✅ تم إرسال الصورة مع النص.")
    except Exception as e:
        await update.message.reply_text(f"❌ فشل إرسال الصورة: {e}")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم إلغاء العملية.")
    context.user_data.clear()
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
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "كلمة محظورة", ADD_BANNED_WORD), pattern="^add_banned_words$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "نمط رابط محظور", ADD_BANNED_LINK), pattern="^add_banned_links$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "بادئة رابط مسموح", ADD_WHITELISTED_LINK), pattern="^add_whitelisted_links$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "الرد التلقائي للخاص", SET_AUTO_REPLY), pattern="^set_auto_reply_start$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "رسالة الترحيب", SET_WELCOME_MESSAGE), pattern="^set_welcome_start$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "الكلمة المفتاحية", ADD_AUTO_REPLY_KEYWORD), pattern="^add_auto_reply_start$"),
            CallbackQueryHandler(button_handler, pattern="^reply_"),
            CallbackQueryHandler(button_handler, pattern="^send_photo_with_text$"),
            CallbackQueryHandler(button_handler, pattern="^duration_")
        ],
        states={
            ADD_BANNED_WORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: save_item_with_duration_prompt(u, c, BannedWord, 'word', SELECT_RESTRICTION_DURATION_WORD))],
            ADD_BANNED_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: save_item_with_duration_prompt(u, c, BannedLink, 'link_pattern', SELECT_RESTRICTION_DURATION_LINK))],
            ADD_WHITELISTED_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: save_item_without_duration(u, c, WhitelistedLink, 'link_prefix'))],
            SET_AUTO_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_auto_reply_message)],
            SET_WELCOME_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_welcome_message)],
            ADD_AUTO_REPLY_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_auto_reply_keyword)],
            ADD_AUTO_REPLY_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_auto_reply_text)],
            ADMIN_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply_message)],
            ADMIN_REPLY_PHOTO_TEXT: [MessageHandler(filters.PHOTO & ~filters.COMMAND, admin_reply_photo_text)],
            SELECT_RESTRICTION_DURATION_WORD: [CallbackQueryHandler(button_handler, pattern="^duration_")],
            SELECT_RESTRICTION_DURATION_LINK: [CallbackQueryHandler(button_handler, pattern="^duration_")]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        conversation_timeout=300
    )

    # --- إضافة المعالجات ---
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.Regex(r'^(يمان|admin)$') & filters.User(user_id=ADMIN_ID), admin_panel))
    
    application.add_handler(conv_handler)

    # --- معالجات الأزرار الديناميكية ---
    application.add_handler(CallbackQueryHandler(lambda u, c: manage_list_menu(u, c, "الكلمات المحظورة", BannedWord, "word", "add_banned_words", "delete_banned_words", "manage_banning"), pattern="^banned_words$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: manage_list_menu(u, c, "الروابط المحظورة", BannedLink, "link_pattern", "add_banned_links", "delete_banned_links", "manage_banning"), pattern="^banned_links$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: manage_list_menu(u, c, "الروابط المسموحة", WhitelistedLink, "link_prefix", "add_whitelisted_links", "delete_whitelisted_links", "manage_banning"), pattern="^whitelisted_links$"))
    
    application.add_handler(CallbackQueryHandler(lambda u, c: delete_item_menu(u, c, "كلمة", BannedWord, "word", "banned_words"), pattern="^delete_banned_words$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: delete_item_menu(u, c, "رابط", BannedLink, "link_pattern", "banned_links"), pattern="^delete_banned_links$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: delete_item_menu(u, c, "رابط مسموح", WhitelistedLink, "link_prefix", "whitelisted_links"), pattern="^delete_whitelisted_links$"))

    application.add_handler(CallbackQueryHandler(lambda u, c: confirm_delete_item(u, c, BannedWord, "word", "الكلمات المحظورة", "add_banned_words", "delete_banned_words", "manage_banning"), pattern="^confirm_delete_banned_words_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: confirm_delete_item(u, c, BannedLink, "link_pattern", "الروابط المحظورة", "add_banned_links", "delete_banned_links", "manage_banning"), pattern="^confirm_delete_banned_links_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: confirm_delete_item(u, c, WhitelistedLink, "link_prefix", "الروابط المسموحة", "add_whitelisted_links", "delete_whitelisted_links", "manage_banning"), pattern="^confirm_delete_whitelisted_links_"))

    application.add_handler(CallbackQueryHandler(lambda u, c: delete_item_menu(u, c, "رد تلقائي", AutoReply, "keyword", "manage_auto_replies"), pattern="^delete_auto_reply_menu$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: confirm_delete_item(u, c, AutoReply, "keyword", "الردود التلقائية", "add_auto_reply_start", "delete_auto_reply_menu", "manage_auto_replies"), pattern="^confirm_delete_auto_replies_"))

    # General button handler for menu navigation
    application.add_handler(CallbackQueryHandler(button_handler, pattern="^(main_menu|manage_banning|manage_auto_replies|manage_broadcast|manage_settings|check_blocked|close_panel)$"))

    # Message handler for group messages (must be after command and conv handlers)
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, message_handler))
    # Message handler for private messages (must be after command and conv handlers)
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE | filters.PHOTO & filters.ChatType.PRIVATE, private_message_handler))

    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()


