# main.py (النسخة النهائية والمحسّنة)
import os
import logging
import re
import asyncio
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
    CallbackQueryHandler, ConversationHandler
)
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest

# استيراد الوحدات المخصصة
from database import (
    init_db, add_or_update_user, get_all_active_users, set_user_blocked, get_blocked_users,
    db_add_item, db_get_all_items, db_delete_item, BannedWord, BannedLink, WhitelistedLink,
    get_setting, set_setting, AutoReply, get_all_auto_replies, get_blocked_user_count,
    add_user_restriction, get_user_restriction, is_user_restricted
)

# --- الإعدادات الأولية ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# تأكد من وجود المتغيرات البيئية
try:
    TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
    ADMIN_ID = int(os.environ['ADMIN_ID'])
except (KeyError, ValueError) as e:
    logger.error(f"خطأ حرج: متغيرات البيئة غير معرفة بشكل صحيح: {e}")
    exit()


# --- مراحل المحادثات ---
(
    ADD_BANNED_WORD, ADD_BANNED_LINK, ADD_WHITELISTED_LINK,
    SET_AUTO_REPLY, BROADCAST_MESSAGE, ADMIN_REPLY,
    ADD_AUTO_REPLY_KEYWORD, ADD_AUTO_REPLY_TEXT, SET_WELCOME_MESSAGE,
    CHOOSE_RESTRICTION, CONFIRM_BROADCAST
) = range(11)


# --- دوال مساعدة ---
def escape_markdown(text: str) -> str:
    """تهريب الأحرف الخاصة في MarkdownV2."""
    if not isinstance(text, str):
        return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

async def is_admin_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """التحقق مما إذا كان المستخدم هو الأدمن."""
    return update.effective_user.id == ADMIN_ID

# --- معالجات الأوامر الأساسية ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر /start."""
    user = update.effective_user
    add_or_update_user(user.id, user.full_name, user.username)
    welcome_message = get_setting('welcome_message') or "أهلاً بك في البوت!"
    await update.message.reply_text(welcome_message)

