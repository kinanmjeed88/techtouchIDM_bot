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
    mute_user, is_user_muted,
    increment_user_message_count, get_top_active_users, SessionLocal
)

# إعداد نظام التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# قراءة المتغيرات
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID'))

# مراحل المحادثة
(
    AWAITING_BANNED_WORD, AWAITING_BANNED_LINK, AWAITING_WHITELISTED_LINK,
    AWAITING_MUTE_DURATION,
    AWAITING_AUTO_REPLY_KEYWORD, AWAITING_AUTO_REPLY_TEXT,
    AWAITING_WELCOME_MESSAGE, AWAITING_WARNING_MESSAGE, AWAITING_AUTO_REPLY_PRIVATE,
    AWAITING_BROADCAST_MESSAGE
) = range(10)

# --- دوال مساعدة ---
def escape_markdown_v2(text: str) -> str:
    if not isinstance(text, str): return ""
    return re.sub(r'([_*\[\]()~`>#\+\-=|{}\.!])', r'\\\1', text)

async def reply_or_edit(update: Update, text: str, reply_markup=None):
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
    except BadRequest:
        plain_text = re.sub(r'[\\*`_\[\]\(\)~>#\+\-=|{}\.!]', '', text)
        if update.callback_query:
            await update.callback_query.edit_message_text(plain_text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(plain_text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Failed to send/edit message: {e}")

# --- وظائف البوت الأساسية ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_or_update_user(user.id, user.full_name, user.username)
    welcome_message = get_setting('welcome_message') or "أهلاً بك في البوت!"
    await update.message.reply_text(welcome_message)

# --- معالج الرسائل العام ---
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # هذا الكود لم يتغير، لذا تم حذفه للاختصار. استخدم نسختك الأصلية هنا.
    pass

# --- لوحة تحكم الأدمن ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID: return

    text = "لوحة تحكم الأدمن:"
    keyboard = [
        [InlineKeyboardButton("🚫 إدارة الحظر", callback_data="manage_banning")],
        [InlineKeyboardButton("💬 إدارة الردود التلقائية", callback_data="manage_auto_replies")],
        [InlineKeyboardButton("📢 إدارة البث", callback_data="manage_broadcast")],
        [InlineKeyboardButton("📊 تقارير التفاعل", callback_data="manage_reports")],
        [InlineKeyboardButton("⚙️ إعدادات أخرى", callback_data="manage_settings")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="close_panel")]
    ]
    await reply_or_edit(update, text, InlineKeyboardMarkup(keyboard))

# --- معالج الأزرار (للتنقل والحذف) ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # القوائم الرئيسية
    if data == "main_menu": await admin_panel(update, context)
    elif data == "close_panel": await query.message.delete()
    elif data == "manage_banning": await show_banning_menu(update, context)
    elif data == "manage_auto_replies": await show_auto_replies_menu(update, context)
    elif data == "manage_broadcast": await show_broadcast_menu(update, context)
    elif data == "manage_reports": await show_reports_menu(update, context)
    elif data == "manage_settings": await show_settings_menu(update, context)

    # قوائم إدارة الحظر
    elif data == "banned_words_menu": await manage_list_menu(update, context, "الكلمات المحظورة", BannedWord, "word", "add_banned_word_start", "delete_banned_word_menu", "manage_banning")
    elif data == "banned_links_menu": await manage_list_menu(update, context, "الروابط المحظورة", BannedLink, "link_pattern", "add_banned_link_start", "delete_banned_link_menu", "manage_banning")
    elif data == "whitelisted_links_menu": await manage_list_menu(update, context, "الروابط المسموحة", WhitelistedLink, "link_prefix", "add_whitelisted_link_start", "delete_whitelisted_link_menu", "manage_banning")

    # قوائم الحذف
    elif data == "delete_banned_word_menu": await confirm_delete_item(update, context, "كلمة محظورة", BannedWord, "word", "banned_words_menu")
    elif data == "delete_banned_link_menu": await confirm_delete_item(update, context, "رابط محظور", BannedLink, "link_pattern", "banned_links_menu")
    elif data == "delete_whitelisted_link_menu": await confirm_delete_item(update, context, "رابط مسموح", WhitelistedLink, "link_prefix", "whitelisted_links_menu")
    elif data.startswith("delete_item_"):
        _, model_name, item_value = data.split("_", 2)
        models = {"bannedword": BannedWord, "bannedlink": BannedLink, "whitelistedlink": WhitelistedLink}
        columns = {"bannedword": "word", "bannedlink": "link_pattern", "whitelistedlink": "link_prefix"}
        model = models[model_name]
        column = columns[model_name]
        if db_delete_item(item_value, model, column):
            await query.answer("تم الحذف بنجاح!", show_alert=True)
            await show_banning_menu(update, context) # Refresh menu
        else:
            await query.answer("فشل الحذف.", show_alert=True)

    # تقارير
    elif data == "top_active_users_report": await show_top_users_report(query)
    elif data == "check_blocked": await query.answer(f"عدد المستخدمين المحظورين: {get_blocked_user_count()}", show_alert=True)

# --- دوال عرض القوائم ---
async def show_banning_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📝 الكلمات المحظورة", callback_data="banned_words_menu")],
        [InlineKeyboardButton("🔗 الروابط المحظورة", callback_data="banned_links_menu")],
        [InlineKeyboardButton("✅ الروابط المسموحة", callback_data="whitelisted_links_menu")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
    ]
    await reply_or_edit(update, "إدارة الحظر:", InlineKeyboardMarkup(keyboard))

async def show_auto_replies_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_or_edit(update, "هذه الميزة قيد الإنشاء.")

async def show_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_or_edit(update, "هذه الميزة قيد الإنشاء.")

async def show_reports_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("📈 أكثر 5 متفاعلين", callback_data="top_active_users_report")], [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]]
    await reply_or_edit(update, "تقارير التفاعل:", InlineKeyboardMarkup(keyboard))

