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
import os
import pickle
import re
import sqlite3
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
VECTORIZER_PATH = os.path.join(MODEL_DIR, "vectorizer.pkl")
BLACKLIST_PATH = os.path.join(MODEL_DIR, "feature_blacklist.json")
CUSTOM_POSITIVE_PATH = os.path.join(MODEL_DIR, "custom_positive.json")
CUSTOM_NEGATIVE_PATH = os.path.join(MODEL_DIR, "custom_negative.json")

os.makedirs(MODEL_DIR, exist_ok=True)


# =============================================================================
# BLACKLIST
# =============================================================================

def load_blacklist():
    """Load blacklisted feature names."""
    if not os.path.exists(BLACKLIST_PATH):
        return set()
    with open(BLACKLIST_PATH, 'r', encoding='utf-8') as f:
        return set(json.load(f))


def save_blacklist(blacklist):
    """Save blacklisted feature names."""
    with open(BLACKLIST_PATH, 'w', encoding='utf-8') as f:
        json.dump(sorted(list(blacklist)), f, indent=2)


def add_to_blacklist(feature_name):
    """Add a feature to the blacklist."""
    blacklist = load_blacklist()
    blacklist.add(feature_name)
    save_blacklist(blacklist)
    print(f"Blacklisted feature: {feature_name}")
    return True