async def admin_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض لوحة تحكم الأدمن عند إرسال كلمة 'يمان'."""
    await admin_panel(update, context)

# --- معالجات الرسائل ---

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """المعالج الرئيسي للرسائل في المجموعات."""
    user = update.effective_user
    chat = update.effective_chat
    message = update.message

    if not user or not message:
        return

    add_or_update_user(user.id, user.full_name, user.username)
    text = message.text or message.caption or ""
    text_lower = text.lower()

    # التحقق مما إذا كان المستخدم مقيداً
    if is_user_restricted(user.id):
        try:
            await message.delete()
            # يمكن إرسال رسالة تحذيرية للمستخدم إذا أردت
        except Exception as e:
            logger.warning(f"Failed to delete restricted message from {user.id}: {e}")
        return

    # 1. فحص الردود التلقائية أولاً
    all_replies = get_all_auto_replies()
    for reply in all_replies:
        if reply.keyword.lower() in text_lower:
            await message.reply_text(reply.reply_text)
            return

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

    # دالة مساعدة لحذف الرسالة وتطبيق التقييد
    async def apply_restriction_and_delete(restriction_type: str, duration_days: int):
        await message.delete()
        user_mention = user.mention_html()
        await context.bot.send_message(
            chat.id,
            f"تم حذف رسالة من {user_mention} لاحتوائها على {restriction_type} محظور.",
            parse_mode=ParseMode.HTML
        )
        if duration_days > 0:
            add_user_restriction(user.id, timedelta(days=duration_days))
            await context.bot.send_message(
                chat.id,
                f"تم تقييد المستخدم {user_mention} لمدة {duration_days} يوم.",
                parse_mode=ParseMode.HTML
            )

    # فحص الروابط
    banned_links = db_get_all_items(BannedLink)
    whitelisted_links = [item.link_prefix for item in db_get_all_items(WhitelistedLink)]
    urls = re.findall(r'(https?://\S+)', text)
    for url in urls:
        is_whitelisted = any(url.startswith(prefix) for prefix in whitelisted_links)
        if not is_whitelisted:
            for link_item in banned_links:
                if link_item.link_pattern in url:
                    await apply_restriction_and_delete("رابط", link_item.restriction_days)
                    return

    # فحص الكلمات
    banned_words = db_get_all_items(BannedWord)
    for word_item in banned_words:
        if word_item.word in text_lower:
            await apply_restriction_and_delete("كلمة", word_item.restriction_days)
            return


async def private_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الرسائل الخاصة من المستخدمين (يدعم الصور والنصوص)."""
    user = update.effective_user
    message = update.message
    add_or_update_user(user.id, user.full_name, user.username)

    # إرسال الرد التلقائي إذا كان مفعلاً
    auto_reply = get_setting('auto_reply')
    if auto_reply:
        await message.reply_text(auto_reply)

    user_info = escape_markdown(f"{user.full_name} (@{user.username})" if user.username else user.full_name)
    header = (
        f"📩 *رسالة جديدة من:* {user_info}\n"
        f"*ID:* `{user.id}`\n\n"
    )
    keyboard = [[InlineKeyboardButton("✍️ رد على المستخدم", callback_data=f"reply_{user.id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # إذا كانت الرسالة تحتوي على صورة
    if message.photo:
        photo_file_id = message.photo[-1].file_id
        caption = message.caption or ""
        escaped_caption = escape_markdown(caption)
        
        # تخزين بيانات الرسالة للرد لاحقاً
        context.bot_data[f"msg_{message.message_id}"] = {'photo': photo_file_id, 'caption': caption}

        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_file_id,
            caption=header + f"📝 *النص المرافق:*\n```{escaped_caption}```",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=reply_markup
        )
    # إذا كانت الرسالة نصية فقط
    elif message.text:
        message_text = escape_markdown(message.text)
        
        # تخزين بيانات الرسالة للرد لاحقاً
        context.bot_data[f"msg_{message.message_id}"] = {'text': message.text}

        text_to_forward = header + f"```{message_text}```"
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=text_to_forward,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN_V2
        )


# --- لوحة تحكم الأدمن ---

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض لوحة تحكم الأدمن."""
    keyboard = [
        [InlineKeyboardButton("🚫 إدارة الحظر والتقييد", callback_data="manage_banning")],
        [InlineKeyboardButton("💬 إدارة الردود التلقائية", callback_data="manage_auto_replies")],
        [InlineKeyboardButton("📢 إدارة البث", callback_data="manage_broadcast")],
        [InlineKeyboardButton("⚙️ إعدادات أخرى", callback_data="manage_settings")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="close_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # إذا كان التحديث من رسالة، نرسل رد. إذا كان من زر، نعدل الرسالة.
    if update.callback_query:
        await update.callback_query.edit_message_text("لوحة تحكم الأدمن:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("لوحة تحكم الأدمن:", reply_markup=reply_markup)


# --- معالج الأزرار الرئيسي (CallbackQueryHandler) ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """المعالج الرئيسي لجميع الأزرار في لوحة التحكم."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # خريطة القوائم لتسهيل التنقل
    menu_map = {
        "main_menu": admin_panel,
        "manage_banning": manage_banning_menu,
        "manage_auto_replies": manage_auto_replies_menu,
        "manage_broadcast": manage_broadcast_menu,
        "manage_settings": manage_settings_menu,
        "banned_words": lambda u, c: manage_list_menu(u, c, "الكلمات المحظورة", BannedWord, "add_banned_word_start", "delete_banned_word_menu", "manage_banning"),
        "banned_links": lambda u, c: manage_list_menu(u, c, "الروابط المحظورة", BannedLink, "add_banned_link_start", "delete_banned_link_menu", "manage_banning"),
        "whitelisted_links": lambda u, c: manage_list_menu(u, c, "الروابط المسموحة", WhitelistedLink, "add_whitelisted_link_start", "delete_whitelisted_link_menu", "manage_banning"),
        "delete_banned_word_menu": lambda u, c: delete_item_menu(u, c, "كلمة", BannedWord, "word", "banned_words"),
        "delete_banned_link_menu": lambda u, c: delete_item_menu(u, c, "رابط", BannedLink, "link_pattern", "banned_links"),
        "delete_whitelisted_link_menu": lambda u, c: delete_item_menu(u, c, "رابط", WhitelistedLink, "link_prefix", "whitelisted_links"),
        "delete_auto_reply_menu": lambda u, c: delete_item_menu(u, c, "رد تلقائي", AutoReply, "keyword", "manage_auto_replies"),
    }

    if data in menu_map:
        await menu_map[data](update, context)
    
    # --- معالجة الحالات الخاصة ---
    elif data == "check_blocked":
        await check_blocked_users(update, context)

    elif data.startswith("reply_"):
        user_id = data.split("_")[1]
        context.user_data['reply_user_id'] = user_id
        await query.message.reply_text(f"أرسل الآن ردك (نص أو صورة مع نص) للمستخدم صاحب الـ ID: {user_id}")
        # لا نرجع حالة محادثة هنا، بل نعتمد على MessageHandler العام
        return ADMIN_REPLY

    elif data.startswith("confirm_delete_"):
        await confirm_delete_item(update, context)

    elif data == "close_panel":
        await query.message.delete()


# --- دوال بناء قوائم لوحة التحكم ---

async def manage_banning_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📝 الكلمات المحظورة", callback_data="banned_words")],
        [InlineKeyboardButton("🔗 الروابط المحظورة", callback_data="banned_links")],
        [InlineKeyboardButton("✅ الروابط المسموحة", callback_data="whitelisted_links")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
    ]
    await update.callback_query.edit_message_text("إدارة الحظر والتقييد:", reply_markup=InlineKeyboardMarkup(keyboard))

async def manage_auto_replies_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ إضافة رد تلقائي", callback_data="add_auto_reply_start")],
        [InlineKeyboardButton("🗑️ حذف رد تلقائي", callback_data="delete_auto_reply_menu")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
    ]
    await update.callback_query.edit_message_text("إدارة الردود التلقائية:", reply_markup=InlineKeyboardMarkup(keyboard))

async def manage_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    blocked_count = get_blocked_user_count()
    keyboard = [
        [InlineKeyboardButton("✍️ إرسال بث جديد", callback_data="broadcast_start")],
        [InlineKeyboardButton(f"👥 فحص المحظورين ({blocked_count})", callback_data="check_blocked")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
    ]
    await update.callback_query.edit_message_text("إدارة البث:", reply_markup=InlineKeyboardMarkup(keyboard))

async def manage_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🤖 تعديل الرد التلقائي للخاص", callback_data="set_auto_reply_start")],
        [InlineKeyboardButton("👋 تعديل رسالة الترحيب", callback_data="set_welcome_start")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="main_menu")]
    ]
    await update.callback_query.edit_message_text("إعدادات أخرى:", reply_markup=InlineKeyboardMarkup(keyboard))


