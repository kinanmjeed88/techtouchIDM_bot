import logging
from arabert.preprocess import ArabertPreprocessor
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

logger = logging.getLogger(__name__)

# --- تهيئة النموذج والمحلل مرة واحدة ---
try:
    model_name = "aubmindlab/bert-base-arabertv2"
    arabert_prep = ArabertPreprocessor(model_name=model_name)
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(model_name)
    logger.info("AraBERT model loaded successfully.")
except Exception as e:
    logger.error(f"Failed to load AraBERT model: {e}", exc_info=True)
    # في حالة فشل التحميل، سيتم تعطيل التحليل
    model = None 
# -----------------------------------------

def analyze_sentiment_hf(text: str) -> str:
    """
    تحليل مشاعر النص باستخدام مكتبة AraBERT التي تعمل محليًا.
    """
    if not model:
        logger.warning("Sentiment analysis is disabled because the model failed to load.")
        return 'neutral'

    if not text or not text.strip():
        return 'neutral'
    
    try:
        # معالجة النص
        text_preprocessed = arabert_prep.preprocess(text)
        
        # تحويل النص إلى مدخلات للنموذج
        inputs = tokenizer.encode_plus(
            text_preprocessed,
            add_special_tokens=True,
            max_length=512,
            padding='max_length',
            truncation=True,
            return_tensors="pt"
        )
        
        # تشغيل النموذج
        with torch.no_grad():
            outputs = model(**inputs)
        
        # الحصول على النتيجة
        # 0 -> سلبي, 1 -> محايد, 2 -> إيجابي
        prediction = torch.argmax(outputs.logits, dim=1).item()
        
        if prediction == 2:
            return 'positive'
        elif prediction == 0:
            return 'negative'
        else:
            return 'neutral'

    except Exception as e:
        logger.error(f"AraBERT analysis failed for text: '{text}'. Error: {e}", exc_info=True)
        return 'neutral'
