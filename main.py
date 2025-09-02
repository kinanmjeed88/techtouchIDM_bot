import os
import logging
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Chat
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
    CallbackQueryHandler, ConversationHandler
)
import yt_dlp

# استيراد الوحدات المخصصة
from database import (
    save_message, SessionLocal, Message, add_or_update_reply,
    get_all_replies, delete_reply
)
from analysis import analyze_sentiment_hf

# ... (الكود من إعدادات logging إلى نهاية دالة handle_link يبقى كما هو) ...
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
ADMIN_ID = os.environ.get('ADMIN_ID')
KEYWORD, REPLY_TEXT = range(2)

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

# --- تعديل دالة معالجة رسائل المجموعة ---
async def process_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text or message.text.startswith('/'):
        return

    all_replies = get_all_replies()
    message_lower = message.text.lower()

    for reply in all_replies:
        if reply.keyword.lower() in message_lower:
            await message.reply_text(reply.reply_text)
            return

    user_id = message.from_user.id
    group_id = message.chat.id  # نحصل على معرف المجموعة من الرسالة
    
    sentiment = analyze_sentiment_hf(message.text)
    # نمرر معرف المجموعة عند حفظ الرسالة
    save_message(user_id, group_id, message.text, sentiment)
    logger.info(f"Saved message from user {user_id} in group {group_id} with sentiment: {sentiment}")

# --- تعديل لوحة التحكم ---
async def show_control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) != str(ADMIN_ID): return

    # التحقق إذا كانت الرسالة في مجموعة
    if update.message.chat.type in [Chat.GROUP, Chat.SUPERGROUP]:
        keyboard = [
            # نمرر معرف المجموعة في callback_data
            [InlineKeyboardButton("📊 طلب تقرير لهذه المجموعة", callback_data=f'get_analysis_report_{update.message.chat.id}')],
            [InlineKeyboardButton("💬 إدارة الردود التلقائية (عام)", callback_data='manage_replies')],
            [InlineKeyboardButton("❌ إغلاق", callback_data='close_panel')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f'لوحة تحكم المجموعة:\n`{update.message.chat.title}`', reply_markup=reply_markup, parse_mode='Markdown')
    else:
        # إذا كان في الخاص، لا يمكن طلب تقرير
        await update.message.reply_text('يمكن استخدام لوحة التحكم الكاملة داخل المجموعات فقط.')

# ... (الكود من get_keyword إلى view_delete_replies يبقى كما هو) ...
async def add_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("تمام. أرسل الآن **الكلمة المفتاحية** التي تريد أن يرد عليها البوت (كلمة واحدة أو جملة قصيرة).", parse_mode='Markdown')
    return KEYWORD

async def get_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['keyword'] = update.message.text.strip()
    await update.message.reply_text("ممتاز. الآن أرسل **نص الرد** الذي سيقوم البوت بإرساله.", parse_mode='Markdown')
    return REPLY_TEXT

async def get_reply_text_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyword = context.user_data['keyword']
    reply_text = update.message.text
    add_or_update_reply(keyword, reply_text)
    await update.message.reply_text(f"✅ تم الحفظ بنجاح!\nالكلمة: {keyword}\nالرد: {reply_text}")
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
        [InlineKeyboardButton("⬅️ عودة للقائمة الرئيسية", callback_data='main_menu')],
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
        await query.edit_message_text("لا توجد ردود تلقائية محفوظة حالياً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ عودة", callback_data='manage_replies')]]))
        return
    keyboard = []
    for reply in replies:
        keyboard.append([InlineKeyboardButton(f"🗑️ {reply.keyword}", callback_data=f"delete_{reply.keyword}")])
    keyboard.append([InlineKeyboardButton("⬅️ عودة", callback_data='manage_replies')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("اضغط على أي رد لحذفه:", reply_markup=reply_markup)

# --- تعديل معالج الأزرار ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # تعديل منطق طلب التقرير
    if data.startswith('get_analysis_report_'):
        group_id = data.split('_', 2)[2] # استخراج معرف المجموعة من callback_data
        await query.edit_message_text("جاري إعداد التقرير لهذه المجموعة...")
        db = SessionLocal()
        try:
            seven_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
            # فلترة الرسائل بناءً على معرف المجموعة
            messages = db.query(Message).filter(Message.group_id == group_id, Message.timestamp >= seven_days_ago).all()
            
            total_messages = len(messages)
            if total_messages == 0:
                await query.edit_message_text("لا توجد رسائل لتحليلها في هذه المجموعة خلال آخر 7 أيام.")
                return

            positive_count = sum(1 for m in messages if m.sentiment == 'positive')
            negative_count = sum(1 for m in messages if m.sentiment == 'negative')
            neutral_count = total_messages - positive_count - negative_count

            positive_percent = (positive_count / total_messages) * 100
            negative_percent = (negative_count / total_messages) * 100
            neutral_percent = (neutral_count / total_messages) * 100

            report = (f"📊 **تقرير تحليل المجموعة لآخر 7 أيام** 📊\n\n"
                      f"▪️ **إجمالي التعليقات:** {total_messages} تعليق\n\n"
                      f"**📉 تحليل المشاعر:**\n"
                      f"💚 **إيجابي:** {positive_percent:.1f}%\n"
                      f"💔 **سلبي:** {negative_percent:.1f}%\n"
                      f"😐 **محايد:** {neutral_percent:.1f}%\n")
            # لا نضيف زر عودة هنا لأن لوحة التحكم قد حُذفت أو تغيرت
            await query.edit_message_text(report, parse_mode='Markdown')
        finally:
            db.close()

    elif data == 'manage_replies':
        await show_replies_menu(update, context)
    # لا يوجد زر عودة للقائمة الرئيسية لأنها تعتمد على المجموعة
    # elif data == 'main_menu': ...
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

# --- إعداد وتشغيل البوت (يبقى كما هو) ---
def main() -> None:
    if not TELEGRAM_TOKEN or not ADMIN_ID:
        logger.error("خطأ: متغيرات البيئة TELEGRAM_TOKEN و ADMIN_ID مطلوبة.")
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
    application.add_handler(MessageHandler(filters.Regex(r'^يمان$'), show_control_panel))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler((filters.Entity("url") | filters.Entity("text_link")) & filters.ChatType.PRIVATE, handle_link))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, process_group_message))
    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
