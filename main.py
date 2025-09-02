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
 SET_AUTO_REPLY_PRIVATE_MESSAGE_TEXT_INPUT) = range(23)

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
    # ... (هذا الكود لم يتغير)
    pass

# --- معالجة الرسائل العادية في المجموعة ---
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (هذا الكود لم يتغير)
    pass

# --- لوحة تحكم الأدمن ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID: return

    keyboard = [
        [InlineKeyboardButton("🚫 إدارة الحظر", callback_data="manage_banning")],
        [InlineKeyboardButton("💬 إدارة الردود التلقائية", callback_data="manage_auto_replies")],
        [InlineKeyboardButton("📢 إدارة البث", callback_data="manage_broadcast")],
        [InlineKeyboardButton("📊 تقارير التفاعل", callback_data="manage_reports")],
        [InlineKeyboardButton("⚙️ إعدادات أخرى", callback_data="manage_settings")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="close_panel")]
    ]
    
    text_to_send = "لوحة تحكم الأدمن:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text_to_send, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text_to_send, reply_markup=InlineKeyboardMarkup(keyboard))

# --- معالج الأزرار الرئيسي ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    menu_map = {
        "main_menu": admin_panel,
        "manage_banning": lambda u, c: u.callback_query.edit_message_text("إدارة الحظر:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 الكلمات المحظورة", callback_data="banned_words_menu")],
            [InlineKeyboardButton("🔗 الروابط المحظورة", callback_data="banned_links_menu")],
            [InlineKeyboardButton("✅ الروابط المسموحة", callback_data="whitelisted_links_menu")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ])),
        "manage_auto_replies": lambda u, c: u.callback_query.edit_message_text("إدارة الردود التلقائية:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة رد تلقائي", callback_data="add_auto_reply_start")],
            [InlineKeyboardButton("🗑️ حذف رد تلقائي", callback_data="delete_auto_reply_menu")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ])),
        "manage_broadcast": lambda u, c: u.callback_query.edit_message_text("إدارة البث:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ إرسال بث جديد", callback_data="broadcast_start")],
            [InlineKeyboardButton(f"👥 فحص المحظورين ({get_blocked_user_count()})", callback_data="check_blocked")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ])),
        "manage_reports": lambda u, c: u.callback_query.edit_message_text("تقارير التفاعل:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📈 أكثر 5 متفاعلين", callback_data="top_active_users_report")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ])),
        "manage_settings": lambda u, c: u.callback_query.edit_message_text("إعدادات أخرى:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🤖 تعديل الرد التلقائي للخاص", callback_data="set_auto_reply_start")],
            [InlineKeyboardButton("👋 تعديل رسالة الترحيب", callback_data="set_welcome_start")],
            [InlineKeyboardButton("⚠️ رسالة التنبيه عند الحظر", callback_data="set_warning_message_start")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ]))
    }

    if data in menu_map:
        await menu_map[data](update, context)
    elif data == "close_panel":
        await query.message.delete()
    elif data == "check_blocked":
        await query.answer(f"عدد المستخدمين المحظورين: {get_blocked_user_count()}", show_alert=True)
    elif data == "top_active_users_report":
        await show_top_users_report(query)
    elif data == "banned_words_menu":
        await manage_list_menu(update, context, "الكلمات المحظورة", BannedWord, "word", "add_banned_word_start", "delete_banned_word_menu", "manage_banning")
    elif data == "banned_links_menu":
        await manage_list_menu(update, context, "الروابط المحظورة", BannedLink, "link_pattern", "add_banned_link_start", "delete_banned_link_menu", "manage_banning")
    elif data == "whitelisted_links_menu":
        await manage_list_menu(update, context, "الروابط المسموحة", WhitelistedLink, "link_prefix", "add_whitelisted_link_start", "delete_whitelisted_link_menu", "manage_banning")
    elif data == "delete_banned_word_menu":
        await confirm_delete_item(update, context, "كلمة محظورة", BannedWord, "word", "banned_words_menu")
    elif data.startswith("delete_item_"):
        _, model_name, item_value = data.split("_", 2)
        models = {"bannedword": BannedWord, "bannedlink": BannedLink, "whitelistedlink": WhitelistedLink}
        columns = {"bannedword": "word", "bannedlink": "link_pattern", "whitelistedlink": "link_prefix"}
        if db_delete_item(item_value, models[model_name], columns[model_name]):
            await query.answer("تم الحذف بنجاح!", show_alert=True)
            await menu_map["manage_banning"](update, context)
        else:
            await query.answer("فشل الحذف.", show_alert=True)

