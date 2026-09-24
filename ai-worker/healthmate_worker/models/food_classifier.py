"""Optional pretrained Food-101 candidate generator.

The dependency is intentionally lazy: the normal Worker stays lightweight when
FOOD_CLASSIFIER_MODEL is empty. A configured Hugging Face image-classification
checkpoint is used only for dish-name candidates; the VLM still estimates the
visible portion and nutrients.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps


@lru_cache(maxsize=2)
def _load(model_id: str):
    try:
        import torch
        from transformers import AutoImageProcessor, AutoModelForImageClassification
    except ImportError:
        raise RuntimeError(
            "Food classifier requires requirements-food-classifier.txt"
        ) from None
    processor = AutoImageProcessor.from_pretrained(model_id)
    model = AutoModelForImageClassification.from_pretrained(model_id)
    model.eval()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    return torch, processor, model, device


def classify_food_candidates(image_path: Path, model_id: str, top_k: int = 3) -> list[dict]:
    if not model_id.strip():
        return []
    torch, processor, model, device = _load(model_id.strip())
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    inputs = {key: value.to(device) for key, value in processor(images=image, return_tensors="pt").items()}
    with torch.inference_mode():
        probabilities = torch.softmax(model(**inputs).logits[0], dim=-1)
    count = min(max(1, top_k), probabilities.numel())
    scores, indices = torch.topk(probabilities, count)
    labels = model.config.id2label
    return [
        {
            "label": str(labels.get(int(index), labels.get(str(int(index)), int(index)))),
            "score": round(float(score), 6),
        }
        for score, index in zip(scores.detach().cpu(), indices.detach().cpu())
    ]

