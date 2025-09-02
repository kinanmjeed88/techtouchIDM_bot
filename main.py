import os
import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
    CallbackQueryHandler, ConversationHandler
)
from telegram.constants import ParseMode
from telegram.error import BadRequest

# استيراد الوحدات المخصصة
from database import (
    init_db, add_or_update_user, get_all_active_users, set_user_blocked, get_blocked_user_count,
    db_add_item, db_get_all_items, db_delete_item, BannedWord, BannedLink, WhitelistedLink,
    get_setting, set_setting, AutoReply, get_all_auto_replies,
    mute_user, is_user_muted, increment_user_message_count, get_top_active_users
)

# --- الإعدادات الأولية ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID'))

# --- مراحل المحادثة (تم تبسيطها) ---
AWAITING_INPUT, AWAITING_MUTE_DURATION = range(2)

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

# --- معالج الرسائل العام (للتحقق من الحظر والردود التلقائية) ---
async def general_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not update.message or not update.message.text: return

    add_or_update_user(user.id, user.full_name, user.username)
    increment_user_message_count(user.id)
    
    # ... (منطق التحقق من الكلمات والروابط المحظورة يوضع هنا)
    # هذا الكود لم يتغير، لذا تم حذفه للاختصار

# --- لوحة تحكم الأدمن والقوائم ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🚫 إدارة الحظر", callback_data="menu_banning")],
        [InlineKeyboardButton("💬 إدارة الردود", callback_data="menu_auto_replies")],
        [InlineKeyboardButton("⚙️ إعدادات أخرى", callback_data="menu_settings")],
        [InlineKeyboardButton("📊 التقارير", callback_data="menu_reports")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="close_panel")]
    ]
    await reply_or_edit(update, "لوحة تحكم الأدمن:", InlineKeyboardMarkup(keyboard))

async def show_banning_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📝 الكلمات المحظورة", callback_data="list_banned_words")],
        [InlineKeyboardButton("🔗 الروابط المحظورة", callback_data="list_banned_links")],
        [InlineKeyboardButton("✅ الروابط المسموحة", callback_data="list_whitelisted_links")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="menu_main")]
    ]
    await reply_or_edit(update, "إدارة الحظر:", InlineKeyboardMarkup(keyboard))

async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("👋 تعديل رسالة الترحيب", callback_data="action_set_welcome_message")],
        [InlineKeyboardButton("⚠️ تعديل رسالة التنبيه", callback_data="action_set_warning_message")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="menu_main")]
    ]
    await reply_or_edit(update, "إعدادات أخرى:", InlineKeyboardMarkup(keyboard))

async def manage_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, model, column: str, add_cb: str, del_prefix: str, back_cb: str):
    items = db_get_all_items(model)
    text = f"*{escape_markdown_v2(title)}:*\n\n"
    keyboard = [[InlineKeyboardButton("➕ إضافة عنصر جديد", callback_data=add_cb)]]
    if items:
        for item in items:
            value = getattr(item, column)
            text += f"\\- `{escape_markdown_v2(value)}`\n"
            keyboard.append([InlineKeyboardButton(f"🗑️ {value[:20]}", callback_data=f"{del_prefix}_{value}")])
    else:
        text += "القائمة فارغة\\."
    keyboard.append([InlineKeyboardButton("⬅️ عودة", callback_data=back_cb)])
    await reply_or_edit(update, text, InlineKeyboardMarkup(keyboard))

# --- معالج الأزرار (للتنقل والحذف الفوري) ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_main": await admin_panel(update, context)
    elif data == "close_panel": await query.message.delete()
    elif data == "menu_banning": await show_banning_menu(update, context)
    elif data == "menu_settings": await show_settings_menu(update, context)
    elif data == "list_banned_words": await manage_list_menu(update, context, "الكلمات المحظورة", BannedWord, "word", "action_add_banned_word", "delete_bannedword", "menu_banning")
    elif data == "list_banned_links": await manage_list_menu(update, context, "الروابط المحظورة", BannedLink, "link_pattern", "action_add_banned_link", "delete_bannedlink", "menu_banning")
    elif data == "list_whitelisted_links": await manage_list_menu(update, context, "الروابط المسموحة", WhitelistedLink, "link_prefix", "action_add_whitelisted_link", "delete_whitelistedlink", "menu_banning")
    elif data.startswith("delete_"):
        _, model_name, item_value = data.split("_", 2)
        models = {"bannedword": BannedWord, "bannedlink": BannedLink, "whitelistedlink": WhitelistedLink}
        columns = {"bannedword": "word", "bannedlink": "link_pattern", "whitelistedlink": "link_prefix"}
        if db_delete_item(item_value, models[model_name], columns[model_name]):
            await query.answer("تم الحذف بنجاح!", show_alert=True)
            await show_banning_menu(update, context) # أعد تحميل قائمة الحظر
        else:
            await query.answer("فشل الحذف.", show_alert=True)

