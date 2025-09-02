import os
import requests
import logging

# إعداد المسجل
logger = logging.getLogger(__name__)

# قراءة التوكن من متغيرات البيئة
HUGGINGFACE_TOKEN = os.environ.get("HUGGINGFACE_TOKEN")

# --- التعديل هنا: استخدام نموذج مختلف وموثوق ---
# هذا النموذج متخصص في تحليل المشاعر للنصوص العربية
API_URL = "https://api-inference.huggingface.co/models/akhooli/xlm-r-large-arabic-sent"

def analyze_sentiment_hf(text: str) -> str:
    """
    يحلل مشاعر النص باستخدام واجهة برمجة تطبيقات Hugging Face.
    """
    if not text or not text.strip():
        return 'neutral'

    if not HUGGINGFACE_TOKEN:
        logger.warning("HUGGINGFACE_TOKEN is not set. Returning 'neutral'.")
        return 'neutral'

    headers = {"Authorization": f"Bearer {HUGGINGFACE_TOKEN}"}
    payload = {"inputs": text}

    try:
        response = requests.post(API_URL, headers=headers, json=payload, timeout=15)

        if response.status_code == 200:
            result = response.json()
            # هيكل الرد قد يختلف بين النماذج، لذا نجعله مرنًا
            if isinstance(result, list) and result and isinstance(result[0], list) and result[0]:
                # العثور على التصنيف الأعلى درجة
                top_label = max(result[0], key=lambda x: x['score'])
                label = top_label['label'].lower()

                # توحيد التصنيفات (قد تكون 'POSITIVE', 'NEGATIVE', 'NEUTRAL')
                if 'positive' in label:
                    return 'positive'
                elif 'negative' in label:
                    return 'negative'
                else:
                    return 'neutral'
            else:
                logger.warning(f"Unexpected API response format: {result}")
                return 'neutral'
        else:
            logger.error(f"Hugging Face API Error: Status Code {response.status_code} - Response: {response.text}")
            return 'neutral'

    except requests.RequestException as e:
        logger.error(f"Hugging Face request failed: {e}")
        return 'neutral'
    except Exception as e:
        logger.error(f"An unexpected error occurred in sentiment analysis: {e}")
        return 'neutral'
