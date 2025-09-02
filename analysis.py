import logging
# --- !! الاستيراد الوحيد الذي نحتاجه !! ---
from farasa.sentiment import FarasaSentimentAnalyzer
# -----------------------------------------

logger = logging.getLogger(__name__)

# تهيئة المحلل الذي نحتاجه فقط
sentiment_analyzer = FarasaSentimentAnalyzer()

def analyze_sentiment_hf(text: str) -> str:
    """
    تحليل مشاعر النص باستخدام مكتبة Farasa التي تعمل محليًا.
    """
    if not text or not text.strip():
        return 'neutral'
    
    try:
        # استدعاء المحلل للحصول على النتيجة
        result = sentiment_analyzer.analyze(text)
        
        if result == 'POS':
            return 'positive'
        elif result == 'NEG':
            return 'negative'
        else:
            return 'neutral'

    except Exception as e:
        logger.error(f"Farasa analysis failed: {e}", exc_info=True)
        return 'neutral'
