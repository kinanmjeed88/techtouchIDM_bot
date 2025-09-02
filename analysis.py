import os
import requests
import logging
import time

logger = logging.getLogger(__name__)

# --- !! استخدام نموذج تحليل مختلف ومشهور !! ---
API_URL = "https://api-inference.huggingface.co/models/akhooli/xlm-roberta-base-arabic-sent"
# -------------------------------------------------

HUGGINGFACE_TOKEN = os.environ.get('HUGGINGFACE_TOKEN')
headers = {"Authorization": f"Bearer {HUGGINGFACE_TOKEN}"}

def analyze_sentiment_hf(text: str) -> str:
    """
    تحليل مشاعر النص باستخدام نموذج بديل مع آلية إعادة المحاولة.
    """
    if not text or not text.strip():
        return 'neutral'

    max_retries = 3
    for attempt in range(max_retries):
        try:
            # هذا النموذج يتوقع بنية مختلفة قليلاً للـ payload
            payload = {"inputs": text}
            response = requests.post(API_URL, headers=headers, json=payload, timeout=20)

            if response.status_code == 200:
                result = response.json()
                # بنية الرد مختلفة أيضًا، يجب أن نتعامل معها
                if result and isinstance(result, list) and result[0]:
                    # البحث عن التصنيف الأعلى درجة
                    top_sentiment = max(result[0], key=lambda x: x['score'])
                    label = top_sentiment['label'].lower()
                        
                    # مطابقة التصنيفات مع قيمنا
                    if label == 'positive':
                        return 'positive'
                    elif label == 'negative':
                        return 'negative'
                    else: # 'neutral' or any other label
                        return 'neutral'

            elif response.status_code == 503:
                logger.warning(f"Attempt {attempt + 1}: Service unavailable (503), retrying in 3 seconds...")
                time.sleep(3)
                continue
                
            else:
                logger.error(f"Hugging Face API Error (Model: xlm-roberta): Status Code {response.status_code} - Response: {response.text}")
                return 'neutral'

        except requests.exceptions.RequestException as e:
            logger.error(f"Attempt {attempt + 1}: An exception occurred: {e}")
            time.sleep(2)

    logger.error("All retry attempts failed for sentiment analysis.")
    return 'neutral'
