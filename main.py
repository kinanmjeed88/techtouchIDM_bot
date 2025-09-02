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
    ADD_BANNED_WORD, ADD_BANNED_LINK, SET_MUTE_DURATION,
    ADD_WHITELISTED_LINK,
    ADD_AUTO_REPLY_KEYWORD, ADD_AUTO_REPLY_TEXT,
    SET_WELCOME_MESSAGE, SET_WARNING_MESSAGE, SET_AUTO_REPLY_PRIVATE,
    BROADCAST_MESSAGE, BROADCAST_CONFIRM
) = range(11)


# --- دوال مساعدة ---
def escape_markdown_v2(text: str) -> str:
    if not isinstance(text, str): return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

async def reply_or_edit(update: Update, text: str, reply_markup=None):
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
    except BadRequest:
        plain_text = re.sub(r'[\\*`_\[\]\(\)~>#\+\-=\|{}\.!]', '', text)
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

# --- معالجات الرسائل ---
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # هذا مجرد مثال مختصر، يجب استخدام الكود الكامل من نسختك
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        return
    add_or_update_user(user.id, user.full_name, user.username)
    increment_user_message_count(user.id)
    # ... بقية منطق معالجة الرسائل

# --- لوحة تحكم الأدمن ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID: return

    text = "لوحة تحكم الأدمن:"
    keyboard = [
        [InlineKeyboardButton("🚫 إدارة الحظر", callback_data="menu_banning")],
        [InlineKeyboardButton("💬 إدارة الردود", callback_data="menu_auto_replies")],
        [InlineKeyboardButton("📢 إدارة البث", callback_data="menu_broadcast")],
        [InlineKeyboardButton("📊 تقارير التفاعل", callback_data="menu_reports")],
        [InlineKeyboardButton("⚙️ إعدادات أخرى", callback_data="menu_settings")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="close_panel")]
    ]
    await reply_or_edit(update, text, InlineKeyboardMarkup(keyboard))

# --- معالج الأزرار الرئيسي ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_main": await admin_panel(update, context)
    elif data == "close_panel": await query.message.delete()
    elif data == "menu_banning": await show_banning_menu(update, context)
    elif data == "menu_auto_replies": await show_auto_replies_menu(update, context)
    elif data == "menu_broadcast": await show_broadcast_menu(update, context)
    elif data == "menu_reports": await show_reports_menu(update, context)
    elif data == "menu_settings": await show_settings_menu(update, context)
    elif data == "list_banned_words": await manage_list_menu(update, context, "الكلمات المحظورة", BannedWord, "word", "add_banned_word", "delete_bannedword", "menu_banning")
    elif data == "list_banned_links": await manage_list_menu(update, context, "الروابط المحظورة", BannedLink, "link_pattern", "add_banned_link", "delete_bannedlink", "menu_banning")
    elif data == "list_whitelisted_links": await manage_list_menu(update, context, "الروابط المسموحة", WhitelistedLink, "link_prefix", "add_whitelisted_link", "delete_whitelistedlink", "menu_banning")
    elif data.startswith("delete_"):
        parts = data.split("_", 2)
        action, model_name, item_value = parts
        models = {"bannedword": BannedWord, "bannedlink": BannedLink, "whitelistedlink": WhitelistedLink, "autoreply": AutoReply}
        columns = {"bannedword": "word", "bannedlink": "link_pattern", "whitelistedlink": "link_prefix", "autoreply": "keyword"}
        model = models[model_name]
        column = columns[model_name]
        if db_delete_item(item_value, model, column):
            await query.answer("تم الحذف بنجاح!", show_alert=True)
            await show_banning_menu(update, context)
        else:
            await query.answer("فشل الحذف.", show_alert=True)
    elif data == "top_active_users_report": await show_top_users_report(query)
    elif data == "check_blocked": await query.answer(f"عدد المستخدمين المحظورين: {get_blocked_user_count()}", show_alert=True)

# --- دوال عرض القوائم ---
async def show_banning_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📝 الكلمات المحظورة", callback_data="list_banned_words")],
        [InlineKeyboardButton("🔗 الروابط المحظورة", callback_data="list_banned_links")],
        [InlineKeyboardButton("✅ الروابط المسموحة", callback_data="list_whitelisted_links")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="menu_main")]
    ]
    await reply_or_edit(update, "إدارة الحظر:", InlineKeyboardMarkup(keyboard))

async def show_auto_replies_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_or_edit(update, "هذه الميزة قيد الإنشاء.")

async def show_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("✍️ إرسال بث جديد", callback_data="broadcast_start")],
        [InlineKeyboardButton(f"👥 فحص المحظورين ({get_blocked_user_count()})", callback_data="check_blocked")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="menu_main")]
    ]
    await reply_or_edit(update, "إدارة البث:", InlineKeyboardMarkup(keyboard))

async def show_reports_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📈 أكثر 5 متفاعلين", callback_data="top_active_users_report")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="menu_main")]
    ]
    await reply_or_edit(update, "تقارير التفاعل:", InlineKeyboardMarkup(keyboard))