# --- دوال إدارة القوائم (إضافة، حذف، عرض) ---

async def manage_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, model, add_cb: str, del_cb: str, back_cb: str):
    query = update.callback_query
    items = db_get_all_items(model)
    text = f"قائمة {item_type}:\n"
    if items:
        # عرض تفاصيل التقييد إن وجدت
        if hasattr(model, 'restriction_days'):
            item_lines = [f"- `{escape_markdown(item.word if hasattr(item, 'word') else item.link_pattern)}` (التقييد: {item.restriction_days} يوم)" for item in items]
        else:
            item_lines = [f"- `{escape_markdown(item.link_prefix if hasattr(item, 'link_prefix') else item.keyword)}`" for item in items]
        text += "\n".join(item_lines)
    else:
        text += "لا يوجد عناصر."

    keyboard = [
        [InlineKeyboardButton("➕ إضافة", callback_data=add_cb)],
        [InlineKeyboardButton("🗑️ حذف", callback_data=del_cb)],
        [InlineKeyboardButton("⬅️ عودة", callback_data=back_cb)]
    ]
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    except BadRequest:
        # في حال فشل Markdown، نعرض النص العادي
        text_plain = text.replace('`', '').replace('\\', '')
        await query.edit_message_text(text_plain, reply_markup=InlineKeyboardMarkup(keyboard))


