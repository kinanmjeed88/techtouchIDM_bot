import os
import requests
import logging

# إعداد نظام التسجيل (Logging) لرؤية الأخطاء المحتملة
logger = logging.getLogger(__name__)

# اسم النموذج الذي نريد استخدامه من Hugging Face
MODEL_NAME = "CAMeL-Lab/bert-base-arabic-camelbert-da-sentiment"
API_URL = f"https://api-inference.huggingface.co/models/{MODEL_NAME}"

# قراءة التوكن من متغيرات البيئة في Railway
# تأكد من أنك أضفت متغيرًا باسم "HUGGINGFACE_TOKEN" في إعدادات Railway
HF_TOKEN = os.environ.get("HUGGINGFACE_TOKEN")

def analyze_sentiment_hf(text: str) -> str:
    """
    يحلل مشاعر النص العربي باستخدام Hugging Face Inference API.
    يرجع 'positive', 'negative', أو 'neutral'.
    """
    # التحقق من وجود النص والتوكن قبل إرسال الطلب
    if not text or not text.strip():
        logger.warning("analyze_sentiment_hf called with empty text.")
        return 'neutral'
    
    if not HF_TOKEN:
        logger.error("HUGGINGFACE_TOKEN is not set in environment variables.")
        return 'neutral'

    # إعداد رأس الطلب مع التوكن للمصادقة
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    
    try:
        # إرسال الطلب إلى الـ API
        response = requests.post(API_URL, headers=headers, json={"inputs": text, "options": {"wait_for_model": True}})
        
        # التحقق من نجاح الطلب
        if response.status_code != 200:
            logger.error(f"Hugging Face API Error: Status Code {response.status_code} - Response: {response.text}")
            return 'neutral'

        result = response.json()
        
        # التأكد من أن النتيجة بالتنسيق المتوقع
        if not isinstance(result, list) or not result or not isinstance(result[0], list):
            logger.error(f"Unexpected API response format: {result}")
            return 'neutral'

        # استخراج النتيجة ذات أعلى درجة ثقة
        highest_score_label = ""
        highest_score = 0.0
        for label_data in result[0]:
            if label_data.get('score', 0) > highest_score:
                highest_score = label_data['score']
                highest_score_label = label_data['label']
        
        # النموذج يرجع "LABEL_0", "LABEL_1", "LABEL_2"
        # نقوم بترجمتها إلى كلمات مفهومة
        if highest_score_label == "LABEL_2": # إيجابي
            return 'positive'
        elif highest_score_label == "LABEL_0": # سلبي
            return 'negative'
        else: # محايد (LABEL_1)
            return 'neutral'

    except requests.exceptions.RequestException as e:
        logger.error(f"A network error occurred during sentiment analysis: {e}")
        return 'neutral'
    except Exception as e:
        logger.error(f"An unexpected error occurred during sentiment analysis: {e}")
        return 'neutral'