async def show_top_users_report(query: Update.callback_query):
    top_users = get_top_active_users()
    report_text = """📈 *أكثر 5 مستخدمين تفاعلاً:*\n\n"""
    if top_users:
        for i, user_obj in enumerate(top_users):
            user_display = escape_markdown_v2(user_obj.full_name or user_obj.username or str(user_obj.telegram_id))
            report_text += f"""{i+1}\\. {user_display} \\(`{escape_markdown_v2(str(user_obj.telegram_id))}`\\) \\- {user_obj.message_count} رسالة\n"""
    else:
        report_text += "لا يوجد مستخدمون متفاعلون بعد\\."
    await query.edit_message_text(report_text, parse_mode=ParseMode.MARKDOWN_V2)

async def manage_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, model, column_name: str, add_cb: str, del_cb: str, back_cb: str):
    items = db_get_all_items(model)
    text = f"*{escape_markdown_v2(title)}:*\n\n"
    if items:
        for item in items:
            value = getattr(item, column_name)
            mute_info = ""
            if hasattr(item, 'mute_duration') and item.mute_duration:
                mute_info = f" \\(تقييد: {item.mute_duration}\\)"
            text += f"\\- `{escape_markdown_v2(value)}`{escape_markdown_v2(mute_info)}\n"
    else:
        text += "القائمة فارغة\\."
    keyboard = [
        [InlineKeyboardButton("➕ إضافة", callback_data=add_cb)],
        [InlineKeyboardButton("🗑️ حذف", callback_data=del_cb)],
        [InlineKeyboardButton("⬅️ عودة", callback_data=back_cb)]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

async def confirm_delete_item(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, model, column: str, back_cb: str):
    items = db_get_all_items(model)
    keyboard = []
    if items:
        for item in items:
            value = getattr(item, column)
            keyboard.append([InlineKeyboardButton(f"🗑️ {value[:20]}", callback_data=f"delete_item_{model.__name__.lower()}_{value}")])
    keyboard.append([InlineKeyboardButton("⬅️ عودة", callback_data=back_cb)])
    await query.edit_message_text(f"اختر {title} الذي تريد حذفه:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- دوال المحادثات ---
async def add_item_start(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, next_state: int, model, column: str, requires_mute: bool = False):
    query = update.callback_query
    await query.answer()
    context.user_data.update({'item_type': item_type, 'model': model, 'column': column, 'requires_mute': requires_mute})
    cancel_button = [[InlineKeyboardButton("إلغاء", callback_data="cancel_conv")]]
    await query.edit_message_text(f"أرسل الآن *{item_type}* الذي تريد إضافته.", reply_markup=InlineKeyboardMarkup(cancel_button), parse_mode=ParseMode.MARKDOWN_V2)
    return next_state

async def received_item_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    if context.user_data.get('requires_mute'):
        context.user_data['value'] = user_input
        keyboard = [[InlineKeyboardButton("بدون تقييد", callback_data="mute_none")], [InlineKeyboardButton("يوم", callback_data="mute_day")], [InlineKeyboardButton("أسبوع", callback_data="mute_week")], [InlineKeyboardButton("شهر", callback_data="mute_month")], [InlineKeyboardButton("إلغاء", callback_data="cancel_conv")]]
        await update.message.reply_text("اختر مدة التقييد:", reply_markup=InlineKeyboardMarkup(keyboard))
        return SET_MUTE_DURATION_BANNED_WORD
    else:
        model = context.user_data['model']
        column = context.user_data['column']
        if db_add_item({column: user_input}, model, column):
            await update.message.reply_text("✅ تم الحفظ بنجاح.")
        else:
            await update.message.reply_text("⚠️ فشل الحفظ.")
        context.user_data.clear()
        return ConversationHandler.END

async def received_mute_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    duration = query.data.split('_')[1] if query.data != 'mute_none' else None
    value = context.user_data['value']
    model = context.user_data['model']
    column = context.user_data['column']
    if db_add_item({column: value, 'mute_duration': duration}, model, column):
        await query.edit_message_text("✅ تم الحفظ بنجاح.")
    else:
        await query.edit_message_text("⚠️ فشل الحفظ.")
    context.user_data.clear()
    return ConversationHandler.END

async def set_setting_start(update: Update, context: ContextTypes.DEFAULT_TYPE, setting_name: str, next_state: int, setting_key: str):
    query = update.callback_query
    await query.answer()
    context.user_data['setting_key'] = setting_key
    cancel_button = [[InlineKeyboardButton("إلغاء", callback_data="cancel_conv")]]
    await query.edit_message_text(f"أرسل الآن *{setting_name}* الجديدة.", reply_markup=InlineKeyboardMarkup(cancel_button), parse_mode=ParseMode.MARKDOWN_V2)
    return next_state

async def received_setting_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_value = update.message.text
    setting_key = context.user_data['setting_key']
    set_setting(setting_key, new_value)
    await update.message.reply_text("✅ تم حفظ الإعداد بنجاح.")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("تم إلغاء العملية.")
    else:
        await update.message.reply_text("تم إلغاء العملية.")
    context.user_data.clear()
    await admin_panel(update, context)
    return ConversationHandler.END

# --- الدالة الرئيسية ---
def main():
    if not TELEGRAM_TOKEN or not ADMIN_ID:
        logger.critical("FATAL: TELEGRAM_TOKEN or ADMIN_ID environment variables are not set.")
        return

    init_db()
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "كلمة محظورة", ADD_BANNED_WORD, BannedWord, "word", True), pattern="^add_banned_word_start$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "رابط محظور", ADD_BANNED_LINK, BannedLink, "link_pattern", True), pattern="^add_banned_link_start$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "رابط مسموح", ADD_WHITELISTED_LINK, WhitelistedLink, "link_prefix"), pattern="^add_whitelisted_link_start$"),
            CallbackQueryHandler(lambda u, c: set_setting_start(u, c, "رسالة الترحيب", SET_WELCOME_MESSAGE, "welcome_message"), pattern="^set_welcome_start$"),
            CallbackQueryHandler(lambda u, c: set_setting_start(u, c, "رسالة التنبيه", SET_WARNING_MESSAGE, "warning_message"), pattern="^set_warning_message_start$"),
        ],
        states={
            ADD_BANNED_WORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_item_input)],
            ADD_BANNED_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_item_input)],
            ADD_WHITELISTED_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_item_input)],
            SET_MUTE_DURATION_BANNED_WORD: [CallbackQueryHandler(received_mute_duration, pattern="^mute_")],
            SET_WELCOME_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_setting_input)],
            SET_WARNING_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_setting_input)],
        },
        fallbacks=[CallbackQueryHandler(cancel, pattern="^cancel_conv$"), CommandHandler('cancel', cancel)],
        per_message=False,
        conversation_timeout=300
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.Regex(r'^يمان$') & filters.User(user_id=ADMIN_ID), admin_panel))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("Bot is starting with original structure and fixed handlers...")
    application.run_polling()

if __name__ == "__main__":
    main()
