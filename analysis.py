import logging
from farasa.segmenter import FarasaSegmenter
from farasa.pos import FarasaPOSTagger
from farasa.ner import FarasaNER
from farasa.diacritizer import FarasaDiacritizer
from farasa.sentiment import FarasaSentimentAnalyzer

# ملاحظة: قد لا نحتاج لكل هذه، لكن تهيئتها لا يضر
# الأهم هو FarasaSentimentAnalyzer
sentiment_analyzer = FarasaSentimentAnalyzer()

logger = logging.getLogger(__name__)

def analyze_sentiment_hf(text: str) -> str:
    """
    تحليل مشاعر النص باستخدام مكتبة Farasa التي تعمل محليًا.
    """
    if not text or not text.strip():
        return 'neutral'
    
    try:
        # استدعاء المحلل للحصول على النتيجة
        # سيعطينا 'POS' أو 'NEG'
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
