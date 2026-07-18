#!/usr/bin/env python3
"""
ML-based paper ranking for arXiv astro-ph.

Inspired by Benty Fields (benty-fields.com):
  - Train a binary classifier per user on saved (positive) vs non-saved (negative) papers
  - Use TF-IDF features on title + abstract
  - Score new papers with the classifier probability

Usage:
    python src/arxiv_ml_ranker.py train       # train/retrain the model
    python src/arxiv_ml_ranker.py score <json_file>  # score papers from JSON

The model is saved to local/ml_ranker/ and auto-loaded by arxiv_daily_ranker_html.py.
"""

import argparse
import json
import math
import os
import pickle
import re
import sqlite3
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

# =============================================================================
# CONFIG
# =============================================================================

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "local", "arxiv_papers.db")
MODEL_DIR = os.path.join(PROJECT_ROOT, "local", "ml_ranker")
MODEL_PATH = os.path.join(MODEL_DIR, "model.pkl")
RECENT_PAPERS_PATH = os.path.join(PROJECT_ROOT, "local", "arxiv_papers.json")
VECTORIZER_PATH = os.path.join(MODEL_DIR, "vectorizer.pkl")
STABILITY_PATH = os.path.join(MODEL_DIR, "feature_stability.json")
CUSTOM_POSITIVE_PATH = os.path.join(MODEL_DIR, "custom_positive.json")
CUSTOM_NEGATIVE_PATH = os.path.join(MODEL_DIR, "custom_negative.json")
CUSTOM_KEYWORD_LOGIT_BOOST = 0.75
CUSTOM_KEYWORD_MAX_MATCHES = 3
FEATURE_DISPLAY_LIMIT = 30

os.makedirs(MODEL_DIR, exist_ok=True)


# =============================================================================
# CUSTOM POSITIVE / NEGATIVE KEYWORDS
# =============================================================================

