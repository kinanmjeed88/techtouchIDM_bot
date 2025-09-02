import logging
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

logger = logging.getLogger(__name__)

# تهيئة المحلل مرة واحدة عند بدء التشغيل
# هذا المحلل يعمل بشكل كامل دون الحاجة للإنترنت
analyzer = SentimentIntensityAnalyzer()

def analyze_sentiment_hf(text: str) -> str:
    """
    تحليل مشاعر النص باستخدام مكتبة VADER-Arabic التي تعمل محليًا.
    """
    if not text or not text.strip():
        return 'neutral'
    
    try:
        # استدعاء المحلل للحصول على درجات المشاعر
        # سيعطينا قاموسًا مثل: {'neg': 0.0, 'neu': 0.5, 'pos': 0.5, 'compound': 0.8}
        sentiment_scores = analyzer.polarity_scores(text)
        
        # استخدام درجة "compound" لتحديد الشعور العام
        # هذه الدرجة تتراوح بين -1 (سلبي جدًا) و +1 (إيجابي جدًا)
        compound_score = sentiment_scores['compound']
        
        if compound_score >= 0.05:
            return 'positive'
        elif compound_score <= -0.05:
            return 'negative'
        else:
            return 'neutral'

    except Exception as e:
        logger.error(f"VADER analysis failed: {e}", exc_info=True)
        return 'neutral'

