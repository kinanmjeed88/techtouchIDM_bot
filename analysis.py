import os
import requests
import logging
import time

logger = logging.getLogger(__name__)

# العودة إلى الـ API العام والمباشر للنموذج
API_URL = "https://api-inference.huggingface.co/models/CAMeL-Lab/bert-base-arabic-camelbert-da-sentiment"
HUGGINGFACE_TOKEN = os.environ.get('HUGGINGFACE_TOKEN')

headers = {"Authorization": f"Bearer {HUGGINGFACE_TOKEN}"}

def analyze_sentiment_hf(text: str) -> str:
    """
    تحليل مشاعر النص باستخدام الـ API العام مع آلية إعادة المحاولة.
    """
    if not text or not text.strip():
        return 'neutral'

    # --- !! آلية إعادة المحاولة الجديدة !! ---
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(API_URL, headers=headers, json={"inputs": text}, timeout=15)

            if response.status_code == 200:
                result = response.json()
                if result and isinstance(result, list) and 'label' in result[0]:
                    # استخراج التصنيف وتحويله ليتوافق مع قيمنا
                    label = result[0]['label'].lower()
                    if 'positive' in label: return 'positive'
                    if 'negative' in label: return 'negative'
                    return 'neutral'
                
            # إذا كانت الخدمة مشغولة، انتظر وحاول مرة أخرى
            elif response.status_code == 503:
                logger.warning(f"Attempt {attempt + 1}: Service unavailable (503), retrying in 2 seconds...")
                time.sleep(2) # انتظر ثانيتين
                continue # انتقل إلى المحاولة التالية
                
            # لأي خطأ آخر، لا تقم بإعادة المحاولة
            else:
                logger.error(f"Hugging Face API Error: Status Code {response.status_code} - Response: {response.text}")
                return 'neutral'

        except requests.exceptions.RequestException as e:
            logger.error(f"Attempt {attempt + 1}: An exception occurred: {e}")
            time.sleep(1) # انتظر ثانية قبل المحاولة التالية

    # إذا فشلت كل المحاولات
    logger.error("All retry attempts failed for sentiment analysis.")
    return 'neutral'