async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("👋 تعديل رسالة الترحيب", callback_data="set_welcome_start")],
        [InlineKeyboardButton("⚠️ تعديل رسالة التنبيه", callback_data="set_warning_start")],
        [InlineKeyboardButton("🤖 تعديل رد الخاص", callback_data="set_autoreply_private_start")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="menu_main")]
    ]
    await reply_or_edit(update, "إعدادات أخرى:", InlineKeyboardMarkup(keyboard))

async def manage_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, model, column: str, add_cb: str, del_cb_prefix: str, back_cb: str):
    items = db_get_all_items(model)
    text = f"*{escape_markdown_v2(title)}:*\n\n"
    del_keyboard = []
    if items:
        for item in items:
            value = getattr(item, column)
            text += f"\\- `{escape_markdown_v2(value)}`\n"
            del_keyboard.append([InlineKeyboardButton(f"🗑️ {value[:20]}", callback_data=f"{del_cb_prefix}_{value}")])
    else:
        text += "القائمة فارغة\\."
    keyboard = [
        [InlineKeyboardButton("➕ إضافة عنصر جديد", callback_data=add_cb)],
        *del_keyboard,
        [InlineKeyboardButton("⬅️ عودة", callback_data=back_cb)]
    ]
    await reply_or_edit(update, text, InlineKeyboardMarkup(keyboard))

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
async def add_item_start(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, next_state: int, setting_key: str = None):
    query = update.callback_query
    await query.answer()
    context.user_data['item_type'] = item_type
    if setting_key:
        context.user_data['setting_key'] = setting_key
    await query.edit_message_text(f"أرسل الآن *{item_type}* الذي تريد إضافته/تعديله.", parse_mode=ParseMode.MARKDOWN)
    return next_state

async def add_banned_item_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    item_value = update.message.text
    context.user_data['item_value'] = item_value
    keyboard = [
        [InlineKeyboardButton("بدون تقييد", callback_data="mute_none")],
        [InlineKeyboardButton("يوم", callback_data="mute_day")],
        [InlineKeyboardButton("أسبوع", callback_data="mute_week")],
        [InlineKeyboardButton("شهر", callback_data="mute_month")]
    ]
    await update.message.reply_text("اختر مدة التقييد عند مخالفة هذا العنصر:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SET_MUTE_DURATION

async def set_mute_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mute_duration = query.data.split('_')[1]
    if mute_duration == 'none': mute_duration = None
    item_type = context.user_data['item_type']
    item_value = context.user_data['item_value']
    model = BannedWord if item_type == "كلمة محظورة" else BannedLink
    column = "word" if item_type == "كلمة محظورة" else "link_pattern"
    if db_add_item({column: item_value, 'mute_duration': mute_duration}, model, column):
        await query.edit_message_text(f"✅ تم حفظ '{item_value}' بنجاح.")
    else:
        await query.edit_message_text(f"⚠️ فشل حفظ '{item_value}'. قد يكون موجوداً بالفعل.")
    context.user_data.clear()
    return ConversationHandler.END

async def add_whitelisted_link_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link_prefix = update.message.text
    if db_add_item({'link_prefix': link_prefix}, WhitelistedLink, 'link_prefix'):
        await update.message.reply_text(f"✅ تم حفظ البادئة '{link_prefix}' كرابط مسموح به.")
    else:
        await update.message.reply_text("⚠️ فشل الحفظ.")
    return ConversationHandler.END

async def set_setting_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    setting_key = context.user_data['setting_key']
    new_value = update.message.text
    set_setting(setting_key, new_value)
    await update.message.reply_text("✅ تم حفظ الإعداد بنجاح.")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("تم إلغاء العملية.")
    else:
        await update.message.reply_text("تم إلغاء العملية.")
    context.user_data.clear()
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
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "كلمة محظورة", ADD_BANNED_WORD), pattern="^add_banned_word$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "رابط محظور", ADD_BANNED_LINK), pattern="^add_banned_link$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "رابط مسموح", ADD_WHITELISTED_LINK), pattern="^add_whitelisted_link$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "رسالة الترحيب", SET_WELCOME_MESSAGE, 'welcome_message'), pattern="^set_welcome_start$"),
            # [FIX] تم تصحيح الخطأ الإملائي هنا
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "رسالة التنبيه", SET_WARNING_MESSAGE, 'warning_message'), pattern="^set_warning_start$"),
        ],
        states={
            ADD_BANNED_WORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_banned_item_received)],
            ADD_BANNED_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_banned_item_received)],
            SET_MUTE_DURATION: [CallbackQueryHandler(set_mute_duration, pattern="^mute_")],
            ADD_WHITELISTED_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_whitelisted_link_received)],
            SET_WELCOME_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_setting_received)],
            SET_WARNING_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_setting_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel), CallbackQueryHandler(cancel, pattern="^cancel$")],
        conversation_timeout=300
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.Regex(r'^يمان$') & filters.User(user_id=ADMIN_ID), admin_panel))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