async def add_item_start(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, state, model):
    """يبدأ محادثة إضافة عنصر جديد."""
    query = update.callback_query
    context.user_data['model_to_add'] = model
    
    # إذا كان النموذج يدعم التقييد، ننتقل إلى خطوة إدخال الكلمة/الرابط
    if hasattr(model, 'restriction_days'):
        await query.edit_message_text(f"أرسل {item_type} الذي تريد إضافته.")
        return state
    # إذا لم يكن يدعم التقييد (مثل الروابط المسموحة)، نطلب الإدخال مباشرة
    else:
        await query.edit_message_text(f"أرسل {item_type} الذي تريد إضافته.")
        return state # سيتم الحفظ مباشرة في الخطوة التالية


async def choose_restriction_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض أزرار اختيار مدة التقييد."""
    item_text = update.message.text.lower().strip()
    context.user_data['item_text'] = item_text

    keyboard = [
        [
            InlineKeyboardButton("يوم واحد", callback_data="restrict_1"),
            InlineKeyboardButton("أسبوع", callback_data="restrict_7")
        ],
        [
            InlineKeyboardButton("شهر", callback_data="restrict_30"),
            InlineKeyboardButton("دائم (حذف فقط)", callback_data="restrict_0")
        ],
        [InlineKeyboardButton("إلغاء", callback_data="cancel_restriction")]
    ]
    await update.message.reply_text(
        f"اختر مدة التقييد للمستخدمين الذين يرسلون '{item_text}':",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSE_RESTRICTION


async def save_item_with_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يحفظ العنصر مع مدة التقييد المختارة."""
    query = update.callback_query
    await query.answer()

    duration_map = {"restrict_1": 1, "restrict_7": 7, "restrict_30": 30, "restrict_0": 0}
    duration = duration_map.get(query.data)

    item_text = context.user_data.get('item_text')
    model = context.user_data.get('model_to_add')
    
    if item_text is None or model is None:
        await query.edit_message_text("حدث خطأ، يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END

    column_name = 'word' if model == BannedWord else 'link_pattern'
    
    if db_add_item({column_name: item_text, 'restriction_days': duration}, model):
        await query.edit_message_text(f"✅ تم حفظ العنصر بنجاح مع تقييد لمدة {duration} يوم.")
    else:
        await query.edit_message_text("⚠️ هذا العنصر موجود بالفعل.")

    context.user_data.clear()
    return ConversationHandler.END


async def save_item_without_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يحفظ العناصر التي لا تتطلب تقييد (مثل الروابط المسموحة)."""
    item_text = update.message.text.strip()
    model = context.user_data.get('model_to_add')
    
    if model is None:
        await update.message.reply_text("حدث خطأ، يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END

    column_name = 'link_prefix' # خاص بالروابط المسموحة
    
    if db_add_item({column_name: item_text}, model):
        await update.message.reply_text("✅ تم الحفظ بنجاح.")
    else:
        await update.message.reply_text("⚠️ هذا العنصر موجود بالفعل.")
        
    context.user_data.clear()
    return ConversationHandler.END


async def delete_item_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, item_type: str, model, column_name: str, back_callback: str):
    """يعرض قائمة بالعناصر لحذفها."""
    query = update.callback_query
    items = db_get_all_items(model)
    if not items:
        await query.answer("لا يوجد عناصر لحذفها!", show_alert=True)
        return

    keyboard = []
    for item in items:
        # استخراج النص من الكائن
        item_text = getattr(item, column_name)
        callback_data = f"confirm_delete_{model.__tablename__}_{item_text}"
        keyboard.append([InlineKeyboardButton(f"🗑️ {item_text[:30]}", callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("⬅️ عودة", callback_data=back_callback)])
    await query.edit_message_text(f"اختر {item_type} الذي تريد حذفه:", reply_markup=InlineKeyboardMarkup(keyboard))


async def confirm_delete_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يؤكد ويقوم بحذف العنصر."""
    query = update.callback_query
    parts = query.data.split("_", 3)
    model_name, item_to_delete = parts[2], parts[3]

    model_map = {
        "banned_words": (BannedWord, "word", "banned_words"),
        "banned_links": (BannedLink, "link_pattern", "banned_links"),
        "whitelisted_links": (WhitelistedLink, "link_prefix", "whitelisted_links"),
        "auto_replies": (AutoReply, "keyword", "manage_auto_replies"),
    }
    
    if model_name not in model_map:
        await query.answer("خطأ: نوع العنصر غير معروف.", show_alert=True)
        return

    model, column_name, back_cb = model_map[model_name]
    
    if db_delete_item(item_to_delete, model, column_name):
        await query.answer("تم الحذف بنجاح!")
        # إعادة تحميل القائمة بعد الحذف
        await button_handler(update, context) # استدعاء المعالج الرئيسي لتحديث القائمة
    else:
        await query.answer("فشل الحذف، العنصر غير موجود.", show_alert=True)


# --- دوال إدارة البث ---

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يبدأ عملية إرسال رسالة بث."""
    await update.callback_query.edit_message_text("أرسل الآن رسالة البث (نص، صورة، أو صورة مع نص).")
    return BROADCAST_MESSAGE

async def broadcast_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض معاينة لرسالة البث ويطلب التأكيد."""
    message = update.message
    context.user_data['broadcast_message'] = message

    total_users = len(get_all_active_users())
    
    await message.reply_text(
        f"تم استلام رسالة البث. هل أنت متأكد من إرسالها إلى {total_users} مستخدم؟",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ نعم، إرسال الآن", callback_data="confirm_broadcast_send")],
            [InlineKeyboardButton("❌ لا، إلغاء", callback_data="cancel_broadcast")]
        ])
    )
    return CONFIRM_BROADCAST

