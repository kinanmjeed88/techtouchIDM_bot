# main.py (النسخة المصححة)
import os
import logging
import re
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
    get_setting, set_setting
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
ADD_BANNED_WORD, ADD_BANNED_LINK, ADD_WHITELISTED_LINK, SET_AUTO_REPLY, BROADCAST_MESSAGE, ADMIN_REPLY = range(6)

# --- دوال مساعدة ---
def escape_markdown(text: str) -> str:
    """تهريب الأحرف الخاصة في MarkdownV2."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# --- معالجات الرسائل ---

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """المعالج الرئيسي للرسائل في المجموعات."""
    user = update.effective_user
    chat = update.effective_chat
    message = update.message

    if not user or not message or not message.text:
        return

    add_or_update_user(user.id, user.full_name, user.username)

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

    text = message.text.lower()
    
    # 1. فحص الروابط
    banned_links = db_get_all_items(BannedLink)
    whitelisted_links = db_get_all_items(WhitelistedLink)
    urls = re.findall(r'(https?://\S+)', text)
    for url in urls:
        is_whitelisted = any(url.startswith(prefix) for prefix in whitelisted_links)
        if not is_whitelisted:
            if any(pattern in url for pattern in banned_links):
                await message.delete()
                await context.bot.send_message(chat.id, f"تم حذف رسالة من {user.mention_html()} لاحتوائها على رابط محظور.", parse_mode=ParseMode.HTML)
                return

    # 2. فحص الكلمات
    banned_words = db_get_all_items(BannedWord)
    if any(word in text for word in banned_words):
        await message.delete()
        await context.bot.send_message(chat.id, f"تم حذف رسالة من {user.mention_html()} لاحتوائها على كلمة محظورة.", parse_mode=ParseMode.HTML)
        return

async def private_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الرسائل الخاصة من المستخدمين."""
    user = update.effective_user
    add_or_update_user(user.id, user.full_name, user.username)

    auto_reply = get_setting('auto_reply')
    if auto_reply:
        await update.message.reply_text(auto_reply)

    user_info = escape_markdown(f"{user.full_name} (@{user.username})" if user.username else user.full_name)
    message_text = escape_markdown(update.message.text)
    
    text_to_forward = (
        f"📩 *رسالة جديدة من:* {user_info}\n"
        f"*ID:* `{user.id}`\n\n"
        f"```{message_text}```"
    )
    keyboard = [[InlineKeyboardButton("✍️ رد على المستخدم", callback_data=f"reply_{user.id}")]]
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
        "manage_broadcast": ("إدارة البث:", [
            [InlineKeyboardButton("✍️ إرسال بث جديد", callback_data="broadcast_start")],
            [InlineKeyboardButton(f"👥 فحص المحظورين ({get_blocked_user_count()})", callback_data="check_blocked")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ]),
        "manage_settings": ("إعدادات أخرى:", [
            [InlineKeyboardButton("🤖 تعديل الرد التلقائي", callback_data="set_auto_reply_start")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ])
    }

    if data in menu_map:
        text, keyboard_data = menu_map[data]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard_data))

    elif data == "check_blocked":
        await query.edit_message_text("جاري فحص المستخدمين...")
        # ... (الكود كما هو)
    
    elif data.startswith("reply_"):
        user_id = data.split("_")[1]
        context.user_data['reply_user_id'] = user_id
        await query.edit_message_text(f"أرسل الآن ردك للمستخدم صاحب الـ ID: {user_id}")
        return ADMIN_REPLY

    elif data == "close_panel":
        await query.message.delete()

# --- دوال إدارة القوائم ---

async def manage_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, model, add_callback: str, delete_callback: str, back_callback: str):
    query = update.callback_query
    items = db_get_all_items(model)
    text = f"قائمة {item_type}:\n" + ("\n".join(f"- `{escape_markdown(item)}`" for item in items) if items else "لا يوجد عناصر.")
    
    keyboard = [
        [InlineKeyboardButton("➕ إضافة", callback_data=add_callback)],
        [InlineKeyboardButton("🗑️ حذف", callback_data=delete_callback)],
        [InlineKeyboardButton("⬅️ عودة", callback_data=back_callback)]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

async def add_item_start(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, state):
    query = update.callback_query
    await query.edit_message_text(f"أرسل {item_type} الذي تريد إضافته.")
    return state

async def delete_item_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, model, back_callback: str):
    query = update.callback_query
    items = db_get_all_items(model)
    if not items:
        await query.answer("لا يوجد عناصر لحذفها!", show_alert=True)
        return
    
    keyboard = [[InlineKeyboardButton(f"🗑️ {item[:30]}", callback_data=f"confirm_delete_{model.__tablename__}_{item}")] for item in items]
    keyboard.append([InlineKeyboardButton("⬅️ عودة", callback_data=back_callback)])
    await query.edit_message_text(f"اختر {item_type} الذي تريد حذفه:", reply_markup=InlineKeyboardMarkup(keyboard))

