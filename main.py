import os
import logging
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Chat
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
    CallbackQueryHandler, ConversationHandler, MessageReactionHandler, ChatMemberHandler
)
import yt_dlp

# استيراد الوحدات المخصصة
from database import (
    save_message, SessionLocal, Message, add_or_update_group, add_or_update_reply,
    get_all_replies, delete_reply, update_message_reactions, get_top_reacted_messages,
    remove_group, get_all_managed_groups
)
from analysis import analyze_sentiment_hf

# إعداد نظام التسجيل
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# قراءة المتغيرات الحساسة
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
ADMIN_ID = os.environ.get('ADMIN_ID')

# مراحل المحادثة
KEYWORD, REPLY_TEXT = range(2)

# --- الدوال تبقى كما هي ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('بوت عمل احصائية')

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    sent_message = await update.message.reply_text('جاري معالجة الرابط، يرجى الانتظار...')
    ydl_opts = {'format': 'best', 'outtmpl': '%(title)s.%(ext)s', 'noplaylist': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            await update.message.reply_document(document=open(filename, 'rb'), caption=info.get('title', ''))
            os.remove(filename)
            await sent_message.delete()
    except Exception as e:
        logger.error(f"Error downloading {url}: {e}")
        await sent_message.edit_text(f'حدث خطأ أثناء التحميل: {e}')

async def process_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.text or message.text.startswith('/'):
        return
    try:
        user_id = message.from_user.id
        group_id = message.chat.id
        message_id = message.message_id
        sentiment = analyze_sentiment_hf(message.text)
        save_message(str(message_id), str(user_id), str(group_id), message.text, sentiment)
        logger.info(f"SUCCESS: Saved message {message_id} from group {group_id}")
    except Exception as e:
        logger.error(f"ERROR: Failed to save message {message.message_id}. Reason: {e}", exc_info=True)
    try:
        all_replies = get_all_replies()
        message_lower = message.text.lower()
        for reply in all_replies:
            if reply.keyword.lower() in message_lower:
                await message.reply_text(reply.reply_text)
                break
    except Exception as e:
        logger.error(f"ERROR: Failed to process auto-reply. Reason: {e}")

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reaction = update.message_reaction
    message_id = reaction.message_id
    positive_emojis = ['👍', '❤️', '🔥', '🥰', '👏', '😁', '🎉', '💯']
    positive_count = 0
    if reaction.new_reaction:
        for r in reaction.new_reaction:
            if hasattr(r, 'emoji') and r.emoji in positive_emojis:
                positive_count += 1
    update_message_reactions(str(message_id), positive_count)
    logger.info(f"Updated reactions for message {message_id}. New positive count: {positive_count}")

async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = update.chat_member
    if result is None: return
    chat = result.chat
    new_member_status = result.new_chat_member.status
    if result.new_chat_member.user.id == context.bot.id:
        if new_member_status == "member":
            logger.info(f"Bot was added to group '{chat.title}' ({chat.id})")
            add_or_update_group(str(chat.id), chat.title)
        elif new_member_status in ["left", "kicked"]:
            logger.info(f"Bot was removed from group '{chat.title}' ({chat.id})")
            remove_group(str(chat.id))

async def register_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if str(user.id) != str(ADMIN_ID): return
    if chat.type not in [Chat.GROUP, Chat.SUPERGROUP]: return
    try:
        add_or_update_group(str(chat.id), chat.title)
        await update.message.reply_text(f"✅ تم تسجيل المجموعة '{chat.title}' بنجاح!")
        logger.info(f"Group '{chat.title}' ({chat.id}) was manually registered by admin.")
    except Exception as e:
        await update.message.reply_text(f"حدث خطأ أثناء التسجيل: {e}")
        logger.error(f"Failed to manually register group {chat.id}: {e}")

async def show_control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID): return
    keyboard = [
        [InlineKeyboardButton("📊 طلب تقرير التحليل", callback_data='select_group_for_report')],
        [InlineKeyboardButton("⭐ أكثر التعليقات إعجاباً", callback_data='select_group_for_top_comments')],
        [InlineKeyboardButton("💬 إدارة الردود التلقائية", callback_data='manage_replies')],
        [InlineKeyboardButton("❌ إغلاق", callback_data='close_panel')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message.chat.type != Chat.PRIVATE:
        await update.message.reply_text("تم إرسال لوحة التحكم إليك في الخاص.", reply_to_message_id=update.message.message_id)
    await context.bot.send_message(chat_id=user.id, text='لوحة التحكم الرئيسية:', reply_markup=reply_markup)

async def add_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("تمام. أرسل الآن **الكلمة المفتاحية**.", parse_mode='Markdown')
    return KEYWORD

async def get_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['keyword'] = update.message.text.strip()
    await update.message.reply_text("ممتاز. الآن أرسل **نص الرد**.", parse_mode='Markdown')
    return REPLY_TEXT

async def get_reply_text_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyword = context.user_data['keyword']
    reply_text = update.message.text
    add_or_update_reply(keyword, reply_text)
    await update.message.reply_text(f"✅ تم الحفظ!\nالكلمة: {keyword}\nالرد: {reply_text}")
    context.user_data.clear()
    await show_replies_menu(update, context, from_conversation=True)
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم إلغاء العملية.")
    context.user_data.clear()
    return ConversationHandler.END

async def show_replies_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, from_conversation=False):
    keyboard = [
        [InlineKeyboardButton("➕ إضافة رد جديد", callback_data='add_reply_start')],
        [InlineKeyboardButton("📋 عرض/حذف الردود", callback_data='view_delete_replies')],
        [InlineKeyboardButton("⬅️ عودة للقائمة الرئيسية", callback_data='main_menu_private')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = "إدارة الردود التلقائية:"
    if from_conversation:
        await update.message.reply_text(message_text, reply_markup=reply_markup)
    else:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(message_text, reply_markup=reply_markup)

async def view_delete_replies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    replies = get_all_replies()
    if not replies:
        await query.edit_message_text("لا توجد ردود محفوظة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ عودة", callback_data='manage_replies')]]))
        return
    keyboard = []
    for reply in replies:
        keyboard.append([InlineKeyboardButton(f"🗑️ {reply.keyword}", callback_data=f"delete_{reply.keyword}")])
    keyboard.append([InlineKeyboardButton("⬅️ عودة", callback_data='manage_replies')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("اضغط على أي رد لحذفه:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == 'select_group_for_report' or data == 'select_group_for_top_comments':
        groups = get_all_managed_groups()
        if not groups:
            await query.edit_message_text("البوت لا يتواجد في أي مجموعات.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ عودة", callback_data='main_menu_private')]]))
            return
        keyboard = []
        request_type = 'get_analysis_report_' if data == 'select_group_for_report' else 'get_top_comments_'
        for group in groups:
            keyboard.append([InlineKeyboardButton(group.group_title, callback_data=f'{request_type}{group.group_id}')])
        keyboard.append([InlineKeyboardButton("⬅️ عودة", callback_data='main_menu_private')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("اختر مجموعة:", reply_markup=reply_markup)

    elif data.startswith('get_analysis_report_'):
        group_id = data.replace('get_analysis_report_', '')
        await query.edit_message_text("جاري إعداد تقرير التحليل...")
        db = SessionLocal()
        try:
            seven_days_ago = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)
            messages = db.query(Message).filter(Message.group_id == group_id, Message.timestamp >= seven_days_ago).all()
            total_messages = len(messages)
            if total_messages == 0:
                await query.edit_message_text("لا توجد رسائل لتحليلها في هذه المجموعة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ عودة", callback_data='select_group_for_report')]]))
                return
            positive_count = sum(1 for m in messages if m.sentiment == 'positive')
            negative_count = sum(1 for m in messages if m.sentiment == 'negative')
            neutral_count = total_messages - positive_count - negative_count
            positive_percent = (positive_count / total_messages) * 100 if total_messages > 0 else 0
            negative_percent = (negative_count / total_messages) * 100 if total_messages > 0 else 0
            neutral_percent = (neutral_count / total_messages) * 100 if total_messages > 0 else 0
            report = (f"📊 **تقرير تحليل المشاعر**\n\n"
                      f"▪️ **إجمالي التعليقات:** {total_messages}\n"
                      f"💚 **إيجابي:** {positive_percent:.1f}%\n"
                      f"💔 **سلبي:** {negative_percent:.1f}%\n"
                      f"😐 **محايد:** {neutral_percent:.1f}%")
            await query.edit_message_text(report, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ عودة", callback_data='select_group_for_report')]]))
        finally:
            db.close()

    elif data.startswith('get_top_comments_'):
        group_id = data.replace('get_top_comments_', '')
        await query.edit_message_text("جاري جلب أكثر التعليقات تفاعلاً...")
        top_messages = get_top_reacted_messages(group_id, limit=5)
        report = "⭐ **أكثر التعليقات تفاعلاً**\n\n"
        if not top_messages:
            report += "_لا توجد تعليقات حظيت بتفاعلات._"
        else:
            for i, msg in enumerate(top_messages):
                short_text = (msg.text[:70] + '...') if len(msg.text) > 70 else msg.text
                report += f"{i+1}. \"{short_text}\"\n(👍 {msg.positive_reactions} | {msg.sentiment})\n\n"
        await query.edit_message_text(report, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ عودة", callback_data='select_group_for_top_comments')]]))
    
    elif data == 'manage_replies':
        await show_replies_menu(update, context)
    
    elif data == 'main_menu_private':
        keyboard = [
            [InlineKeyboardButton("📊 طلب تقرير التحليل", callback_data='select_group_for_report')],
            [InlineKeyboardButton("⭐ أكثر التعليقات إعجاباً", callback_data='select_group_for_top_comments')],
            [InlineKeyboardButton("💬 إدارة الردود التلقائية", callback_data='manage_replies')],
            [InlineKeyboardButton("❌ إغلاق", callback_data='close_panel')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text('لوحة التحكم الرئيسية:', reply_markup=reply_markup)
    
    elif data == 'view_delete_replies':
        await view_delete_replies(update, context)
    
    elif data.startswith('delete_'):
        keyword_to_delete = data.split('_', 1)[1]
        if delete_reply(keyword_to_delete):
            await query.answer(f"تم حذف الرد الخاص بـ '{keyword_to_delete}'")
            await view_delete_replies(update, context)
        else:
            await query.answer("خطأ: لم يتم العثور على الرد.")
    
    elif data == 'close_panel':
        await query.message.delete()

def main() -> None:
    if not TELEGRAM_TOKEN or not ADMIN_ID:
        logger.error("خطأ: متغيرات البيئة مطلوبة.")
        return
        
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_reply_start, pattern='^add_reply_start$')],
        states={
            KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_keyword)],
            REPLY_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_reply_text_and_save)],
        },
        fallbacks=[CommandHandler('cancel', cancel_conversation)],
        per_message=False 
    )
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Regex(r'^تسجيل$'), register_group))
    application.add_handler(MessageHandler(filters.Regex(r'^يمان$'), show_control_panel))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageReactionHandler(handle_reaction))
    application.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(MessageHandler((filters.Entity("url") | filters.Entity("text_link")) & filters.ChatType.PRIVATE, handle_link))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUPS | filters.REPLY), process_group_message))

    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