def load_custom_positive():
    """Load user-defined positive keywords."""
    if not os.path.exists(CUSTOM_POSITIVE_PATH):
        return []
    with open(CUSTOM_POSITIVE_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_custom_negative():
    """Load user-defined negative keywords."""
    if not os.path.exists(CUSTOM_NEGATIVE_PATH):
        return []
    with open(CUSTOM_NEGATIVE_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_custom_positive(keywords):
    """Save user-defined positive keywords."""
    with open(CUSTOM_POSITIVE_PATH, 'w', encoding='utf-8') as f:
        json.dump(keywords, f, indent=2)


def save_custom_negative(keywords):
    """Save user-defined negative keywords."""
    with open(CUSTOM_NEGATIVE_PATH, 'w', encoding='utf-8') as f:
        json.dump(keywords, f, indent=2)


def add_custom_positive(keyword):
    """Add a keyword to the custom positive list."""
    keywords = load_custom_positive()
    keyword = keyword.strip().lower()
    if keyword and keyword not in keywords:
        keywords.append(keyword)
        save_custom_positive(keywords)
    return True


def add_custom_negative(keyword):
    """Add a keyword to the custom negative list."""
    keywords = load_custom_negative()
    keyword = keyword.strip().lower()
    if keyword and keyword not in keywords:
        keywords.append(keyword)
        save_custom_negative(keywords)
    return True


def remove_custom_positive(keyword):
    """Remove a keyword from the custom positive list."""
    keywords = load_custom_positive()
    keyword = keyword.strip().lower()
    if keyword in keywords:
        keywords.remove(keyword)
        save_custom_positive(keywords)
        return True
    return False


def remove_custom_negative(keyword):
    """Remove a keyword from the custom negative list."""
    keywords = load_custom_negative()
    keyword = keyword.strip().lower()
    if keyword in keywords:
        keywords.remove(keyword)
        save_custom_negative(keywords)
        return True
    return False


def deduplicate_custom_keywords():
    """
    Canonically deduplicate each list and remove negative concepts duplicated
    by positive concepts. Positive wins. Returns (positive_list, negative_list).
    """
    original_pos = load_custom_positive()
    original_neg = load_custom_negative()

    def unique_variants(keywords):
        seen = set()
        unique = []
        for keyword in keywords:
            keyword = keyword.strip().lower()
            key = canonical_feature_key(keyword)
            if keyword and key and key not in seen:
                seen.add(key)
                unique.append(keyword)
        return unique, seen

    pos, pos_keys = unique_variants(original_pos)
    neg, _ = unique_variants(original_neg)
    new_neg = [word for word in neg if canonical_feature_key(word) not in pos_keys]
    if pos != original_pos:
        save_custom_positive(pos)
    if new_neg != original_neg:
        save_custom_negative(new_neg)
    return pos, new_neg


def custom_keyword_logit_adjustments(texts):
    """Return bounded per-paper log-odds adjustments from manual keywords."""
    positive, negative = deduplicate_custom_keywords()

    def compile_keyword(keyword):
        canonical = canonical_feature_key(re.sub(r'[^\w]+', ' ', keyword))
        tokens = [re.escape(token) for token in canonical.split() if token]
        if not tokens:
            return None
        return re.compile(r'(?<!\w)' + r'\s+'.join(tokens) + r'(?!\w)', re.IGNORECASE)

    positive_patterns = [pattern for word in positive if (pattern := compile_keyword(word))]
    negative_patterns = [pattern for word in negative if (pattern := compile_keyword(word))]
    adjustments = []
    for text in texts:
        text = canonical_feature_key(re.sub(r'[^\w]+', ' ', text))
        positive_matches = min(
            CUSTOM_KEYWORD_MAX_MATCHES,
            sum(bool(pattern.search(text)) for pattern in positive_patterns),
        )
        negative_matches = min(
            CUSTOM_KEYWORD_MAX_MATCHES,
            sum(bool(pattern.search(text)) for pattern in negative_patterns),
        )
        adjustments.append(CUSTOM_KEYWORD_LOGIT_BOOST * (positive_matches - negative_matches))
    return np.asarray(adjustments, dtype=float)


def adjusted_model_probabilities(clf, X, texts):
    """Apply manual keyword preferences to model log-odds, then return probabilities."""
    logits = clf.decision_function(X) + custom_keyword_logit_adjustments(texts)
    logits = np.clip(logits, -50, 50)
    return 1.0 / (1.0 + np.exp(-logits))


# =============================================================================
# DATA FETCHING
# =============================================================================

def get_saved_papers():
    """Load all saved papers from the DB as positive examples."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT arxiv_id, title, authors, abstract FROM saved_papers")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_my_publications():
    """Load user's own publications from the DB as additional positive signal."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT title, authors, abstract FROM my_publications")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def fetch_random_astroph_papers(max_results=100, days_back=30):
    """Fetch a random sample of recent astro-ph papers as negative examples."""
    end = datetime.now()
    start = end - timedelta(days=days_back)
    
    start_str = start.strftime("%Y%m%d%H%M")
    end_str = end.strftime("%Y%m%d%H%M")
    
    url = (
        f"https://export.arxiv.org/api/query?"
        f"search_query=cat:astro-ph*+AND+submittedDate:[{start_str}+TO+{end_str}]"
        f"&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending"
    )
    
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req, timeout=20)
    data = resp.read().decode('utf-8')
    
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    root = ET.fromstring(data)
    entries = root.findall('atom:entry', ns)
    
    papers = []
    for entry in entries:
        id_elem = entry.find('atom:id', ns)
        title_elem = entry.find('atom:title', ns)
        summary_elem = entry.find('atom:summary', ns)
        
        arxiv_id = id_elem.text if id_elem is not None else ''
        short_id = arxiv_id.split('/')[-1].replace('abs/', '') if arxiv_id else ''
        short_id = re.sub(r'v\d+$', '', short_id)
        
        title = title_elem.text.strip() if title_elem is not None else ''
        abstract = summary_elem.text.strip() if summary_elem is not None else ''
        
        papers.append({
            'id': short_id,
            'title': title,
            'abstract': abstract,
        })
    
    return papers


def load_recent_papers_as_negatives():
    """Use the already-fetched daily corpus when arXiv throttles training."""
    try:
        with open(RECENT_PAPERS_PATH, 'r', encoding='utf-8') as f:
            papers = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    return [
        {
            'id': re.sub(r'v\d+$', '', str(p.get('id', '')).split('/')[-1]),
            'title': p.get('title', ''),
            'abstract': p.get('abstract', ''),
        }
        for p in papers
        if p.get('title') or p.get('abstract')
    ]


# =============================================================================
# TEXT PREPROCESSING
# =============================================================================

def _clean_text(text):
    """Clean text for TF-IDF input: strip LaTeX, HTML, math, URLs, citations."""
    if not text:
        return ""
    text = text.lower()
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Remove HTML entities
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    text = re.sub(r'&#\d+;', ' ', text)
    text = re.sub(r'&#x[0-9a-fA-F]+;', ' ', text)
    # Remove LaTeX commands
    text = re.sub(r'\\[a-zA-Z]+\*?(\{[^}]*\})*', ' ', text)
    # Remove LaTeX superscripts/subscripts
    text = re.sub(r'[\^_]\{[^}]*\}', ' ', text)
    text = re.sub(r'[\^_]', ' ', text)
    # Remove math mode
    text = re.sub(r'\$[^$]*\$', ' ', text)
    # Remove URLs
    text = re.sub(r'http[s]?://\S+', ' ', text)
    # Remove arXiv IDs
    text = re.sub(r'ar[xX]iv\s*[:\s]*[\d\.]+[v\d]*', ' ', text)
    # Remove citations
    text = re.sub(r'\[\d+\]', ' ', text)
    # Remove parentheses with single numbers
    text = re.sub(r'\(\d+\)', ' ', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def paper_to_text(paper):
    """Convert a paper dict to a single text string for TF-IDF."""
    title = _clean_text(paper.get('title', ''))
    abstract = _clean_text(paper.get('abstract', ''))
    return f"{title} {abstract}"


# =============================================================================
# MODEL
# =============================================================================

def build_training_data():
    """
    Build positive + negative training set.
    
    Positive: saved papers only.
    Negative: random recent astro-ph papers not in saved_papers.
    """
    saved = get_saved_papers()
    
    # Get IDs of saved papers to exclude from negatives
    saved_ids = {p['arxiv_id'] for p in saved}
    
    # Fetch negative examples
    negatives = load_recent_papers_as_negatives()
    if negatives:
        print(f"Using {len(negatives)} locally cached recent papers as negative examples")
    else:
        print("Local recent-paper cache unavailable; fetching negative examples from arXiv...")
        try:
            negatives = fetch_random_astroph_papers(max_results=150, days_back=30)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(
                f"Could not fetch negative examples from arXiv ({exc}), and no local recent-paper cache is available"
            ) from exc
    negatives = [p for p in negatives if p['id'] not in saved_ids]
    print(f"  Fetched {len(negatives)} negative papers (excluded {len(saved_ids)} saved)")
    
    # Build texts and labels
    texts = []
    labels = []
    
    # Positive: saved papers (label=1)
    for p in saved:
        texts.append(paper_to_text(p))
        labels.append(1)
    print(f"  Positive (saved): {len(saved)}")
    
    # Negative: random recent papers (label=0)
    # Balance negatives to match positives
    n_pos = sum(labels)
    n_neg = min(len(negatives), n_pos * 2)  # up to 2:1 ratio
    for p in negatives[:n_neg]:
        texts.append(paper_to_text(p))
        labels.append(0)
    print(f"  Negative: {n_neg}")
    
    return texts, np.array(labels)


def document_frequency_thresholds(n_documents):
    """Return conservative TF-IDF document-frequency thresholds."""
    min_df = min(5, max(2, math.ceil(n_documents * 0.01)))
    # With only two or three documents, 85% would permit fewer documents than
    # min_df and scikit-learn correctly rejects that contradictory range.
    max_df = 1.0 if n_documents < 4 else 0.85
    return min_df, max_df


def compute_feature_stability(X, labels, feature_names, n_resamples=30, random_state=42):
    """Estimate how consistently features rank highly across stratified subsamples."""
    positive_indices = np.flatnonzero(labels == 1)
    negative_indices = np.flatnonzero(labels == 0)
    if len(positive_indices) < 4 or len(negative_indices) < 4 or X.shape[1] == 0:
        return {
            "available": False,
            "reason": "At least four positive and four negative papers are required",
            "n_resamples": 0,
            "threshold": 0.6,
            "positive": {},
            "negative": {},
        }

    rng = np.random.default_rng(random_state)
    positive_counts = np.zeros(X.shape[1], dtype=int)
    negative_counts = np.zeros(X.shape[1], dtype=int)
    # Track a wider candidate pool than the 30 displayed terms so correlated
    # features have a fair chance to demonstrate repeatability.
    top_k = min(X.shape[1], max(60, min(200, X.shape[1] // 20)))

    for seed in range(n_resamples):
        pos_sample = rng.choice(
            positive_indices, size=max(3, round(len(positive_indices) * 0.8)), replace=False
        )
        neg_sample = rng.choice(
            negative_indices, size=max(3, round(len(negative_indices) * 0.8)), replace=False
        )
        sample = np.concatenate([pos_sample, neg_sample])
        sample_labels = labels[sample]
        estimator = LogisticRegression(
            C=1.0,
            class_weight='balanced',
            max_iter=1000,
            random_state=random_state + seed,
        )
        estimator.fit(X[sample], sample_labels)
        sample_coef = estimator.coef_[0]

        pos_ranked = np.argsort(sample_coef)[-top_k:]
        neg_ranked = np.argsort(sample_coef)[:top_k]
        positive_counts[pos_ranked[sample_coef[pos_ranked] > 0]] += 1
        negative_counts[neg_ranked[sample_coef[neg_ranked] < 0]] += 1

    return {
        "available": True,
        "n_resamples": n_resamples,
        "sample_fraction": 0.8,
        "candidate_count": top_k,
        "threshold": 0.6,
        "positive": {
            str(feature_names[i]): round(int(count) / n_resamples, 4)
            for i, count in enumerate(positive_counts) if count
        },
        "negative": {
            str(feature_names[i]): round(int(count) / n_resamples, 4)
            for i, count in enumerate(negative_counts) if count
        },
    }


def load_feature_stability():
    if not os.path.exists(STABILITY_PATH):
        return {"available": False, "positive": {}, "negative": {}, "threshold": 0.6}
    try:
        with open(STABILITY_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"available": False, "positive": {}, "negative": {}, "threshold": 0.6}


def canonical_feature_key(feature):
    """Return a conservative key for deduplicating singular/plural features."""
    def singularize(token):
        irregular = {
            'analyses': 'analysis',
            'gases': 'gas',
            'lenses': 'lens',
            'indices': 'index',
            'matrices': 'matrix',
        }
        if token in irregular:
            return irregular[token]
        # galaxies -> galaxy, studies -> study; avoid short forms such as ies.
        if len(token) > 4 and token.endswith('ies'):
            return token[:-3] + 'y'
        # classes -> class, processes -> process.
        if len(token) > 5 and token.endswith(('sses', 'shes', 'ches', 'xes', 'zes')):
            return token[:-2]
        # quasars -> quasar, stars -> star. Preserve common non-plural endings
        # such as physics, analysis, lens, gas, cosmos, and apparatus.
        if (len(token) > 4 and token.endswith('s')
                and not token.endswith(('ss', 'us', 'is', 'ics', 'os'))):
            return token[:-1]
        return token

    normalized = re.sub(r'[-_]+', ' ', feature.lower())
    return ' '.join(singularize(token) for token in normalized.split())


def is_contiguous_subphrase(shorter, longer):
    """Return True when shorter is a proper contiguous token span of longer."""
    short_tokens = shorter.split()
    long_tokens = longer.split()
    if not short_tokens or len(short_tokens) >= len(long_tokens):
        return False
    width = len(short_tokens)
    return any(
        long_tokens[start:start + width] == short_tokens
        for start in range(len(long_tokens) - width + 1)
    )


def deduplicate_feature_variants(features):
    """Keep the best variant and hide terms represented by longer stable phrases."""
    canonical_items = [
        (feature, canonical_feature_key(feature[0])) for feature in features
    ]
    phrase_keys = [key for _, key in canonical_items if len(key.split()) > 1]
    seen = set()
    unique = []
    for feature, key in canonical_items:
        # A longer stable phrase is more informative in the inspector than any
        # proper contiguous component. For example, active galactic nuclei
        # suppresses active galactic, galactic nuclei, and each unigram.
        if any(is_contiguous_subphrase(key, phrase) for phrase in phrase_keys):
            continue
        if key not in seen:
            seen.add(key)
            unique.append(feature)
    return unique


def train_model():
    """Train a TF-IDF + Logistic Regression model and save to disk."""
    texts, labels = build_training_data()
    
    if len(labels) == 0 or labels.sum() == 0:
        print("ERROR: No positive training data. Save some papers first.")
        return False
    
    print(f"\nTraining on {len(texts)} samples ({labels.sum()} positive, {len(labels)-labels.sum()} negative)...")
    
    # TF-IDF + Logistic Regression pipeline
    min_df, max_df = document_frequency_thresholds(len(texts))
    print(f"Document-frequency thresholds: min_df={min_df}, max_df={max_df:.2f}")
    vectorizer = TfidfVectorizer(
        max_features=10000,
        ngram_range=(1, 2),      # unigrams + bigrams
        min_df=min_df,           # ignore rare, weakly supported terms
        max_df=max_df,           # ignore corpus-wide generic terms
        stop_words='english',
        sublinear_tf=True,       # use 1 + log(tf) instead of raw tf
    )
    
    X = vectorizer.fit_transform(texts)
    
    clf = LogisticRegression(
        C=1.0,
        class_weight='balanced',  # handle imbalance
        max_iter=1000,
        random_state=42,
    )
    clf.fit(X, labels)

    stability = compute_feature_stability(X, labels, vectorizer.get_feature_names_out())
    
    # Save model and vectorizer
    with open(MODEL_PATH, 'wb') as f:
        pickle.dump(clf, f)
    with open(VECTORIZER_PATH, 'wb') as f:
        pickle.dump(vectorizer, f)
    with open(STABILITY_PATH, 'w', encoding='utf-8') as f:
        json.dump(stability, f, indent=2, sort_keys=True)
    
    print(f"Model saved to {MODEL_PATH}")
    print(f"Vectorizer saved to {VECTORIZER_PATH}")
    if stability["available"]:
        print(f"Feature stability saved to {STABILITY_PATH} ({stability['n_resamples']} resamples)")
    else:
        print(f"Feature stability unavailable: {stability['reason']}")
    print(f"Feature space: {X.shape[1]} dimensions")
    
    return True


# =============================================================================
# SCORING
# =============================================================================

def load_model():
    """Load the trained model and vectorizer from disk."""
    if not os.path.exists(MODEL_PATH) or not os.path.exists(VECTORIZER_PATH):
        return None, None
    
    with open(MODEL_PATH, 'rb') as f:
        clf = pickle.load(f)
    with open(VECTORIZER_PATH, 'rb') as f:
        vectorizer = pickle.load(f)
    
    return clf, vectorizer


def score_papers(papers):
    """
    Score a list of paper dicts using the trained ML model.
    
    Args:
        papers: list of dicts with 'title' and 'abstract' keys
    
    Returns:
        list of (probability, paper) tuples, sorted by probability descending
    """
    clf, vectorizer = load_model()
    
    if clf is None or vectorizer is None:
        print("WARNING: No ML model found. Run 'python src/arxiv_ml_ranker.py train' first.")
        return []
    
    texts = [paper_to_text(p) for p in papers]
    X = vectorizer.transform(texts)
    probs = adjusted_model_probabilities(clf, X, texts)
    
    scored = list(zip(probs, papers))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def score_papers_with_fallback(papers):
    """
    Score papers using ML model only.
    
    Returns:
        list of (norm_score, raw_score, paper) tuples
    """
    clf, vectorizer = load_model()
    
    if clf is not None and vectorizer is not None:
        texts = [paper_to_text(p) for p in papers]
        X = vectorizer.transform(texts)
        probs = adjusted_model_probabilities(clf, X, texts)
        
        # Normalize to 0-100 for display consistency
        if len(probs) > 1:
            max_p = probs.max()
            min_p = probs.min()
            range_p = max_p - min_p if max_p > min_p else 1.0
            norm_scores = 100 * (probs - min_p) / range_p
        else:
            norm_scores = probs * 100
        
        return [(int(norm_scores[i]), int(probs[i] * 100), papers[i]) for i in range(len(papers))]
    
    print("WARNING: No ML model found. Run 'python src/arxiv_ml_ranker.py train' first.")
    return [(0, 0, p) for p in papers]


# =============================================================================
# FEATURES HTML
# =============================================================================

def generate_features_html(output_path=None):
    """Generate an interactive HTML page showing the top learned + custom features."""
    import html as html_module
    
    clf, vectorizer = load_model()
    if clf is None or vectorizer is None:
        print("No model found. Train first.")
        return False
    
    feature_names = vectorizer.get_feature_names_out()
    coef = clf.coef_[0]
    stability = load_feature_stability()
    stability_threshold = float(stability.get("threshold", 0.6))
    positive_stability = stability.get("positive", {})
    negative_stability = stability.get("negative", {})
    
    # Get training data counts
    saved = get_saved_papers()
    n_pos = len(saved)
    
    custom_pos, custom_neg = deduplicate_custom_keywords()
    custom_keys = {
        canonical_feature_key(keyword) for keyword in custom_pos + custom_neg
    }
    positive_auto_limit = max(0, FEATURE_DISPLAY_LIMIT - len(custom_pos))
    negative_auto_limit = max(0, FEATURE_DISPLAY_LIMIT - len(custom_neg))
    
    noisy_tokens = {
        'sub', 'sup', 'br', 'lt', 'gt', 'amp', 'quot', 'apos', 'nbsp', 'ndash', 'mdash',
        'span', 'div', 'p', 'h1', 'h2', 'h3', 'strong', 'em', 'b', 'i', 'u',
        'table', 'tr', 'td', 'li', 'ul', 'ol',
        'arxiv', '2606', '2605', '2604', '2607', '2608', '2609', '2610', '2611', '2612',
    }
    
    # Common ambiguous/generic words that shouldn't be features
    ambiguous_words = {
        'using', 'use', 'used', 'based', 'via', 'with', 'without', 'within',
        'between', 'among', 'during', 'after', 'before', 'above', 'below', 'under',
        'over', 'into', 'onto', 'upon', 'toward', 'towards', 'across', 'through',
        'throughout', 'around', 'about', 'against', 'along', 'amongst', 'beside',
        'besides', 'beyond', 'concerning', 'despite', 'except', 'excluding', 'following',
        'including', 'like', 'near', 'off', 'opposite', 'outside', 'past',
        'regarding', 'round', 'since', 'than', 'till', 'to', 'underneath',
        'unlike', 'until', 'unto', 'up', 'versus', 'vs', 'via', 'worth',
        'and', 'or', 'but', 'nor', 'so', 'yet', 'for', 'as', 'because', 'if',
        'when', 'where', 'while', 'although', 'though', 'unless', 'whether', 'either',
        'neither', 'both', 'all', 'any', 'each', 'every', 'few', 'less', 'little',
        'many', 'more', 'most', 'much', 'no', 'none', 'one', 'other', 'others',
        'several', 'some', 'such', 'various', 'whole', 'this', 'that',
        'these', 'those', 'they', 'them', 'their', 'theirs', 'the', 'a', 'an',
        'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had',
        'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might',
        'can', 'shall', 'must', 'ought', 'need', 'dare', 'used', 'get', 'got',
        'go', 'going', 'went', 'gone', 'make', 'made', 'take', 'took', 'taken',
        'come', 'came', 'coming', 'see', 'saw', 'seen', 'know', 'knew', 'known',
        'think', 'thought', 'say', 'said', 'saying', 'look',
        'looked', 'looking', 'want', 'wanted', 'give', 'gave', 'given',
        'find', 'found', 'finding', 'tell', 'told', 'telling',
        'work', 'worked', 'working', 'call', 'called', 'calling',
        'try', 'tried', 'trying', 'ask', 'asked', 'asking', 'need', 'needed',
        'needed', 'feel', 'felt', 'feeling', 'become', 'became', 'becoming',
        'leave', 'left', 'leaving', 'put', 'putting', 'mean', 'meant', 'meaning',
        'keep', 'kept', 'keeping', 'let', 'letting', 'begin', 'began', 'beginning',
        'seem', 'seemed', 'seeming', 'help', 'helped', 'helping', 'show', 'showed',
        'showing', 'hear', 'heard', 'hearing', 'play', 'played', 'playing', 'run',
        'ran', 'running', 'move', 'moved', 'moving', 'live', 'lived', 'living',
        'believe', 'believed', 'believing', 'hold', 'held', 'holding', 'bring',
        'brought', 'bringing', 'happen', 'happened', 'happening', 'stand', 'stood',
        'standing', 'lose', 'lost', 'losing', 'pay', 'paid', 'paying', 'meet',
        'met', 'meeting', 'include', 'included', 'including', 'continue', 'continued',
        'continuing', 'set', 'setting', 'learn', 'learned', 'learning', 'change',
        'changed', 'changing', 'lead', 'led', 'leading', 'understand', 'understood',
        'understanding', 'watch', 'watched', 'watching', 'follow', 'followed', 'following',
        'stop', 'stopped', 'stopping', 'create', 'created', 'creating', 'speak',
        'spoke', 'spoken', 'speaking', 'read', 'reading', 'allow', 'allowed',
        'allowing', 'add', 'added', 'adding', 'spend', 'spent', 'spending', 'grow',
        'grew', 'grown', 'growing', 'open', 'opened', 'opening', 'walk', 'walked',
        'walking', 'win', 'won', 'winning', 'offer', 'offered', 'offering', 'remember',
        'remembered', 'remembering', 'love', 'loved', 'loving', 'consider', 'considered',
        'considering', 'appear', 'appeared', 'appearing', 'buy', 'bought', 'buying',
        'wait', 'waited', 'waiting', 'serve', 'served', 'serving', 'die', 'died',
        'dying', 'send', 'sent', 'sending', 'expect', 'expected', 'expecting', 'build',
        'built', 'building', 'stay', 'stayed', 'staying', 'fall', 'fell', 'fallen',
        'falling', 'cut', 'cutting', 'reach', 'reached', 'reaching', 'kill', 'killed',
        'killing', 'remain', 'remained', 'remaining', 'suggest', 'suggested', 'suggesting',
        'raise', 'raised', 'raising', 'pass', 'passed', 'passing', 'sell', 'sold',
        'selling', 'require', 'required', 'requiring', 'report', 'reported', 'reporting',
        'decide', 'decided', 'deciding', 'pull', 'pulled', 'pulling', 'very', 'also',
        'just', 'only', 'even', 'back', 'still', 'way', 'many', 'however', 'too',
        'already', 'enough', 'almost', 'quite', 'rather', 'pretty', 'fairly', 'really',
        'actually', 'certainly', 'definitely', 'exactly', 'probably', 'perhaps',
        'possibly', 'likely', 'unlikely', 'maybe', 'may', 'might', 'must', 'shall',
        'will', 'would', 'could', 'should', 'can', 'need', 'dare', 'ought', 'used',
        'able', 'unable', 'possible', 'impossible', 'likely', 'unlikely', 'sure',
        'certain', 'true', 'false', 'right', 'wrong', 'correct', 'incorrect',
        'exact', 'approximate', 'approx', 'about', 'around', 'roughly', 'nearly',
        'almost', 'close', 'closer', 'closest', 'far', 'further', 'furthest',
        'near', 'nearer', 'nearest', 'next', 'last', 'first', 'second', 'third',
        'new', 'old', 'older', 'oldest', 'young', 'younger', 'youngest', 'early',
        'earlier', 'earliest', 'late', 'later', 'latest', 'recent', 'recently',
        'present', 'current', 'previous', 'former', 'future', 'past', 'ago',
        'now', 'then', 'today', 'tomorrow', 'yesterday', 'soon', 'later', 'before',
        'after', 'since', 'until', 'till', 'yet', 'already', 'still', 'ever',
        'never', 'always', 'often', 'sometimes', 'usually', 'frequently', 'rarely',
        'seldom', 'occasionally', 'generally', 'typically', 'mainly', 'mostly',
        'partly', 'fully', 'completely', 'totally', 'entirely', 'wholly', 'half',
        'quarter', 'double', 'triple', 'single', 'multiple', 'several', 'various',
        'different', 'same', 'similar', 'equal', 'equivalent', 'opposite', 'reverse',
        'inverse', 'direct', 'indirect', 'positive', 'negative', 'neutral', 'active',
        'passive', 'dynamic', 'static', 'stable', 'unstable', 'constant', 'variable',
        'fixed', 'flexible', 'rigid', 'elastic', 'plastic', 'permanent', 'temporary',
        'transient', 'steady', 'uniform', 'homogeneous', 'heterogeneous', 'isotropic',
        'anisotropic', 'symmetric', 'asymmetric', 'parallel', 'perpendicular',
        'orthogonal', 'tangential', 'radial', 'axial', 'lateral', 'longitudinal',
        'transverse', 'horizontal', 'vertical', 'oblique', 'inclined', 'tilted',
        'upright', 'upside', 'downside', 'top', 'bottom', 'side', 'front', 'back',
        'left', 'right', 'center', 'central', 'middle', 'edge', 'corner', 'end',
        'start', 'beginning', 'origin', 'source', 'target', 'destination', 'goal',
        'objective', 'purpose', 'aim', 'intent', 'intention', 'plan', 'design',
        'scheme', 'strategy', 'tactic', 'method', 'methodology', 'technique',
        'approach', 'procedure', 'process', 'protocol', 'algorithm', 'recipe',
        'rule', 'principle', 'law', 'theorem', 'lemma', 'corollary', 'proposition',
        'hypothesis', 'theory', 'model', 'framework', 'paradigm', 'concept',
        'idea', 'notion', 'view', 'opinion', 'belief', 'assumption', 'presumption',
        'supposition', 'conjecture', 'speculation', 'guess', 'estimate', 'approximation',
        'prediction', 'forecast', 'projection', 'expectation', 'hope', 'wish',
        'desire', 'want', 'need', 'requirement', 'demand', 'request', 'order',
        'command', 'instruction', 'direction', 'guidance', 'advice', 'suggestion',
        'recommendation', 'proposal', 'offer', 'application', 'use',
        'usage', 'utilization', 'employment', 'operation', 'function', 'role',
        'job', 'task', 'duty', 'responsibility', 'obligation', 'commitment',
        'engagement', 'involvement', 'participation', 'contribution', 'input',
        'output', 'result', 'outcome', 'consequence', 'effect', 'impact', 'influence',
        'implication', 'significance', 'importance', 'relevance', 'pertinence',
        'relation', 'relationship', 'connection', 'link', 'tie', 'bond', 'association',
        'correlation', 'correspondence', 'match', 'mismatch', 'difference',
        'distinction', 'discrimination', 'contrast', 'comparison',
        'analogy', 'similarity', 'dissimilarity', 'agreement', 'disagreement',
        'consensus', 'conflict', 'dispute', 'debate', 'discussion', 'conversation',
        'dialogue', 'talk', 'speech', 'lecture', 'presentation', 'report', 'paper',
        'article', 'essay', 'thesis', 'dissertation', 'manuscript', 'draft',
        'version', 'edition', 'copy', 'original', 'reproduction', 'replica', 'duplicate',
        'clone', 'imitation', 'simulation', 'emulation', 'representation', 'depiction',
        'description', 'account', 'narrative', 'story', 'tale', 'history', 'record',
        'document', 'file', 'data', 'information', 'knowledge', 'wisdom', 'understanding',
        'comprehension', 'perception', 'awareness', 'consciousness', 'recognition',
        'identification', 'classification', 'categorization', 'grouping', 'sorting',
        'ordering', 'arrangement', 'organization', 'structure', 'construction',
        'composition', 'formation', 'creation', 'production', 'generation', 'birth',
        'cause', 'reason', 'motive', 'motivation', 'incentive',
        'stimulus', 'trigger', 'prompt', 'cue', 'signal', 'sign', 'indication',
        'evidence', 'proof', 'verification', 'validation', 'confirmation',
        'demonstration', 'show', 'display', 'exhibition', 'manifestation', 'expression',
        'statement', 'declaration', 'announcement', 'proclamation', 'pronouncement',
        'assertion', 'claim', 'allegation', 'accusation', 'charge', 'complaint',
        'criticism', 'critique', 'review', 'evaluation', 'assessment', 'appraisal',
        'analysis', 'examination', 'inspection', 'investigation', 'study', 'research',
        'inquiry', 'query', 'question', 'interrogation', 'survey', 'poll', 'test',
        'trial', 'experiment', 'observation', 'measurement', 'quantification',
        'estimation', 'calculation', 'computation', 'determination', 'derivation',
        'extraction', 'isolation', 'separation', 'purification', 'refinement',
        'enrichment', 'enhancement', 'improvement', 'betterment', 'upgrade',
        'update', 'revision', 'modification', 'alteration', 'change', 'variation',
        'fluctuation', 'oscillation', 'vibration', 'wave', 'perturbation', 'disturbance',
        'disruption', 'interruption', 'break', 'pause', 'stop', 'halt', 'cease',
        'terminate', 'end', 'finish', 'complete', 'accomplish', 'achieve', 'attain',
        'reach', 'arrive', 'come', 'go', 'move', 'travel', 'journey', 'trip', 'tour',
        'visit', 'stay', 'remain', 'reside', 'live', 'dwell', 'exist', 'be', 'become',
        'seem', 'appear', 'look', 'sound', 'feel', 'taste', 'smell', 'sense', 'perceive',
        'detect', 'discover', 'find', 'locate', 'place', 'position', 'site', 'spot',
        'point', 'location', 'locale', 'venue', 'place', 'area', 'region', 'zone',
        'sector', 'district', 'neighborhood', 'vicinity', 'proximity', 'surroundings',
        'environment', 'setting', 'context', 'background', 'backdrop', 'scene', 'stage',
        'platform', 'base', 'foundation', 'ground', 'floor', 'level', 'layer', 'stratum',
        'tier', 'rank', 'grade', 'class', 'category', 'type', 'kind', 'sort', 'variety',
        'form', 'shape', 'figure', 'pattern', 'design', 'structure', 'architecture',
        'configuration', 'layout', 'arrangement', 'disposition', 'distribution',
        'allocation', 'assignment', 'allotment', 'apportionment', 'division', 'partition',
        'segment', 'section', 'part', 'piece', 'portion', 'share', 'fraction', 'fragment',
        'bit', 'particle', 'speck', 'grain', 'drop', 'drip', 'trickle', 'stream', 'flow',
        'current', 'torrent', 'flood', 'deluge', 'inundation', 'overflow', 'spill',
        'leak', 'seep', 'ooze', 'exude', 'emit', 'release', 'discharge', 'eject',
        'expel', 'propel', 'project', 'throw', 'toss', 'cast', 'fling', 'hurl',
        'launch', 'fire', 'shoot', 'blast', 'explode', 'detonate', 'ignite', 'burn',
        'combust', 'incinerate', 'scorch', 'char', 'sear', 'singe', 'roast', 'toast',
        'bake', 'fry', 'boil', 'simmer', 'stew', 'steam', 'poach', 'braise', 'grill',
        'barbecue', 'smoke', 'cure', 'preserve', 'conserve', 'maintain', 'sustain',
        'support', 'uphold', 'bolster', 'reinforce', 'strengthen', 'fortify', 'harden',
        'toughen', 'anneal', 'temper', 'quench', 'cool', 'chill', 'freeze', 'thaw',
        'melt', 'fuse', 'weld', 'solder', 'braze', 'glue', 'adhere', 'stick', 'attach',
        'fasten', 'secure', 'anchor', 'moor', 'dock', 'berth', 'park', 'land', 'ground',
        'earth', 'soil', 'dirt', 'mud', 'clay', 'silt', 'sand', 'gravel', 'pebble',
        'stone', 'rock', 'boulder', 'crag',
        'cliff', 'precipice', 'bluff', 'escarpment', 'scarp', 'slope', 'incline',
        'gradient', 'grade', 'pitch', 'slant', 'tilt', 'tip', 'lean', 'list', 'heel',
        'cant', 'skew', 'oblique', 'diagonal', 'cross', 'transverse', 'athwart',
        'across', 'over', 'through', 'throughout', 'all', 'entire', 'complete', 'full',
        'total', 'whole', 'integral', 'intact', 'undamaged', 'unbroken', 'uninjured',
        'unhurt', 'unharmed', 'safe', 'secure', 'protected', 'guarded', 'shielded',
        'screened', 'sheltered', 'covered', 'hidden', 'concealed', 'secret', 'private',
        'personal', 'individual', 'particular', 'specific', 'special', 'especial',
        'unique', 'distinctive', 'characteristic', 'typical', 'representative',
        'exemplary', 'model', 'ideal', 'perfect', 'flawless', 'impeccable', 'faultless',
        'errorless', 'correct', 'accurate', 'precise', 'exact', 'strict', 'rigid',
        'inflexible', 'unyielding', 'unbending', 'stiff', 'hard', 'firm', 'solid',
        'rigid', 'stiff', 'taut', 'tense', 'tight', 'strained', 'stressed', 'pressured',
        'compressed', 'squeezed', 'crushed', 'flattened', 'squashed', 'smashed',
        'shattered', 'broken', 'fractured', 'cracked', 'split', 'ruptured', 'burst',
        'exploded', 'blown', 'detonated', 'ignited', 'fired', 'lit', 'lighted',
        'illuminated', 'bright', 'brilliant', 'radiant', 'luminous', 'shining',
        'glowing', 'gleaming', 'glistening', 'glittering', 'sparkling', 'twinkling',
        'flickering', 'flashing', 'blinking', 'winking', 'squinting', 'peering',
        'gazing', 'staring', 'looking', 'watching', 'observing', 'viewing', 'seeing',
        'witnessing', 'perceiving', 'noticing', 'noting', 'marking', 'spotting',
        'catching', 'snatching', 'grabbing', 'seizing', 'grasping', 'clutching',
        'clenching', 'gripping', 'holding', 'keeping', 'retaining', 'maintaining',
        'preserving', 'conserving', 'saving', 'sparing', 'economizing', 'scrimping',
        'skimping', 'pinching', 'tightening', 'narrowing', 'constricting', 'contracting',
        'shrinking', 'compressing', 'condensing', 'concentrating', 'focusing',
        'focusing', 'centering', 'aiming', 'pointing', 'directing', 'guiding', 'leading',
        'steering', 'piloting', 'navigating', 'driving', 'riding', 'sailing', 'flying',
        'floating', 'drifting', 'gliding', 'soaring', 'hovering', 'hanging', 'suspending',
        'dangling', 'swinging', 'rocking', 'rolling', 'pitching', 'yawing', 'heaving',
        'surging', 'swelling', 'rising', 'ascending', 'climbing', 'mounting', 'scaling',
        'surmounting', 'overcoming', 'conquering', 'defeating', 'vanquishing',
        'subduing', 'subjugating', 'dominating', 'mastering', 'controlling', 'managing',
        'handling', 'wielding', 'manipulating', 'operating', 'working', 'functioning',
        'running', 'operating', 'performing', 'executing', 'implementing', 'enforcing',
        'administering', 'managing', 'supervising', 'overseeing', 'monitoring',
        'watching', 'observing', 'surveilling', 'patrolling', 'guarding', 'protecting',
        'defending', 'shielding', 'screening', 'covering', 'hiding', 'concealing',
        'masking', 'disguising', 'camouflaging', 'cloaking', 'veiling', 'shrouding',
        'wrapping', 'enveloping', 'surrounding', 'encircling', 'enclosing', 'encompassing',
        'containing', 'including', 'incorporating', 'embodying', 'comprehending',
        'understanding', 'grasping', 'apprehending', 'realizing', 'recognizing',
        'identifying', 'diagnosing', 'determining', 'deciding', 'resolving', 'settling',
        'fixing', 'repairing', 'mending', 'patching', 'restoring', 'renovating',
        'refurbishing', 'renewing', 'refreshing', 'reviving', 'resuscitating',
        'revitalizing', 'rejuvenating', 'regenerating', 'recreating', 'reconstructing',
        'rebuilding', 'remodeling', 'refashioning', 'reshaping', 'reforming',
        'transforming', 'transmuting', 'transfiguring', 'metamorphosing', 'converting',
        'changing', 'altering', 'varying', 'modifying', 'adjusting', 'adapting',
        'accommodating', 'fitting', 'suiting', 'matching', 'corresponding', 'agreeing',
        'accord', 'harmony', 'concord', 'unison', 'unity', 'uniformity', 'consistency',
        'coherence', 'cohesion', 'adhesion', 'bond', 'link', 'tie', 'connection',
        'junction', 'join', 'joint', 'seam', 'suture', 'stitch', 'tack', 'nail',
        'screw', 'bolt', 'rivet', 'pin', 'peg', 'dowel', 'nail', 'spike', 'stake',
        'post', 'pole', 'pillar', 'column', 'shaft', 'beam', 'girder', 'joist', 'rafter',
        'stud', 'strut', 'brace', 'prop', 'stay', 'support', 'shore', 'brace',
        'bracket', 'console', 'corbel', 'cantilever', 'overhang', 'projection',
        'extension', 'annex', 'appendix', 'addition', 'supplement', 'attachment',
        'accessory', 'adjunct', 'appurtenance', 'appendage', 'limb', 'member', 'part',
        'portion', 'share', 'section', 'segment', 'division', 'subdivision', 'branch',
        'offshoot', 'twig', 'sprig', 'shoot', 'scion', 'graft', 'bud', 'blossom',
        'flower', 'bloom', 'petal', 'sepal', 'calyx', 'corolla', 'stamen', 'pistil',
        'ovary', 'style', 'stigma', 'anther', 'filament', 'pollen', 'nectar', 'honey',
        'sap', 'juice', 'latex', 'gum', 'resin', 'amber', 'pitch', 'tar', 'bitumen',
        'asphalt', 'cement', 'concrete', 'mortar', 'plaster', 'stucco', 'lath',
        'shingle', 'shake', 'tile', 'slate', 'shingle', 'panel', 'board', 'plank',
        'slab', 'sheet', 'plate', 'pane', 'panel', 'fencing', 'railing', 'balustrade',
        'banister', 'handrail', 'guardrail', 'barrier', 'fence', 'wall', 'hedge',
        'ditch', 'moat', 'trench', 'channel', 'canal', 'aqueduct', 'conduit', 'pipeline',
        'tube', 'pipe', 'hose', 'cable', 'wire', 'line', 'cord', 'rope', 'chain',
        'link', 'bond', 'tie', 'knot', 'splice', 'joint', 'junction', 'union',
        'fusion', 'blend', 'mixture', 'compound', 'composite', 'alloy', 'amalgam',
        'solution', 'suspension', 'emulsion', 'dispersion', 'colloid', 'gel', 'foam',
        'froth', 'scum', 'slag', 'dross', 'cinder', 'ash', 'soot', 'smoke', 'fume',
        'vapor', 'steam', 'mist', 'fog', 'cloud', 'haze', 'smog', 'dust', 'sand',
        'grit', 'dirt', 'soil', 'earth', 'mud', 'clay', 'silt', 'sludge', 'muck',
        'ooze', 'sediment', 'deposit', 'precipitate', 'residue', 'remainder',
        'remnant', 'relic', 'remains', 'relics', 'ruins', 'debris', 'wreckage',
        'rubble', 'remains', 'ashes', 'embers', 'cinders', 'slag', 'dross', 'scoria',
        'clinker', 'slag', 'cinder', 'ash', 'soot', 'char', 'coal', 'coke', 'carbon',
        'graphite', 'diamond', 'gem', 'jewel', 'stone', 'rock', 'mineral', 'crystal',
        'crystal', 'grain', 'granule', 'pellet', 'bead', 'pearl', 'drop', 'blob',
        'glob', 'globule', 'spheroid', 'ovoid', 'ellipsoid', 'cylinder', 'cone',
        'pyramid', 'prism', 'cube', 'block', 'brick', 'ingot', 'bar', 'rod', 'stick',
        'staff', 'pole', 'stake', 'post', 'pillar', 'column', 'monolith', 'obelisk',
        'statue', 'sculpture', 'figure', 'image', 'icon', 'idol', 'effigy', 'likeness',
        'portrait', 'picture', 'painting', 'drawing', 'sketch', 'draft', 'design',
        'plan', 'scheme', 'blueprint', 'map', 'chart', 'diagram', 'graph', 'plot',
        'table', 'matrix', 'grid', 'lattice', 'network', 'web', 'mesh', 'net',
        'tissue', 'fabric', 'cloth', 'textile', 'material', 'stuff', 'substance',
        'matter', 'material', 'corporeal', 'physical', 'bodily', 'tangible', 'palpable',
        'concrete', 'solid', 'firm', 'hard', 'rigid', 'stiff', 'inflexible', 'unyielding',
        'elastic', 'flexible', 'pliable', 'pliant', 'supple', 'limber', 'lissome',
        'lithe', 'loose', 'slack', 'lax', 'relaxed', 'calm', 'tranquil', 'serene',
        'placid', 'peaceful', 'quiet', 'still', 'silent', 'hushed', 'muffled',
        'muted', 'dampened', 'suppressed', 'repressed', 'restrained', 'constrained',
        'restricted', 'limited', 'confined', 'bounded', 'circumscribed', 'defined',
        'determined', 'fixed', 'set', 'established', 'settled', 'decided', 'resolved',
        'determined', 'concluded', 'finished', 'completed', 'done', 'ended', 'over',
        'through', 'past', 'gone', 'by', 'at', 'on', 'in', 'to', 'of', 'for', 'from',
        'up', 'down', 'out', 'off', 'over', 'under', 'again', 'further', 'then', 'once',
        'here', 'there', 'when', 'where', 'why', 'how', 'all', 'any', 'both', 'each',
        'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only',
        'own', 'same', 'so', 'than', 'too', 'very', 'just', 'but', 'if', 'or', 'because',
        'as', 'until', 'while', 'what', 'which', 'who', 'whom', 'this', 'that',
        'these', 'those', 'am', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'shall', 'should',
        'may', 'might', 'can', 'could', 'must', 'ought', 'need', 'dare', 'used', 'ii',
        'iii', 'iv', 'vi', 'vii', 'viii', 'ix', 'x', 'xi', 'xii', 'fig', 'eq', 'eqs',
        'sec', 'tab', 'ref', 'refs', 'et', 'al', 'et al', 'eg', 'ie', 'cf', 'vs',
        'viz', 'ibid', 'op', 'cit', 'loc', 'cit', 'passim', 'circa', 'ca', 'c',
        'approx', 'approx', 'about', 'around', 'roughly', 'nearly', 'almost', 'ca',
        'ibid', 'idem', 'eod', 'qed', 'qef', 'viz', 'sc', 'inter', 'alia', 'sensu',
        'lato', 'stricto', 'sic', 'pace', 're', 'in', 'situ', 'ex', 'ante', 'post',
        'hoc', 'modo', 'ergo', 'ipso', 'facto', 'prima', 'facie', 'de', 'facto',
        'jure', 'novo', 'novo', 'novo', 'parte', 'contact', 'article', 'paper',
        'study', 'studies', 'analysis', 'analyses', 'result', 'results', 'discussion',
        'conclusion', 'conclusions', 'introduction', 'background', 'abstract', 'summary',
        'overview', 'review', 'literature', 'method', 'methods', 'data', 'observation',
        'observations', 'measurement', 'measurements', 'sample', 'samples', 'source',
        'sources', 'table', 'tables', 'figure', 'figures', 'appendix', 'appendices',
        'supplementary', 'material', 'materials', 'note', 'notes', 'footnote',
        'footnotes', 'reference', 'references', 'acknowledgment', 'acknowledgments',
        'funding', 'support', 'grant', 'grants', 'author', 'authors', 'corresponding',
        'affiliation', 'affiliations', 'email', 'emails', 'address', 'addresses',
        'university', 'universities', 'institute', 'institutes', 'institution',
        'institutions', 'department', 'departments', 'laboratory', 'laboratories',
        'lab', 'labs', 'observatory', 'observatories', 'telescope', 'telescopes',
        'instrument', 'instruments', 'camera', 'cameras', 'detector', 'detectors',
        'survey', 'surveys', 'mission', 'missions', 'project', 'projects', 'program',
        'programs', 'collaboration', 'collaborations', 'team', 'teams', 'group',
        'groups', 'consortium', 'consortia', 'archive', 'archives', 'database',
        'databases', 'repository', 'repositories', 'server', 'servers', 'website',
        'websites', 'web', 'online', 'internet', 'url', 'doi', 'arxiv', 'arxiv',
    }
    
    def is_clean(f):
        if len(f) < 3:
            return False
        if re.fullmatch(r'\d+', f):
            return False
        if re.fullmatch(r'\d+\.\d+', f):
            return False
        tokens = f.split()
        if any(t in noisy_tokens for t in tokens):
            return False
        if re.match(r'^\d', f) or re.search(r'\d$', f):
            return False
        if any(c in f for c in ['^', '_', '{', '}', '\\', '$', '<', '>', '&']):
            return False
        # Filter out pure ambiguous/generic words
        if f in ambiguous_words or f.lower() in ambiguous_words:
            return False
        # Filter out words that are just single letters or Roman numerals
        if re.fullmatch(r'[ivxlcdm]+', f.lower()):
            return False
        # Filter out 3-letter abbreviations that are likely not meaningful
        if len(f) == 3 and f.isalpha() and f.lower() not in {
            'agn', 'bh', 'sfr', 'sed', 'pah', 'lisa', 'jwst', 'sdss', 'ngc', 'uv', 'ir',
            'nir', 'mir', 'fir', 'xrb', 'grb', 'snr', 'ism', 'igm', 'cgm', 'dm', 'de',
            'bhn', 'hst', 'wfc', 'acs', 'nircam', 'miri',
            'nirspec', 'ifu', 'mos', 'slit', 'lsst', 'desi', 'euclid', 'roman', 'wfos',
            'ngrst', 'fov', 'psf', 'fwhm', 'sn', 'snr', 'rms', 'std',
        }:
            return False
        return True
    
    # Model-learned features exclude canonical manual duplicates and isolated
    # components already represented by a custom phrase.
    learned = [(name, coef[i]) for i, name in enumerate(feature_names)
               if is_clean(name)
               and canonical_feature_key(name) not in custom_keys
               and not any(
                   is_contiguous_subphrase(canonical_feature_key(name), custom_key)
                   for custom_key in custom_keys
               )]
    learned.sort(key=lambda x: x[1], reverse=True)

    if stability.get("available"):
        stable_pos = [
            item for item in learned
            if item[1] > 0 and positive_stability.get(item[0], 0) >= stability_threshold
        ]
        stable_neg = [
            item for item in learned
            if item[1] < 0 and negative_stability.get(item[0], 0) >= stability_threshold
        ]
        top_pos = deduplicate_feature_variants(stable_pos)[:positive_auto_limit]
        top_neg = deduplicate_feature_variants(stable_neg[::-1])[:negative_auto_limit]
        stability_summary = (
            f"Filtered at ≥{stability_threshold:.0%} selection frequency across "
            f"{stability.get('n_resamples', 0)} stratified subsamples"
        )
    else:
        top_pos = []
        top_neg = []
        stability_summary = "Unavailable for this model; retrain once to enable stability filtering"

    def stability_cell(scores, name):
        return f"{scores.get(name, 0):.0%}" if stability.get("available") else "—"

    if not stability.get("available"):
        positive_empty_message = "Retrain the model to generate stable positive features."
        negative_empty_message = "Retrain the model to generate stable negative features."
    else:
        positive_empty_message = "All positive feature slots are used by custom keywords."
        negative_empty_message = "All negative feature slots are used by custom keywords."
    
    # Build custom keyword rows
    custom_pos_rows = "\n".join(
        f'<tr><td><code>{html_module.escape(name)}</code></td><td><button class="remove-btn" onclick="removeCustomPos(\'{html_module.escape(name)}\', this)">×</button></td></tr>'
        for name in custom_pos
    ) if custom_pos else '<tr><td colspan="2" style="color:#888;font-style:italic;text-align:center;">No custom positive keywords yet. Add one below.</td></tr>'
    
    custom_neg_rows = "\n".join(
        f'<tr><td><code>{html_module.escape(name)}</code></td><td><button class="remove-btn" onclick="removeCustomNeg(\'{html_module.escape(name)}\', this)">×</button></td></tr>'
        for name in custom_neg
    ) if custom_neg else '<tr><td colspan="2" style="color:#888;font-style:italic;text-align:center;">No custom negative keywords yet. Add one below.</td></tr>'
    
    # Build model-learned rows with remove buttons
    pos_rows = "\n".join(
        f'<tr><td>{i+1}</td><td><code>{html_module.escape(name)}</code></td><td class="coef-pos">{c:+.4f}</td><td>{stability_cell(positive_stability, name)}</td></tr>'
        for i, (name, c) in enumerate(top_pos)
    ) if top_pos else f'<tr><td colspan="4" class="note">{positive_empty_message}</td></tr>'
    neg_rows = "\n".join(
        f'<tr><td>{i+1}</td><td><code>{html_module.escape(name)}</code></td><td class="coef-neg">{c:+.4f}</td><td>{stability_cell(negative_stability, name)}</td></tr>'
        for i, (name, c) in enumerate(top_neg)
    ) if top_neg else f'<tr><td colspan="4" class="note">{negative_empty_message}</td></tr>'
    
    date_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ML Model Features</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333; }}
    h1 {{ color: #1a1a1a; border-bottom: 2px solid #b31b1b; padding-bottom: 10px; }}
    h2 {{ color: #1a1a1a; margin-top: 30px; border-bottom: 1px solid #ddd; padding-bottom: 8px; }}
    h3 {{ color: #444; margin-top: 20px; border-bottom: 1px dashed #ddd; padding-bottom: 6px; font-size: 1.1em; }}
    .info {{ background: #f5f5f5; padding: 16px; border-radius: 8px; margin-bottom: 20px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
    th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; }}
    th {{ background: #fafafa; font-weight: 600; }}
    tr:hover {{ background: #f9f9f9; }}
    .coef-pos {{ color: #2e7d32; font-weight: bold; }}
    .coef-neg {{ color: #c62828; font-weight: bold; }}
    code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 4px; font-family: monospace; }}
    .nav {{ margin-bottom: 20px; display: flex; gap: 16px; }}
    .nav a {{ color: #b31b1b; text-decoration: none; font-weight: bold; }}
    .nav a:hover {{ text-decoration: underline; }}
    .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
    @media (max-width: 700px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
    .remove-btn {{ background: #c62828; color: white; border: none; border-radius: 4px; padding: 2px 8px; cursor: pointer; font-size: 0.85em; line-height: 1; }}
    .remove-btn:hover {{ background: #b71c1c; }}
    .add-btn {{ background: #2e7d32; color: white; border: none; border-radius: 4px; padding: 4px 12px; cursor: pointer; font-size: 0.9em; font-weight: bold; }}
    .add-btn:hover {{ background: #1b5e20; }}
    .custom-input {{ padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; font-size: 0.9em; width: 60%; }}
    .add-row {{ display: flex; gap: 8px; margin-top: 8px; }}
    .regenerate-btn {{ background: #b31b1b; color: white; border: none; border-radius: 6px; padding: 8px 16px; cursor: pointer; font-size: 1em; font-weight: bold; margin-top: 12px; }}
    .regenerate-btn:hover {{ background: #8e1b1b; }}
    .train-btn {{ background: #2e7d32; color: white; border: none; border-radius: 6px; padding: 8px 16px; cursor: pointer; font-size: 1em; font-weight: bold; margin: 12px 0 0 8px; }}
    .train-btn:hover {{ background: #1b5e20; }}
    .msg {{ padding: 8px 12px; border-radius: 4px; margin-top: 8px; display: none; }}
    .msg.success {{ background: #e8f5e9; color: #2e7d32; }}
    .msg.error {{ background: #ffebee; color: #c62828; }}
    .custom-section {{ background: #e8f5e9; padding: 12px; border-radius: 8px; margin-bottom: 16px; }}
    .custom-section.neg {{ background: #ffebee; }}
    .note {{ font-size: 0.85em; color: #666; margin-bottom: 8px; }}
  </style>
</head>
<body>
  <div class="nav">
    <a href="/daily.html">← Daily Papers</a>
    <a href="/recent.html">📅 Recent Papers</a>
    <a href="/search-arxiv.html">🔍 Search arXiv</a>
    <a href="/chat.html">💬 Chat</a>
    <a href="/database.html">📂 Saved Papers</a>
    <a href="/publications.html">📚 My Publications</a>
  </div>
  <h1>ML Model Features</h1>
  <div class="info">
    <p><strong>Model:</strong> TF-IDF + Logistic Regression</p>
    <p><strong>Training data:</strong> {n_pos} saved papers (positive) vs. random recent astro-ph papers (negative)</p>
    <p><strong>Feature space:</strong> {len(feature_names):,} dimensions (unigrams + bigrams)</p>
    <p><strong>Generated:</strong> {date_str}</p>
    <p><strong>Custom Positive:</strong> {len(custom_pos)} keywords</p>
    <p><strong>Custom Negative:</strong> {len(custom_neg)} keywords</p>
    <p><strong>Display budget:</strong> {FEATURE_DISPLAY_LIMIT} positive and {FEATURE_DISPLAY_LIMIT} negative keywords total; custom keywords use slots first</p>
    <p><strong>Feature stability:</strong> {stability_summary}</p>
    <p style="color:#666;font-size:0.9em;">These are stable terms learned from your saved papers. Positive weights → relevant to you. Negative weights → not relevant. Manual keywords adjust ranking immediately without retraining.</p>
    <button class="regenerate-btn" onclick="regenerate()">🔄 Regenerate Page</button>
    <button class="train-btn" onclick="trainModel()">🧠 Train Model Now</button>
    <p id="training-status" class="note" style="margin-top:8px;"></p>
    <div id="msg" class="msg"></div>
  </div>
  
  <div class="two-col">
    <div>
      <div class="custom-section">
        <h3>✏️ Your Custom Positive Keywords</h3>
        <p class="note">Each matching keyword increases a paper's model log-odds. Add terms that should make papers rank higher.</p>
        <table>
          <tbody>
{custom_pos_rows}
          </tbody>
        </table>
        <div class="add-row">
          <input class="custom-input" id="add-pos-input" type="text" placeholder="Add a positive keyword..." onkeydown="if(event.key==='Enter')addCustomPosFromInput()">
          <button class="add-btn" onclick="addCustomPosFromInput()">Add</button>
        </div>
      </div>
      
      <h3>Model-learned Positive Features</h3>
      <table>
        <thead><tr><th>#</th><th>Feature</th><th>Weight</th><th>Stability</th></tr></thead>
        <tbody>
{pos_rows}
        </tbody>
      </table>
    </div>
    <div>
      <div class="custom-section neg">
        <h3>✏️ Your Custom Negative Keywords</h3>
        <p class="note">Each matching keyword decreases a paper's model log-odds. Add a learned positive term here when it is not useful to you. Positive wins if a term appears in both lists.</p>
        <table>
          <tbody>
{custom_neg_rows}
          </tbody>
        </table>
        <div class="add-row">
          <input class="custom-input" id="add-neg-input" type="text" placeholder="Add a negative keyword..." onkeydown="if(event.key==='Enter')addCustomNegFromInput()">
          <button class="add-btn" onclick="addCustomNegFromInput()">Add</button>
        </div>
      </div>
      
      <h3>Model-learned Negative Features</h3>
      <table>
        <thead><tr><th>#</th><th>Feature</th><th>Weight</th><th>Stability</th></tr></thead>
        <tbody>
{neg_rows}
        </tbody>
      </table>
    </div>
  </div>
  
  <script>
    async function showMsg(text, isError) {{
      const el = document.getElementById('msg');
      el.textContent = text;
      el.className = 'msg ' + (isError ? 'error' : 'success');
      el.style.display = 'block';
      setTimeout(() => el.style.display = 'none', 3000);
    }}

    async function regenerateAndReload() {{
      const resp = await fetch('/api/regenerate-features', {{method: 'POST'}});
      const data = await resp.json();
      if (!data.success) throw new Error(data.error || 'Failed to regenerate feature page');
      location.reload();
    }}

    async function removeCustomPos(name, btn) {{
      try {{
        const resp = await fetch('/api/custom-positive/remove', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{keyword: name}})
        }});
        const data = await resp.json();
        if (data.success) {{
          showMsg('Removed custom positive: ' + name);
          await regenerateAndReload();
        }} else {{
          showMsg(data.error || 'Failed', true);
        }}
      }} catch (e) {{
        showMsg('Error: ' + e.message, true);
      }}
    }}

    async function removeCustomNeg(name, btn) {{
      try {{
        const resp = await fetch('/api/custom-negative/remove', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{keyword: name}})
        }});
        const data = await resp.json();
        if (data.success) {{
          showMsg('Removed custom negative: ' + name);
          await regenerateAndReload();
        }} else {{
          showMsg(data.error || 'Failed', true);
        }}
      }} catch (e) {{
        showMsg('Error: ' + e.message, true);
      }}
    }}

    async function addCustomPosFromInput() {{
      const input = document.getElementById('add-pos-input');
      const name = input.value.trim().toLowerCase();
      if (!name) return;
      try {{
        const resp = await fetch('/api/custom-positive', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{keyword: name}})
        }});
        const data = await resp.json();
        if (data.success) {{
          showMsg('Added custom positive: ' + name);
          input.value = '';
          await regenerateAndReload();
        }} else {{
          showMsg(data.error || 'Failed', true);
        }}
      }} catch (e) {{
        showMsg('Error: ' + e.message, true);
      }}
    }}

    async function addCustomNegFromInput() {{
      const input = document.getElementById('add-neg-input');
      const name = input.value.trim().toLowerCase();
      if (!name) return;
      try {{
        const resp = await fetch('/api/custom-negative', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{keyword: name}})
        }});
        const data = await resp.json();
        if (data.success) {{
          showMsg('Added custom negative: ' + name);
          input.value = '';
          await regenerateAndReload();
        }} else {{
          showMsg(data.error || 'Failed', true);
        }}
      }} catch (e) {{
        showMsg('Error: ' + e.message, true);
      }}
    }}

    async function regenerate() {{
      try {{
        const btn = document.querySelector('.regenerate-btn');
        btn.textContent = '⏳ Regenerating...';
        btn.disabled = true;
        const resp = await fetch('/api/regenerate-features', {{method: 'POST'}});
        const data = await resp.json();
        if (data.success) {{
          showMsg('Page regenerated! Reloading...');
          setTimeout(() => location.reload(), 1000);
        }} else {{
          showMsg(data.error || 'Failed', true);
          btn.textContent = '🔄 Regenerate Page';
          btn.disabled = false;
        }}
      }} catch (e) {{
        showMsg('Error: ' + e.message, true);
        document.querySelector('.regenerate-btn').textContent = '🔄 Regenerate Page';
        document.querySelector('.regenerate-btn').disabled = false;
      }}
    }}

    async function refreshTrainingStatus() {{
      try {{
        const resp = await fetch('/api/ml-retraining');
        const state = await resp.json();
        const status = state.training ? 'Training is running…' : 'Training is idle.';
        const progress = `${{state.changes_since_training}} / ${{state.retrain_after_changes}} saved-paper changes`;
        document.getElementById('training-status').textContent = status + ' ' + progress;
        document.querySelector('.train-btn').disabled = state.training;
      }} catch (e) {{
        document.getElementById('training-status').textContent = 'Training status unavailable.';
      }}
    }}

    async function trainModel() {{
      const btn = document.querySelector('.train-btn');
      btn.disabled = true;
      try {{
        const resp = await fetch('/api/ml-retraining/train', {{method: 'POST'}});
        const responseText = await resp.text();
        let data;
        try {{
          data = JSON.parse(responseText);
        }} catch (_) {{
          if (resp.status === 404) {{
            throw new Error('The running ArXistant server is outdated. Restart the server and try again.');
          }}
          throw new Error(responseText.trim() || `Server returned ${{resp.status}}`);
        }}
        if (!data.success) throw new Error(data.error || 'Failed to start training');
        showMsg(data.started ? 'Model training started in the background.' : 'Model training is already running.');
        await refreshTrainingStatus();
      }} catch (e) {{
        showMsg('Error: ' + e.message, true);
        btn.disabled = false;
      }}
    }}

    refreshTrainingStatus();
    setInterval(refreshTrainingStatus, 5000);
  </script>
</body>
</html>"""
    
    if output_path is None:
        output_path = os.path.join(PROJECT_ROOT, "local", "ml_features.html")
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"Features HTML saved to {output_path}")
    return True


# =============================================================================
# CLI
# =============================================================================

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='ML-based arXiv paper ranking')
    parser.add_argument('command', choices=['train', 'score', 'features'], help='Command to run')
    parser.add_argument('input_file', nargs='?', help='JSON file for scoring (required for score)')
    args = parser.parse_args()
    
    if args.command == 'train':
        train_model()
    elif args.command == 'features':
        generate_features_html()
    elif args.command == 'score':
        if not args.input_file:
            print("ERROR: input_file required for score command")
            parser.print_help()
            exit(1)
        with open(args.input_file, 'r') as f:
            papers = json.load(f)
        scored = score_papers(papers)
        for prob, paper in scored[:20]:
            print(f"{prob:.3f} | {paper['title'][:80]}...")
