# main.py
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
ADD_BANNED_WORD, ADD_BANNED_LINK, ADD_WHITELISTED_LINK, SET_AUTO_REPLY, BROADCAST_MESSAGE = range(5)

# --- معالجات الرسائل ---

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """المعالج الرئيسي للرسائل في المجموعات."""
    user = update.effective_user
    chat = update.effective_chat
    message = update.message

    if not user or not message or not message.text:
        return

    # تخزين معلومات المستخدم
    add_or_update_user(user.id, user.full_name, user.username)

    # التحقق من المشرفين والأدمن
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
        return # المشرفون معفيون من الفحص

    text = message.text.lower()

    # 1. فحص الروابط المحظورة
    banned_links = db_get_all_items(BannedLink)
    whitelisted_links = db_get_all_items(WhitelistedLink)
    
    urls = re.findall(r'(https?://\S+)', text)
    for url in urls:
        is_whitelisted = any(url.startswith(prefix) for prefix in whitelisted_links)
        if not is_whitelisted:
            is_banned = any(pattern in url for pattern in banned_links)
            if is_banned:
                await message.delete()
                await context.bot.send_message(chat.id, f"تم حذف رسالة من {user.mention_html()} لاحتوائها على رابط محظور.", parse_mode=ParseMode.HTML)
                return

    # 2. فحص الكلمات المحظورة
    banned_words = db_get_all_items(BannedWord)
    if any(word in text for word in banned_words):
        await message.delete()
        await context.bot.send_message(chat.id, f"تم حذف رسالة من {user.mention_html()} لاحتوائها على كلمة محظورة.", parse_mode=ParseMode.HTML)
        return