# --- معالج المحادثات (للعمليات متعددة الخطوات) ---
async def start_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تبدأ محادثة لطلب إدخال من المستخدم."""
    query = update.callback_query
    action = query.data # e.g., "action_add_banned_word"
    context.user_data['action'] = action
    
    prompts = {
        "action_add_banned_word": "أرسل الكلمة التي تريد حظرها:",
        "action_add_banned_link": "أرسل نمط الرابط الذي تريد حظره:",
        "action_add_whitelisted_link": "أرسل بادئة الرابط التي تريد السماح بها:",
        "action_set_welcome_message": "أرسل رسالة الترحيب الجديدة:",
        "action_set_warning_message": "أرسل رسالة التنبيه الجديدة عند الحظر:",
    }
    prompt_text = prompts.get(action, "إدخال غير معروف.")
    
    cancel_button = [[InlineKeyboardButton("إلغاء", callback_data="cancel_conv")]]
    await reply_or_edit(update, prompt_text, InlineKeyboardMarkup(cancel_button))
    return AWAITING_INPUT

async def handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعالج الإدخال النصي من المستخدم داخل المحادثة."""
    action = context.user_data.get('action')
    user_input = update.message.text

    # التعامل مع الإعدادات
    if action in ["action_set_welcome_message", "action_set_warning_message"]:
        setting_keys = {
            "action_set_welcome_message": "welcome_message",
            "action_set_warning_message": "warning_message",
        }
        set_setting(setting_keys[action], user_input)
        await update.message.reply_text("✅ تم حفظ الإعداد بنجاح.")
        return ConversationHandler.END

    # التعامل مع الروابط المسموحة
    if action == "action_add_whitelisted_link":
        db_add_item({'link_prefix': user_input}, WhitelistedLink, 'link_prefix')
        await update.message.reply_text("✅ تم حفظ الرابط المسموح به.")
        return ConversationHandler.END

    # التعامل مع العناصر المحظورة (يتطلب خطوة إضافية)
    if action in ["action_add_banned_word", "action_add_banned_link"]:
        context.user_data['value'] = user_input
        keyboard = [
            [InlineKeyboardButton("بدون تقييد", callback_data="mute_none")],
            [InlineKeyboardButton("يوم", callback_data="mute_day")],
            [InlineKeyboardButton("أسبوع", callback_data="mute_week")],
            [InlineKeyboardButton("شهر", callback_data="mute_month")],
            [InlineKeyboardButton("إلغاء", callback_data="cancel_conv")]
        ]
        await update.message.reply_text("اختر مدة التقييد:", reply_markup=InlineKeyboardMarkup(keyboard))
        return AWAITING_MUTE_DURATION

async def handle_mute_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يحفظ العنصر المحظور مع مدة التقييد المختارة."""
    query = update.callback_query
    await query.answer()
    
    action = context.user_data.get('action')
    value = context.user_data.get('value')
    duration = query.data.split('_')[1] if query.data != 'mute_none' else None

    model = BannedWord if action == "action_add_banned_word" else BannedLink
    column = "word" if action == "action_add_banned_word" else "link_pattern"

    if db_add_item({column: value, 'mute_duration': duration}, model, column):
        await reply_or_edit(update, f"✅ تم حفظ '{value}' بنجاح.")
    else:
        await reply_or_edit(update, f"⚠️ فشل حفظ '{value}'.")
        
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ينهي المحادثة ويعيد المستخدم للقائمة الرئيسية."""
    await reply_or_edit(update, "تم إلغاء العملية.")
    context.user_data.clear()
    await admin_panel(update, context) # العودة للوحة التحكم
    return ConversationHandler.END

# --- الدالة الرئيسية ---
def main():
    if not TELEGRAM_TOKEN or not ADMIN_ID:
        logger.critical("FATAL: TELEGRAM_TOKEN or ADMIN_ID are not set.")
        return

    init_db()
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # معالج المحادثات المنظم
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_conversation, pattern="^action_")
        ],
        states={
            AWAITING_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_input)],
            AWAITING_MUTE_DURATION: [CallbackQueryHandler(handle_mute_duration, pattern="^mute_")]
        },
        fallbacks=[
            CallbackQueryHandler(cancel_conversation, pattern="^cancel_conv$"),
            CommandHandler('cancel', cancel_conversation)
        ],
        per_message=False
    )

    # --- ترتيب أولوية المعالجات ---
    # 1. الأوامر المباشرة (مثل /start أو كلمة "يمان")
    application.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("أهلاً بك!")))
    application.add_handler(MessageHandler(filters.Regex(r'^يمان$') & filters.User(user_id=ADMIN_ID), admin_panel))

    # 2. معالج المحادثات (له أولوية على الأزرار العامة والرسائل)
    application.add_handler(conv_handler)

    # 3. معالج الأزرار العامة (للتنقل في القوائم والحذف)
    application.add_handler(CallbackQueryHandler(button_handler))

    # 4. معالج الرسائل العام (يأتي في النهاية ليلتقط أي رسالة لم تتم معالجتها)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, general_message_handler))

    logger.info("Bot is starting with improved handler priority...")
    application.run_polling()

if __name__ == "__main__":
    main()