async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يرسل رسالة البث إلى جميع المستخدمين."""
    query = update.callback_query
    message_to_broadcast = context.user_data.get('broadcast_message')

    if not message_to_broadcast:
        await query.edit_message_text("خطأ: لم يتم العثور على رسالة البث. يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END

    await query.edit_message_text("بدأ إرسال البث... سيتم إعلامك عند الانتهاء.")
    
    users = get_all_active_users()
    sent_count = 0
    blocked_count = 0

    for user in users:
        try:
            await context.bot.copy_message(
                chat_id=user.id,
                from_chat_id=message_to_broadcast.chat_id,
                message_id=message_to_broadcast.message_id
            )
            sent_count += 1
        except Forbidden:
            set_user_blocked(user.id)
            blocked_count += 1
        except Exception as e:
            logger.error(f"Failed to send broadcast to {user.id}: {e}")
        await asyncio.sleep(0.1) # لتجنب الوصول إلى حدود الإرسال

    await query.message.reply_text(
        f"✅ انتهى البث!\n\n"
        f"📬 تم الإرسال بنجاح إلى: {sent_count} مستخدم.\n"
        f"🚫 مستخدمون قاموا بحظر البوت: {blocked_count}."
    )
    context.user_data.clear()
    return ConversationHandler.END


async def check_blocked_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض قائمة بالمستخدمين الذين حظروا البوت."""
    query = update.callback_query
    blocked_users = get_blocked_users()
    if not blocked_users:
        await query.answer("لا يوجد مستخدمون محظورون حالياً.", show_alert=True)
        return

    text = "قائمة المستخدمين الذين حظروا البوت:\n\n"
    for user in blocked_users:
        user_info = f"- {user.full_name}"
        if user.username:
            user_info += f" (@{user.username})"
        user_info += f" (ID: `{user.id}`)\n"
        text += user_info
    
    # إرسال كرسالة جديدة لأن القائمة قد تكون طويلة
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