async def private_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الرسائل الخاصة من المستخدمين."""
    user = update.effective_user
    add_or_update_user(user.id, user.full_name, user.username)

    # إرسال الرد التلقائي (إذا كان موجودًا)
    auto_reply = get_setting('auto_reply')
    if auto_reply:
        await update.message.reply_text(auto_reply)

    # إرسال الرسالة كـ "تذكرة دعم" للأدمن
    user_info = f"{user.full_name} (@{user.username})" if user.username else user.full_name
    text_to_forward = (
        f"📩 **رسالة جديدة من:** {user_info}\n"
        f"**ID:** `{user.id}`\n\n"
        f"```{update.message.text}```"
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

    if data == "main_menu":
        keyboard = [
            [InlineKeyboardButton("🚫 إدارة الحظر", callback_data="manage_banning")],
            [InlineKeyboardButton("📢 إدارة البث", callback_data="manage_broadcast")],
            [InlineKeyboardButton("⚙️ إعدادات أخرى", callback_data="manage_settings")],
            [InlineKeyboardButton("❌ إغلاق", callback_data="close_panel")]
        ]
        await query.edit_message_text("لوحة تحكم الأدمن:", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "manage_banning":
        keyboard = [
            [InlineKeyboardButton("📝 إدارة الكلمات المحظورة", callback_data="banned_words")],
            [InlineKeyboardButton("🔗 إدارة الروابط المحظورة", callback_data="banned_links")],
            [InlineKeyboardButton("✅ إدارة الروابط المسموحة", callback_data="whitelisted_links")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ]
        await query.edit_message_text("إدارة الحظر:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "manage_broadcast":
        blocked_count = get_blocked_user_count()
        keyboard = [
            [InlineKeyboardButton("✍️ إرسال رسالة بث جديدة", callback_data="broadcast_start")],
            [InlineKeyboardButton(f"👥 فحص المحظورين ({blocked_count})", callback_data="check_blocked")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ]
        await query.edit_message_text("إدارة البث:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "manage_settings":
        keyboard = [
            [InlineKeyboardButton("🤖 تعديل الرد التلقائي", callback_data="set_auto_reply_start")],
            [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
        ]
        await query.edit_message_text("إعدادات أخرى:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "check_blocked":
        await query.edit_message_text("جاري فحص المستخدمين... قد يستغرق هذا بعض الوقت.")
        all_users = get_all_active_users()
        blocked_count = 0
        for user in all_users:
            try:
                await context.bot.send_chat_action(user.id, 'typing')
                await asyncio.sleep(0.2)
            except Forbidden:
                set_user_blocked(user.id)
                blocked_count += 1
            except BadRequest: # User not found, etc.
                set_user_blocked(user.id)
                blocked_count += 1
            except Exception:
                pass
        total_blocked = get_blocked_user_count()
        await query.edit_message_text(f"تم فحص المستخدمين.\n- مستخدمون جدد تم اكتشاف حظرهم: {blocked_count}\n- إجمالي المحظورين: {total_blocked}")

    elif data.startswith("reply_"):
        user_id = data.split("_")[1]
        context.user_data['reply_user_id'] = user_id
        await query.edit_message_text(f"أرسل الآن ردك للمستخدم صاحب الـ ID: {user_id}")
        return "admin_reply" # State for conversation

    elif data == "close_panel":
        await query.message.delete()

# --- دوال إدارة القوائم (مشتركة) ---

async def manage_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, model, state):
    query = update.callback_query
    items = db_get_all_items(model)
    text = f"قائمة {item_type}:\n" + ("\n".join(f"- `{item}`" for item in items) if items else "لا يوجد عناصر.")
    
    keyboard = [
        [InlineKeyboardButton("➕ إضافة", callback_data=f"add_{model.__tablename__}")],
        [InlineKeyboardButton("🗑️ حذف", callback_data=f"delete_{model.__tablename__}")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="manage_banning")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

async def add_item_start(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, state):
    query = update.callback_query
    await query.edit_message_text(f"أرسل {item_type} الذي تريد إضافته.")
    return state

async def delete_item_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, model):
    query = update.callback_query
    items = db_get_all_items(model)
    if not items:
        await query.answer("لا يوجد عناصر لحذفها!", show_alert=True)
        return
    
    keyboard = [[InlineKeyboardButton(f"🗑️ {item}", callback_data=f"confirm_delete_{model.__tablename__}_{item}")] for item in items]
    keyboard.append([InlineKeyboardButton("⬅️ عودة", callback_data=f"{model.__tablename__}")])
    await query.edit_message_text(f"اختر {item_type} الذي تريد حذفه:", reply_markup=InlineKeyboardMarkup(keyboard))

async def confirm_delete_item(update: Update, context: ContextTypes.DEFAULT_TYPE, model):
    query = update.callback_query
    parts = query.data.split("_", 3)
    item_to_delete = parts[3]
    
    if db_delete_item(item_to_delete, model):
        await query.answer("تم الحذف بنجاح!")
        await manage_list_menu(update, context, "العناصر", model, None) # Refresh menu
    else:
        await query.answer("فشل الحذف.", show_alert=True)

# --- معالجات المحادثات ---

async def save_item(update: Update, context: ContextTypes.DEFAULT_TYPE, model):
    item = update.message.text
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
    message = update.message
    users = get_all_active_users()
    sent_count = 0
    failed_count = 0
    await update.message.reply_text(f"بدء البث إلى {len(users)} مستخدم...")
    for user in users:
        try:
            await context.bot.copy_message(chat_id=user.id, from_chat_id=message.chat_id, message_id=message.message_id)
            sent_count += 1
            await asyncio.sleep(0.1)
        except Forbidden:
            set_user_blocked(user.id)
            failed_count += 1
        except Exception:
            failed_count += 1
    await update.message.reply_text(f"📢 انتهى البث.\n- ✅ تم الإرسال إلى: {sent_count}\n- ❌ فشل الإرسال أو محظور: {failed_count}")
    return ConversationHandler.END

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
        logger.error("Missing TELEGRAM_TOKEN or ADMIN_ID environment variables.")
        return

    init_db()
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # --- محادثات ---
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "كلمة محظورة", ADD_BANNED_WORD), pattern="^add_banned_words$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "رابط محظور", ADD_BANNED_LINK), pattern="^add_banned_links$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "رابط مسموح به", ADD_WHITELISTED_LINK), pattern="^add_whitelisted_links$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "الرد التلقائي الجديد", SET_AUTO_REPLY), pattern="^set_auto_reply_start$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "رسالة البث", BROADCAST_MESSAGE), pattern="^broadcast_start$"),
        ],
        states={
            ADD_BANNED_WORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: save_item(u, c, BannedWord))],
            ADD_BANNED_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: save_item(u, c, BannedLink))],
            ADD_WHITELISTED_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: save_item(u, c, WhitelistedLink))],
            SET_AUTO_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_auto_reply_message)],
            BROADCAST_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, send_broadcast)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        conversation_timeout=300
    )
    
    admin_reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(lambda u, c: button_handler(u, c), pattern="^reply_")],
        states={"admin_reply": [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply_message)]},
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    # --- إضافة المعالجات ---
    application.add_handler(CommandHandler("start", lambda u, c: add_or_update_user(u.effective_user.id, u.effective_user.full_name, u.effective_user.username)))
    application.add_handler(CommandHandler("yman", admin_panel, filters=filters.User(user_id=ADMIN_ID)))
    
    application.add_handler(conv_handler)
    application.add_handler(admin_reply_conv)

    application.add_handler(CallbackQueryHandler(lambda u, c: manage_list_menu(u, c, "الكلمات المحظورة", BannedWord, ADD_BANNED_WORD), pattern="^banned_words$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: manage_list_menu(u, c, "الروابط المحظورة", BannedLink, ADD_BANNED_LINK), pattern="^banned_links$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: manage_list_menu(u, c, "الروابط المسموحة", WhitelistedLink, ADD_WHITELISTED_LINK), pattern="^whitelisted_links$"))
    
    application.add_handler(CallbackQueryHandler(lambda u, c: delete_item_menu(u, c, "كلمة", BannedWord), pattern="^delete_banned_words$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: delete_item_menu(u, c, "رابط", BannedLink), pattern="^delete_banned_links$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: delete_item_menu(u, c, "رابط", WhitelistedLink), pattern="^delete_whitelisted_links$"))

    application.add_handler(CallbackQueryHandler(lambda u, c: confirm_delete_item(u, c, BannedWord), pattern="^confirm_delete_banned_words_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: confirm_delete_item(u, c, BannedLink), pattern="^confirm_delete_banned_links_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: confirm_delete_item(u, c, WhitelistedLink), pattern="^confirm_delete_whitelisted_links_"))

    application.add_handler(CallbackQueryHandler(button_handler))

    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, private_message_handler))
    application.add_handler(MessageHandler(filters.ChatType.GROUP | filters.ChatType.SUPERGROUP, message_handler))

    logger.info("Bot is starting with new features...")
    application.run_polling()

if __name__ == "__main__":
    main()
