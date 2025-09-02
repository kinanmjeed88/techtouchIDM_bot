from transformers import pipeline

sentiment_analyzer = pipeline(
    "sentiment-analysis",
    model="CAMeL-Lab/bert-base-arabic-camelbert-da-sentiment"
)

def analyze_sentiment_hf(text: str) -> str:
    if not text or not text.strip():
        return 'neutral'
        
    try:
        result = sentiment_analyzer(text)[0]
        label = result["label"]
        return label
    except Exception:
        return 'neutral'

