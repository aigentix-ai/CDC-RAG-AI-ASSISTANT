"""
=============================================================================
CONTEXTUAL QUERY REWRITING MODULE
=============================================================================
Transforms vague or conversational follow-up questions (e.g. 'what about for
mutual funds?', 'what is the penalty for it?') into sharp, standalone search
queries using conversation history.
=============================================================================
"""

import re
from typing import List, Dict, Any, Optional

PRONOUN_OR_FOLLOWUP_PATTERNS = [
    r'\b(what about|how about|what of)\b',
    r'\b(for (them|that|it|this|those))\b',
    r'\b(penalty for (it|that|this))\b',
    r'\b(deadline for (it|that|this))\b',
    r'\b(does (this|it|that) apply)\b',
    r'\b(can (they|it|he|she))\b',
    r'\b(why is that)\b',
    r'\b(tell me more( about (that|it))?)\b',
    r'\b(what else)\b',
    r'\b(explain (that|it|more))\b',
    r'\b(how much is (it|the fine|the penalty))\b',
    r'\b(is there any exception)\b'
]

def is_context_dependent_query(query: str) -> bool:
    """Checks whether the query relies on prior turns to make sense."""
    q = query.strip().lower()
    if len(q.split()) <= 4:
        return True
    return any(re.search(pat, q, re.I) for pat in PRONOUN_OR_FOLLOWUP_PATTERNS)


def extract_topic_from_text(text: str) -> str:
    """Extracts the primary regulatory subject from previous conversation turn."""
    clean = re.sub(r'^(what is|what are|how to|can you tell me|show me|explain)\s+', '', text.strip(), flags=re.I)
    clean = clean.rstrip('?!.')
    return clean.strip()


def rewrite_query_for_retrieval(query: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    """
    Rewrites a conversational query into a standalone search query.
    If query is already standalone or no history exists, returns normalized query.
    """
    if not query or not query.strip():
        return ""

    raw_query = query.strip()
    if not history or not is_context_dependent_query(raw_query):
        return raw_query

    # Find the most recent user turn that had substantive content
    last_user_turn = None
    for turn in reversed(history):
        if isinstance(turn, dict) and turn.get("role") == "user":
            txt = str(turn.get("text", "")).strip()
            if txt and txt.lower() != raw_query.lower() and len(txt.split()) >= 3:
                last_user_turn = txt
                break

    if not last_user_turn:
        return raw_query

    # Rule-based contextual fusion (instant, zero network latency)
    prior_topic = extract_topic_from_text(last_user_turn)

    # e.g. "what about for mutual funds?" + "net capital balance requirement for brokers" ->
    # "net capital balance requirement for mutual funds"
    match_what_about = re.search(r'\b(?:what|how)\s+about\s+(?:for\s+)?(.+)', raw_query, re.I)
    if match_what_about and prior_topic:
        new_target = match_what_about.group(1).rstrip('?.! ')
        # Replace entity if possible, or append
        entity_words = ["brokers", "broker", "participants", "participant", "banks", "treasury", "custodians"]
        replaced = False
        for ew in entity_words:
            if ew in prior_topic.lower():
                prior_topic = re.sub(r'\b' + ew + r'\b', new_target, prior_topic, flags=re.I)
                replaced = True
                break
        if replaced:
            return prior_topic
        else:
            return f"{prior_topic} {new_target}"

    # e.g. "what is the penalty for it?" -> "penalty for [prior topic]"
    if re.search(r'\b(penalty|fine|punishment|sanctions?)\b', raw_query, re.I) and prior_topic:
        return f"penalties for {prior_topic}"

    # e.g. "what are the deadlines for it?" -> "deadlines for [prior topic]"
    if re.search(r'\b(deadline|timeframe|due date|submission date)\b', raw_query, re.I) and prior_topic:
        return f"submission deadlines for {prior_topic}"

    # Default fallback: append prior topic keywords to provide strong lexical and vector signal
    return f"{prior_topic} {raw_query}"
