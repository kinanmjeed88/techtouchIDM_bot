import os
import requests
import logging
import json

logger = logging.getLogger(__name__)

API_URL = os.environ.get('HF_SPACE_API_URL')
HUGGINGFACE_TOKEN = os.environ.get('HUGGINGFACE_TOKEN')

def analyze_sentiment_hf(text: str) -> str:
    """
    تحليل مشاعر النص باستخدام نقطة النهاية المخصصة (HF Space) مع تشخيص مفصل.
    """
    if not text or not text.strip():
        logger.warning("Analysis skipped: Input text is empty.")
        return 'neutral'
    
    if not API_URL:
        logger.error("Analysis failed: HF_SPACE_API_URL environment variable is not set.")
        return 'neutral'

    # بناء رأس الطلب
    headers = {
        "Authorization": f"Bearer {HUGGINGFACE_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # بناء جسم الطلب (Payload)
    payload = {"data": [text]}
    
    logger.info(f"--- [ANALYSIS-START] ---")
    logger.info(f"Sending request to: {API_URL}")
    logger.info(f"Payload: {json.dumps(payload)}")

    try:
        response = requests.post(API_URL, headers=headers, json=payload, timeout=20) # زيادة مهلة الانتظار
        
        logger.info(f"Received response. Status Code: {response.status_code}")
        logger.info(f"Response Body: {response.text}")

        if response.status_code == 200:
            result = response.json()
            
            if 'data' in result and isinstance(result['data'], list) and result['data']:
                sentiment = result['data'][0]
                if sentiment in ['positive', 'negative', 'neutral']:
                    logger.info(f"--- [ANALYSIS-SUCCESS] --- Sentiment: {sentiment}")
                    return sentiment
                else:
                    logger.warning(f"Analysis result is unexpected: {sentiment}")
                    return 'neutral'
            else:
                logger.warning(f"Analysis response format is unexpected: {result}")
                return 'neutral'
        else:
            logger.error(f"--- [ANALYSIS-FAIL] --- Status Code: {response.status_code}")
            return 'neutral'

    except requests.exceptions.RequestException as e:
        logger.error(f"--- [ANALYSIS-EXCEPTION] --- An exception occurred: {e}", exc_info=True)
        return 'neutral'