async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("👋 تعديل رسالة الترحيب", callback_data="set_welcome_start")],
        [InlineKeyboardButton("⚠️ تعديل رسالة التنبيه", callback_data="set_warning_start")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
    ]
    await reply_or_edit(update, "إعدادات أخرى:", InlineKeyboardMarkup(keyboard))

async def manage_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, model, column: str, add_cb: str, del_cb: str, back_cb: str):
    items = db_get_all_items(model)
    text = f"*{escape_markdown_v2(title)}:*\n\n"
    if items:
        for item in items:
            value = getattr(item, column)
            text += f"\\- `{escape_markdown_v2(value)}`\n"
    else:
        text += "القائمة فارغة\\."
    keyboard = [
        [InlineKeyboardButton("➕ إضافة", callback_data=add_cb)],
        [InlineKeyboardButton("🗑️ حذف", callback_data=del_cb)],
        [InlineKeyboardButton("⬅️ عودة", callback_data=back_cb)]
    ]
    await reply_or_edit(update, text, InlineKeyboardMarkup(keyboard))

async def confirm_delete_item(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, model, column: str, back_cb: str):
    items = db_get_all_items(model)
    keyboard = []
    if items:
        for item in items:
            value = getattr(item, column)
            keyboard.append([InlineKeyboardButton(f"🗑️ {value[:20]}", callback_data=f"delete_item_{model.__name__.lower()}_{value}")])
    keyboard.append([InlineKeyboardButton("⬅️ عودة", callback_data=back_cb)])
    await reply_or_edit(update, f"اختر {title} الذي تريد حذفه:", InlineKeyboardMarkup(keyboard))

async def show_top_users_report(query: Update.callback_query):
    top_users = get_top_active_users()
    report_text = """📈 *أكثر 5 مستخدمين تفاعلاً:*\n\n"""
    if top_users:
        for i, user in enumerate(top_users):
            display_name = escape_markdown_v2(user.full_name or user.username or f"User {user.telegram_id}")
            report_text += f"""{i+1}\\. {display_name} \\- {user.message_count} رسالة\n"""
    else:
        report_text += "لا يوجد مستخدمون متفاعلون بعد\\."
    await reply_or_edit(query, report_text)

