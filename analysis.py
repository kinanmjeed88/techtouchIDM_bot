import logging
from pyarabic_sentiment.sentiment_analyzer import SentimentAnalyzer

logger = logging.getLogger(__name__)

# تهيئة المحلل مرة واحدة عند بدء التشغيل
# هذه المكتبة حديثة ومتوافقة وتعمل محليًا
sa = SentimentAnalyzer()

def analyze_sentiment_hf(text: str) -> str:
    """
    تحليل مشاعر النص باستخدام مكتبة pyarabic-sentiment التي تعمل محليًا.
    """
    if not text or not text.strip():
        return 'neutral'
    
    try:
        # استدعاء المحلل للحصول على النتيجة
        # سيعطينا قاموسًا مثل: {'negative': 0.8, 'neutral': 0.1, 'positive': 0.1}
        result = sa.predict(text)
        
        # العثور على الشعور الذي يمتلك أعلى درجة
        # sentiment = max(result, key=result.get)
        
        # استخدام منطق أكثر دقة لتجنب التحيز نحو المحايد
        pos_score = result.get('positive', 0)
        neg_score = result.get('negative', 0)

        if pos_score > neg_score and pos_score > 0.4:
            return 'positive'
        elif neg_score > pos_score and neg_score > 0.4:
            return 'negative'
        else:
            return 'neutral'

    except Exception as e:
        logger.error(f"PyArabic-Sentiment analysis failed: {e}", exc_info=True)
        return 'neutral'
