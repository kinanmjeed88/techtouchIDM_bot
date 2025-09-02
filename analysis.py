import logging
from simple_sentiment_arabic import sentiment

logger = logging.getLogger(__name__)

def analyze_sentiment_hf(text: str) -> str:
    """
    تحليل مشاعر النص باستخدام مكتبة simple-sentiment-arabic البسيطة.
    """
    if not text or not text.strip():
        return 'neutral'
    
    try:
        # استدعاء المحلل للحصول على النتيجة
        # سيعطينا 'positive', 'negative', أو 'neutral' مباشرة
        result = sentiment(text)
        return result

    except Exception as e:
        logger.error(f"Simple-Sentiment analysis failed: {e}", exc_info=True)
        return 'neutral'
