"""
pipeline/lime_explainer.py
──────────────────────────
Model-agnostic LIME (Local Interpretable Model-Agnostic Explanations) for
text classification. Works with any object exposing `predict_proba(list[str])`.

How it works:
    1. Tokenise the input into words.
    2. Generate `num_samples` perturbed copies by randomly dropping ~30%
       of the words.
    3. Get the model's P(class=1) for each perturbation.
    4. Fit a linear model that maps "word present / absent" → probability.
    5. Each word's coefficient = its LIME weight (positive → pushes the
       prediction toward FIR; negative → pushes away).

This is the canonical LIME-for-text approach from Ribeiro et al. (2016),
implemented from scratch here so we don't carry the full `lime` package as
a dependency (it pulls scikit-image, matplotlib, etc. — way too heavy).
"""

from __future__ import annotations

import random
from typing import Any, Dict, List

import numpy as np


def explain_with_lime(text: str, model_pipeline: Any, num_samples: int = 200) -> Dict:
    """Args:
        text:           Document to explain.
        model_pipeline: Anything with `predict_proba(list[str]) -> ndarray[N, 2]`.
                        Both sklearn pipelines and the SBERT wrapper in
                        orchestrator._lime_on_sbert_classifier satisfy this.
        num_samples:    More samples → smoother explanation, more compute.

    Returns:
        {"lime_weights": [(word, weight), ...],  "summary": str}
    """
    words = [w for w in text.lower().split() if len(w) > 2]
    if not words:
        return {"lime_weights": [], "summary": "Not enough tokens to explain."}

    n = len(words)
    rng = random.Random(42)

    # Generate perturbed samples (boolean mask = include word i?)
    perturbed_texts: List[str] = []
    masks: List[List[bool]] = []
    for _ in range(num_samples):
        mask = [rng.random() > 0.3 for _ in range(n)]   # 70% keep rate
        masks.append(mask)
        perturbed_texts.append(" ".join(w if mask[i] else "" for i, w in enumerate(words)))

    # Get model probabilities for all samples — P(class=1)
    probas = model_pipeline.predict_proba(perturbed_texts)[:, 1]

    # Fit a linear model: probas ≈ mask_matrix @ weights
    mask_matrix = np.array(masks, dtype=float)
    try:
        weights, _, _, _ = np.linalg.lstsq(mask_matrix, probas, rcond=None)
    except Exception:
        weights = np.zeros(n)

    # Pair words with weights, deduplicate, sort by |weight|
    word_weights: Dict[str, float] = {}
    for word, w in zip(words, weights):
        clean = word.strip(".,;:\"'()[]{}")
        if len(clean) > 2:
            # Keep the highest absolute weight per unique word
            if clean not in word_weights or abs(w) > abs(word_weights[clean]):
                word_weights[clean] = float(round(w, 4))

    sorted_pairs = sorted(word_weights.items(), key=lambda kv: abs(kv[1]), reverse=True)[:15]

    positive = [w for w, s in sorted_pairs if s > 0.01][:5]
    negative = [w for w, s in sorted_pairs if s < -0.01][:3]

    parts = []
    if positive:
        parts.append(f"Words pushing toward FIR: {', '.join(positive)}")
    if negative:
        parts.append(f"Words pushing away from FIR: {', '.join(negative)}")
    summary = ". ".join(parts) + "." if parts else "LIME found no strong influential words."

    return {"lime_weights": sorted_pairs, "summary": summary}