# --- معالجات المحادثات الأخرى ---

async def set_setting_start(update: Update, context: ContextTypes.DEFAULT_TYPE, setting_key: str, prompt: str, state):
    """يبدأ محادثة تعديل إعداد."""
    query = update.callback_query
    context.user_data['setting_key'] = setting_key
    await query.edit_message_text(prompt)
    return state

async def save_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يحفظ الإعداد ويعرض الرسالة الجديدة للتأكيد."""
    key = context.user_data.pop('setting_key', None)
    value = update.message.text
    if not key: return ConversationHandler.END

    set_setting(key, value)
    
    # عرض الرسالة الجديدة للتأكيد
    confirmation_text = f"✅ تم حفظ الإعداد بنجاح.\n\n*المعاينة:*\n---\n{value}"
    await update.message.reply_text(confirmation_text, parse_mode=ParseMode.MARKDOWN_V2)
    
    return ConversationHandler.END


async def add_auto_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يبدأ محادثة إضافة رد تلقائي."""
    await update.callback_query.edit_message_text("أرسل الكلمة المفتاحية للرد التلقائي.")
    return ADD_AUTO_REPLY_KEYWORD

async def save_auto_reply_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يحفظ الكلمة المفتاحية ويطلب نص الرد."""
    context.user_data['keyword'] = update.message.text.strip().lower()
    await update.message.reply_text("الآن أرسل نص الرد.")
    return ADD_AUTO_REPLY_TEXT

async def save_auto_reply_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يحفظ الرد التلقائي الكامل."""
    keyword = context.user_data.pop('keyword')
    reply_text = update.message.text
    
    if db_add_item({'keyword': keyword, 'reply_text': reply_text}, AutoReply):
        await update.message.reply_text("✅ تم حفظ الرد التلقائي.")
    else:
        await update.message.reply_text("⚠️ هذه الكلمة المفتاحية موجودة بالفعل.")
        
    return ConversationHandler.END


async def admin_reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يرسل رداً من الأدمن إلى المستخدم (يدعم الصور والنصوص)."""
    user_id = context.user_data.pop('reply_user_id', None)
    if not user_id: return ConversationHandler.END
    
    message = update.message
    try:
        # إذا كان الرد يحتوي على صورة
        if message.photo:
            await context.bot.send_photo(
                chat_id=user_id,
                photo=message.photo[-1].file_id,
                caption=message.caption or ""
            )
        # إذا كان الرد نصياً فقط
        elif message.text:
            await context.bot.send_message(chat_id=user_id, text=message.text)
        
        await message.reply_text("✅ تم إرسال ردك.")
    except Exception as e:
        await message.reply_text(f"❌ فشل إرسال الرد: {e}")
        
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يلغي العملية الحالية."""
    # تحديد ما إذا كان الإلغاء من رسالة أو زر
    if update.message:
        await update.message.reply_text("تم إلغاء العملية.")
    elif update.callback_query:
        await update.callback_query.edit_message_text("تم إلغاء العملية.")
    
    context.user_data.clear()
    return ConversationHandler.END


# --- الدالة الرئيسية ---

def main():
    """الدالة الرئيسية لتشغيل البوت."""
    init_db()
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # --- محادثات (Conversations) ---
    
    # محادثة إضافة العناصر (كلمات، روابط)
    add_item_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "كلمة محظورة", ADD_BANNED_WORD, BannedWord), pattern="^add_banned_word_start$"),
            CallbackQueryHandler(lambda u, c: add_item_start(u, c, "نمط رابط محظور", ADD