async def confirm_delete_item(update: Update, context: ContextTypes.DEFAULT_TYPE, model, item_type: str, add_cb: str, del_cb: str, back_cb: str):
    query = update.callback_query
    item_to_delete = query.data.split("_", 3)[3]
    
    if db_delete_item(item_to_delete, model):
        await query.answer("تم الحذف بنجاح!")
        # Refresh menu by calling the manage_list_menu again
        await manage_list_menu(update, context, item_type, model, add_cb, del_cb, back_cb)
    else:
        await query.answer("فشل الحذف.", show_alert=True)

# --- معالجات المحادثات ---

async def save_item(update: Update, context: ContextTypes.DEFAULT_TYPE, model):
    item = update.message.text.lower().strip()
    if db_add_item(item, model):
        await update.message.reply_text("✅ تم الحفظ بنجاح.")
    else:
        await update.message.reply_text("⚠️ هذا العنصر موجود بالفعل.")
    return ConversationHandler.END

async def set_auto_reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting('auto_reply', update.message.text)
    await update.message.reply_text("✅ تم حفظ الرد التلقائي الجديد.")
    return ConversationHandler.END

async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (الكود كما هو)
    pass

async def admin_reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = context.user_data.pop('reply_user_id', None)
    if not user_id:
        return ConversationHandler.END
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
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "الرد التلقائي", SET_AUTO_REPLY), pattern="^set_auto_reply_start$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "رسالة البث", BROADCAST_MESSAGE), pattern="^broadcast_start$"),
            CallbackQueryHandler(button_handler, pattern="^reply_")
        ],
        states={
            ADD_BANNED_WORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: save_item(u, c, BannedWord))],
            ADD_BANNED_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: save_item(u, c, BannedLink))],
            ADD_WHITELISTED_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: save_item(u, c, WhitelistedLink))],
            SET_AUTO_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_auto_reply_message)],
            BROADCAST_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, send_broadcast)],
            ADMIN_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply_message)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        conversation_timeout=300
    )

    # --- إضافة المعالجات ---
    application.add_handler(CommandHandler("start", lambda u, c: add_or_update_user(u.effective_user.id, u.effective_user.full_name, u.effective_user.username)))
    application.add_handler(MessageHandler(filters.Regex(r'^يمان$') & filters.User(user_id=ADMIN_ID), admin_panel))
    
    application.add_handler(conv_handler)

    # --- معالجات الأزرار الديناميكية ---
    application.add_handler(CallbackQueryHandler(lambda u, c: manage_list_menu(u, c, "الكلمات المحظورة", BannedWord, "add_banned_words", "delete_banned_words", "manage_banning"), pattern="^banned_words$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: manage_list_menu(u, c, "الروابط المحظورة", BannedLink, "add_banned_links", "delete_banned_links", "manage_banning"), pattern="^banned_links$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: manage_list_menu(u, c, "الروابط المسموحة", WhitelistedLink, "add_whitelisted_links", "delete_whitelisted_links", "manage_banning"), pattern="^whitelisted_links$"))
    
    application.add_handler(CallbackQueryHandler(lambda u, c: delete_item_menu(u, c, "كلمة", BannedWord, "banned_words"), pattern="^delete_banned_words$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: delete_item_menu(u, c, "رابط", BannedLink, "banned_links"), pattern="^delete_banned_links$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: delete_item_menu(u, c, "رابط", WhitelistedLink, "whitelisted_links"), pattern="^delete_whitelisted_links$"))

    application.add_handler(CallbackQueryHandler(lambda u, c: confirm_delete_item(u, c, BannedWord, "الكلمات المحظورة", "add_banned_words", "delete_banned_words", "manage_banning"), pattern="^confirm_delete_banned_words_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: confirm_delete_item(u, c, BannedLink, "الروابط المحظورة", "add_banned_links", "delete_banned_links", "manage_banning"), pattern="^confirm_delete_banned_links_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: confirm_delete_item(u, c, WhitelistedLink, "الروابط المسموحة", "add_whitelisted_links", "delete_whitelisted_links", "manage_banning"), pattern="^confirm_delete_whitelisted_links_"))

    application.add_handler(CallbackQueryHandler(button_handler))

    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, private_message_handler))
    application.add_handler(MessageHandler(filters.ChatType.GROUP | filters.ChatType.SUPERGROUP, message_handler))

    logger.info("Bot is starting with new management features...")
    application.run_polling()

if __name__ == "__main__":
    main()