def remove_from_blacklist(feature_name):
    """Remove a feature from the blacklist."""
    blacklist = load_blacklist()
    if feature_name in blacklist:
        blacklist.remove(feature_name)
        save_blacklist(blacklist)
        print(f"Removed from blacklist: {feature_name}")
        return True
    return False


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
    Remove from negative any keywords that also exist in positive.
    Positive wins. Returns (positive_list, negative_list).
    """
    pos = load_custom_positive()
    neg = load_custom_negative()
    pos_set = set(pos)
    new_neg = [w for w in neg if w not in pos_set]
    if len(new_neg) != len(neg):
        save_custom_negative(new_neg)
    return pos, new_neg


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
    resp = urllib.request.urlopen(req)
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
    print("Fetching negative examples from recent arXiv...")
    negatives = fetch_random_astroph_papers(max_results=150, days_back=30)
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


def train_model():
    """Train a TF-IDF + Logistic Regression model and save to disk."""
    texts, labels = build_training_data()
    
    if len(labels) == 0 or labels.sum() == 0:
        print("ERROR: No positive training data. Save some papers first.")
        return False
    
    print(f"\nTraining on {len(texts)} samples ({labels.sum()} positive, {len(labels)-labels.sum()} negative)...")
    
    # TF-IDF + Logistic Regression pipeline
    vectorizer = TfidfVectorizer(
        max_features=10000,
        ngram_range=(1, 2),      # unigrams + bigrams
        min_df=2,                # ignore terms that appear in < 2 docs
        max_df=0.95,             # ignore terms that appear in > 95% of docs
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
    
    # Save model and vectorizer
    with open(MODEL_PATH, 'wb') as f:
        pickle.dump(clf, f)
    with open(VECTORIZER_PATH, 'wb') as f:
        pickle.dump(vectorizer, f)
    
    print(f"Model saved to {MODEL_PATH}")
    print(f"Vectorizer saved to {VECTORIZER_PATH}")
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
    probs = clf.predict_proba(X)[:, 1]  # probability of class 1 (positive/saved)
    
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
        probs = clf.predict_proba(X)[:, 1]
        
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
    
    # Get training data counts
    saved = get_saved_papers()
    n_pos = len(saved)
    
    blacklist = load_blacklist()
    custom_pos, custom_neg = deduplicate_custom_keywords()
    custom_pos_set = set(custom_pos)
    custom_neg_set = set(custom_neg)
    
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
    
    # Model-learned features, excluding custom keywords and blacklisted ones
    learned = [(name, coef[i]) for i, name in enumerate(feature_names) 
               if is_clean(name) and name not in blacklist 
               and name not in custom_pos_set and name not in custom_neg_set]
    learned.sort(key=lambda x: x[1], reverse=True)
    
    top_pos = learned[:50]
    top_neg = learned[-30:][::-1]
    
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
        f'<tr><td>{i+1}</td><td><code>{html_module.escape(name)}</code></td><td class="coef-pos">{c:+.4f}</td><td><button class="remove-btn" onclick="removeFeature(\'{html_module.escape(name)}\', this)">×</button></td></tr>'
        for i, (name, c) in enumerate(top_pos)
    )
    neg_rows = "\n".join(
        f'<tr><td>{i+1}</td><td><code>{html_module.escape(name)}</code></td><td class="coef-neg">{c:+.4f}</td><td><button class="remove-btn" onclick="removeFeature(\'{html_module.escape(name)}\', this)">×</button></td></tr>'
        for i, (name, c) in enumerate(top_neg)
    )
    
    # Build blacklisted section
    blacklisted_rows = "\n".join(
        f'<tr><td><code>{html_module.escape(name)}</code></td><td><button class="undo-btn" onclick="restoreFeature(\'{html_module.escape(name)}\')">↺ Restore</button></td></tr>'
        for name in sorted(blacklist)
    ) if blacklist else '<tr><td colspan="2" style="color:#888;font-style:italic;text-align:center;">No features blacklisted yet. Click × on any feature above to remove it.</td></tr>'
    
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
    .undo-btn {{ background: #1976d2; color: white; border: none; border-radius: 4px; padding: 2px 8px; cursor: pointer; font-size: 0.85em; line-height: 1; }}
    .undo-btn:hover {{ background: #1565c0; }}
    .add-btn {{ background: #2e7d32; color: white; border: none; border-radius: 4px; padding: 4px 12px; cursor: pointer; font-size: 0.9em; font-weight: bold; }}
    .add-btn:hover {{ background: #1b5e20; }}
    .custom-input {{ padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; font-size: 0.9em; width: 60%; }}
    .add-row {{ display: flex; gap: 8px; margin-top: 8px; }}
    .blacklisted {{ background: #f0f0f0; padding: 12px; border-radius: 8px; margin-top: 20px; }}
    .blacklisted h2 {{ border-bottom: 1px solid #ccc; margin-top: 0; }}
    .blacklisted table {{ width: 100%; border-collapse: collapse; }}
    .blacklisted td {{ padding: 6px 8px; border-bottom: 1px solid #ddd; }}
    .regenerate-btn {{ background: #b31b1b; color: white; border: none; border-radius: 6px; padding: 8px 16px; cursor: pointer; font-size: 1em; font-weight: bold; margin-top: 12px; }}
    .regenerate-btn:hover {{ background: #8e1b1b; }}
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
    <p><strong>Blacklisted:</strong> {len(blacklist)} features</p>
    <p><strong>Custom Positive:</strong> {len(custom_pos)} keywords</p>
    <p><strong>Custom Negative:</strong> {len(custom_neg)} keywords</p>
    <p style="color:#666;font-size:0.9em;">These are the terms the model learned from your saved papers. Positive weights → relevant to you. Negative weights → not relevant. Click × to remove a feature. Use the input boxes below to add custom keywords.</p>
    <button class="regenerate-btn" onclick="regenerate()">🔄 Regenerate Page</button>
    <div id="msg" class="msg"></div>
  </div>
  
  <div class="two-col">
    <div>
      <div class="custom-section">
        <h3>✏️ Your Custom Positive Keywords</h3>
        <p class="note">These keywords are manually added to the positive list. They take priority over model-learned features.</p>
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
        <thead><tr><th>#</th><th>Feature</th><th>Weight</th><th></th></tr></thead>
        <tbody>
{pos_rows}
        </tbody>
      </table>
    </div>
    <div>
      <div class="custom-section neg">
        <h3>✏️ Your Custom Negative Keywords</h3>
        <p class="note">These keywords are manually added to the negative list. If a keyword also exists in the positive list, the positive one wins.</p>
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
        <thead><tr><th>#</th><th>Feature</th><th>Weight</th><th></th></tr></thead>
        <tbody>
{neg_rows}
        </tbody>
      </table>
    </div>
  </div>
  
  <div class="blacklisted">
    <h2>Blacklisted Features</h2>
    <p style="color:#666;font-size:0.85em;margin-top:0;">These features are excluded from the display. Click ↺ to restore a feature.</p>
    <table>
      <tbody>
{blacklisted_rows}
      </tbody>
    </table>
  </div>

  <script>
    async function showMsg(text, isError) {{
      const el = document.getElementById('msg');
      el.textContent = text;
      el.className = 'msg ' + (isError ? 'error' : 'success');
      el.style.display = 'block';
      setTimeout(() => el.style.display = 'none', 3000);
    }}

    async function removeFeature(name, btn) {{
      if (!confirm('Remove feature "' + name + '" from the display?')) return;
      try {{
        const resp = await fetch('/api/blacklist', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{feature: name}})
        }});
        const data = await resp.json();
        if (data.success) {{
          showMsg('Removed: ' + name);
          const row = btn.closest('tr');
          if (row) row.style.display = 'none';
        }} else {{
          showMsg(data.error || 'Failed', true);
        }}
      }} catch (e) {{
        showMsg('Error: ' + e.message, true);
      }}
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
          const row = btn.closest('tr');
          if (row) row.style.display = 'none';
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
          const row = btn.closest('tr');
          if (row) row.style.display = 'none';
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
          location.reload();
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
          location.reload();
        }} else {{
          showMsg(data.error || 'Failed', true);
        }}
      }} catch (e) {{
        showMsg('Error: ' + e.message, true);
      }}
    }}

    async function restoreFeature(name) {{
      try {{
        const resp = await fetch('/api/blacklist/remove', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{feature: name}})
        }});
        const data = await resp.json();
        if (data.success) {{
          showMsg('Restored: ' + name);
          location.reload();
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
    parser.add_argument('command', choices=['train', 'score'], help='Command to run')
    parser.add_argument('input_file', nargs='?', help='JSON file for scoring (required for score)')
    args = parser.parse_args()
    
    if args.command == 'train':
        train_model()
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