# --- معالج المحادثات ---
async def start_conversation_for_item(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, next_state: int, model, column: str, requires_mute: bool = False):
    query = update.callback_query
    await query.answer()
    context.user_data.update({'item_type': item_type, 'model': model, 'column': column, 'requires_mute': requires_mute})
    cancel_button = [[InlineKeyboardButton("إلغاء", callback_data="cancel_conv")]]
    await query.edit_message_text(f"أرسل الآن *{item_type}* الذي تريد إضافته.", reply_markup=InlineKeyboardMarkup(cancel_button), parse_mode=ParseMode.MARKDOWN_V2)
    return next_state

async def start_conversation_for_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, setting_name: str, next_state: int, setting_key: str):
    query = update.callback_query
    await query.answer()
    context.user_data.update({'setting_key': setting_key})
    cancel_button = [[InlineKeyboardButton("إلغاء", callback_data="cancel_conv")]]
    await query.edit_message_text(f"أرسل الآن *{setting_name}* الجديدة.", reply_markup=InlineKeyboardMarkup(cancel_button), parse_mode=ParseMode.MARKDOWN_V2)
    return next_state

async def received_item_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    context.user_data['value'] = user_input
    if context.user_data.get('requires_mute'):
        keyboard = [
            [InlineKeyboardButton("بدون تقييد", callback_data="mute_none")],
            [InlineKeyboardButton("يوم", callback_data="mute_day")],
            [InlineKeyboardButton("أسبوع", callback_data="mute_week")],
            [InlineKeyboardButton("شهر", callback_data="mute_month")],
            [InlineKeyboardButton("إلغاء", callback_data="cancel_conv")]
        ]
        await update.message.reply_text("اختر مدة التقييد:", reply_markup=InlineKeyboardMarkup(keyboard))
        return AWAITING_MUTE_DURATION
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

async def received_setting_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_value = update.message.text
    setting_key = context.user_data['setting_key']
    set_setting(setting_key, new_value)
    await update.message.reply_text("✅ تم حفظ الإعداد بنجاح.")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_or_edit(update, "تم إلغاء العملية.")
    context.user_data.clear()
    await admin_panel(update, context)
    return ConversationHandler.END

# --- الدالة الرئيسية ---
def main():
    if not TELEGRAM_TOKEN or not ADMIN_ID:
        logger.critical("FATAL: TELEGRAM_TOKEN or ADMIN_ID are not set.")
        return

    init_db()
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(lambda u, c: start_conversation_for_item(u, c, "كلمة محظورة", AWAITING_BANNED_WORD, BannedWord, "word", True), pattern="^add_banned_word_start$"),
            CallbackQueryHandler(lambda u, c: start_conversation_for_item(u, c, "رابط محظور", AWAITING_BANNED_LINK, BannedLink, "link_pattern", True), pattern="^add_banned_link_start$"),
            CallbackQueryHandler(lambda u, c: start_conversation_for_item(u, c, "رابط مسموح", AWAITING_WHITELISTED_LINK, WhitelistedLink, "link_prefix"), pattern="^add_whitelisted_link_start$"),
            CallbackQueryHandler(lambda u, c: start_conversation_for_setting(u, c, "رسالة الترحيب", AWAITING_WELCOME_MESSAGE, "welcome_message"), pattern="^set_welcome_start$"),
            CallbackQueryHandler(lambda u, c: start_conversation_for_setting(u, c, "رسالة التنبيه", AWAITING_WARNING_MESSAGE, "warning_message"), pattern="^set_warning_start$"),
        ],
        states={
            AWAITING_BANNED_WORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_item_input)],
            AWAITING_BANNED_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_item_input)],
            AWAITING_WHITELISTED_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_item_input)],
            AWAITING_MUTE_DURATION: [CallbackQueryHandler(received_mute_duration, pattern="^mute_")],
            AWAITING_WELCOME_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_setting_input)],
            AWAITING_WARNING_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_setting_input)],
        },
        fallbacks=[CallbackQueryHandler(cancel_conversation, pattern="^cancel_conv$"), CommandHandler('cancel', cancel_conversation)],
        per_message=False
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.Regex(r'^يمان$') & filters.User(user_id=ADMIN_ID), admin_panel))
    
    # يجب أن يكون معالج المحادثات قبل المعالجات العامة
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("Bot is starting with fixed handlers...")
    application.run_polling()

if __name__ == "__main__":
    main()
