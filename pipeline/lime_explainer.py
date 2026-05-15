"""
pipeline/lime_explainer.py

Generates LIME (Local Interpretable Model-Agnostic Explanations) for
the FIR classification decision.

LIME perturbs the input text and observes how the model's prediction changes,
identifying which words most influenced the "this is a FIR" decision.
"""

import re
import numpy as np
import random


def explain_with_lime(text: str, model_pipeline, num_samples: int = 200) -> dict:
    """
    Lightweight LIME implementation for text classification.

    Args:
        text:           The document text that was classified
        model_pipeline: sklearn Pipeline with (tfidf, clf) steps
        num_samples:    Number of perturbation samples (higher = more accurate)

    Returns:
        {
          "lime_weights": [(word, weight), ...],   # sorted by |weight|
          "summary": str,                           # human-readable explanation
        }
    """
    words = text.lower().split()
    words = [w for w in words if len(w) > 2]   # skip stop-word-sized tokens
    if not words:
        return {"lime_weights": [], "summary": "Not enough tokens to explain."}

    n = len(words)
    rng = random.Random(42)

    # Generate perturbed samples: randomly mask 0–70% of words
    perturbed_texts = []
    masks = []
    for _ in range(num_samples):
        mask = [rng.random() > 0.3 for _ in range(n)]  # 70% keep rate
        masks.append(mask)
        sample = " ".join(w if mask[i] else "" for i, w in enumerate(words))
        perturbed_texts.append(sample)

    # Get model probabilities for all samples
    probas = model_pipeline.predict_proba(perturbed_texts)[:, 1]   # P(FIR)

    # Fit a linear model: presence of each word → change in probability
    mask_matrix = np.array(masks, dtype=float)   # (num_samples, n_words)
    # Simple least-squares: probas ≈ mask_matrix @ weights
    try:
        weights, _, _, _ = np.linalg.lstsq(mask_matrix, probas, rcond=None)
    except Exception:
        weights = np.zeros(n)

    # Pair words with weights, deduplicate, sort by |weight|
    word_weights: dict[str, float] = {}
    for word, weight in zip(words, weights):
        w = word.strip(".,;:\"'()[]{}")
        if len(w) > 2:
            # Keep the highest absolute weight per unique word
            if w not in word_weights or abs(weight) > abs(word_weights[w]):
                word_weights[w] = float(round(weight, 4))

    sorted_pairs = sorted(word_weights.items(), key=lambda x: abs(x[1]), reverse=True)[:15]

    # Build human-readable summary
    positive = [w for w, s in sorted_pairs if s > 0.01][:5]
    negative = [w for w, s in sorted_pairs if s < -0.01][:3]

    parts = []
    if positive:
        parts.append(f"Words pushing toward FIR: {', '.join(positive)}")
    if negative:
        parts.append(f"Words pushing away from FIR: {', '.join(negative)}")
    summary = ". ".join(parts) + "." if parts else "LIME found no strong influential words."

    return {
        "lime_weights": sorted_pairs,
        "summary": summary,
    }