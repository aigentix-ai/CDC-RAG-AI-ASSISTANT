"""
=============================================================================
TYPO CORRECTION & QUERY NORMALIZATION MODULE
=============================================================================
Provides domain-aware spell-checking, fuzzy term mapping, and intent recovery
for CDC Pakistan & SECP regulatory compliance queries.
=============================================================================
"""

import re
import difflib
from typing import Tuple, List, Dict, Any, Optional

# Canonical vocabulary of SECP / CDC regulatory, financial, and conversational terms
REGULATORY_VOCABULARY = [
    # CDC & Depository terms
    "central", "depository", "company", "participant", "participants",
    "securities", "security", "custody", "pledge", "pledging", "transfer", "transfers",
    "unauthorized", "admission", "eligibility", "sub-account", "account", "accounts",
    "clearing", "settlement", "cds", "cdc", "regulations", "regulation",
    "rules", "rule", "procedures", "procedure", "holding", "holdings",
    "freeze", "unfreeze", "withdrawal", "deposit", "sub-accounts",
    
    # SECP & Compliance terms
    "secp", "commission", "directives", "directive", "circular", "circulars",
    "compliance", "penalty", "penalties", "fine", "fines", "violation", "violations",
    "statutory", "deadline", "deadlines", "submission", "requirement", "requirements",
    "obligation", "obligations", "sanctions", "reporting", "disciplinary", "action",
    "appeal", "cancellation", "suspension",
    
    # Financial & Capital terms
    "capital", "adequacy", "balance", "net", "audit", "audited", "financial",
    "statements", "broker", "brokers", "margin", "exposure", "collateral",
    "brokerage", "trec", "ratio",
    
    # Conversational & Meta terms
    "previous", "message", "question", "answer", "earlier", "repeat", "summary",
    "shorter", "email", "bullet", "points", "takeaway", "detail", "details",
    "guidance", "clauses", "advisory"
]

# Explicit high-frequency typing slips & phonetic mappings
COMMON_TYPO_MAP = {
    # Question words & helpers
    "wht": "what", "wat": "what", "waht": "what",
    "hw": "how", "howw": "how",
    "whch": "which", "wich": "which",
    "whn": "when", "wen": "when",
    "shw": "show", "sho": "show",
    "gve": "give", "giv": "give",
    "abt": "about", "aboutt": "about",
    "plz": "please", "pls": "please", "pleas": "please",
    "tel": "tell", "telll": "tell",
    
    # Regulatory keywords
    "pennalty": "penalty", "penalts": "penalties", "penality": "penalty", "penaltys": "penalties", "pnalty": "penalty",
    "submision": "submission", "submisson": "submission", "sumbission": "submission", "submsn": "submission",
    "cpaital": "capital", "capitl": "capital", "captial": "capital", "cptl": "capital", "captl": "capital",
    "adeqcy": "adequacy", "adequcy": "adequacy", "adequecy": "adequacy", "adeqaucy": "adequacy", "adeqcay": "adequacy",
    "particpant": "participant", "particpnt": "participant", "partcipant": "participant", "particiapnt": "participant",
    "particpants": "participants", "partcipants": "participants",
    "depsoitry": "depository", "deposirty": "depository", "depsoitory": "depository", "depostory": "depository",
    "regulatins": "regulations", "regultions": "regulations", "regualtions": "regulations", "regualtion": "regulation", "reglations": "regulations",
    "directves": "directives", "directivs": "directives", "directve": "directive", "diretives": "directives",
    "securitis": "securities", "securites": "securities", "secutities": "securities", "secrities": "securities",
    "accnt": "account", "acount": "account", "accunt": "account", "accts": "accounts", "accnts": "accounts",
    "subaccnt": "sub-account", "sub-accnt": "sub-account", "subacount": "sub-account",
    "custdy": "custody", "cusotdy": "custody", "custdy": "custody",
    "plegde": "pledge", "peldge": "pledge",
    "transfr": "transfer", "transfar": "transfer", "trnsfer": "transfer", "trnfr": "transfer",
    "unauthroized": "unauthorized", "unautherized": "unauthorized", "unauthorisd": "unauthorized", "unauthorizd": "unauthorized",
    "elgiblity": "eligibility", "eligiblty": "eligibility", "elegibility": "eligibility",
    "deadlin": "deadline", "deadilne": "deadline", "dedline": "deadline", "deadln": "deadline", "dedln": "deadline",
    "complience": "compliance", "complianse": "compliance", "complianc": "compliance", "complaince": "compliance",
    "requriment": "requirement", "requirments": "requirements", "requriements": "requirements", "requirment": "requirement",
    "statutary": "statutory", "statutry": "statutory",
    "balence": "balance", "balanc": "balance",
    "circlar": "circular", "circuler": "circular", "cricular": "circular",
    "auditt": "audit", "audt": "audit",
    "verifed": "verified", "verifcation": "verification",
    "brokr": "broker", "brokrs": "brokers",
    "cdss": "cds",
    
    # Conversational meta terms
    "prevoius": "previous", "prevous": "previous", "prvious": "previous", "pervious": "previous", "previuos": "previous",
    "mesage": "message", "messge": "message", "msge": "message", "msg": "message", "mesages": "messages",
    "queston": "question", "qstn": "question", "ques": "question", "questin": "question", "qestion": "question",
    "answr": "answer", "anwer": "answer", "answre": "answer",
    "shorterr": "shorter", "shrt": "shorter", "shortr": "shorter",
    "sumary": "summary", "summarise": "summarize", "sumarize": "summarize",
    "searchengin": "search engine", "searchengine": "search engine"
}

def correct_query_typos(query: str) -> Tuple[str, bool]:
    """
    Analyzes input query for typos, spelling mistakes, and shorthand abbreviations.
    Normalizes recognized typos into canonical regulatory and conversational vocabulary.
    
    Returns:
        (corrected_query: str, corrections_applied: bool)
    """
    if not query or not query.strip():
        return query, False

    tokens = re.findall(r"[\w'-]+|[^\w\s]", query)
    corrected_tokens = []
    has_changed = False

    for token in tokens:
        clean_lower = token.lower().strip()
        
        # 1. Direct dictionary match
        if clean_lower in COMMON_TYPO_MAP:
            replacement = COMMON_TYPO_MAP[clean_lower]
            corrected_tokens.append(replacement)
            has_changed = True
            continue
            
        # 2. Skip very short tokens or punctuation
        if len(clean_lower) < 4 or not clean_lower.isalnum():
            corrected_tokens.append(token)
            continue
            
        # 3. Fuzzy match against domain vocabulary if not already an exact match
        if clean_lower not in REGULATORY_VOCABULARY:
            matches = difflib.get_close_matches(clean_lower, REGULATORY_VOCABULARY, n=1, cutoff=0.78)
            if matches:
                corrected_tokens.append(matches[0])
                has_changed = True
                continue
                
        corrected_tokens.append(token)

    # Reconstruct text spacing cleanly
    reconstructed = ""
    for i, tok in enumerate(corrected_tokens):
        if i > 0 and tok.isalnum() and corrected_tokens[i - 1].isalnum():
            reconstructed += " " + tok
        elif i > 0 and tok in ["?", "!", ".", ",", ":", ";"]:
            reconstructed += tok
        elif i > 0:
            reconstructed += " " + tok
        else:
            reconstructed = tok

    return (reconstructed.strip() if has_changed else query), has_changed


def is_chat_history_inquiry(query: str) -> bool:
    """
    Identifies whether the query is asking about previous messages or chat conversation history
    (e.g., 'what did i ask in last message?', 'what was my previous question?').
    """
    q, _ = correct_query_typos(query.lower().strip())
    patterns = [
        r'\bwhat\s+(did|was)\s+(i|we)\s+(ask|say|discuss|type|write|send)\b',
        r'\bwhat\s+was\s+my\s+(last|previous|prior|first)\s+(question|message|query|prompt|msg)\b',
        r'\bwhat\s+did\s+i\s+just\s+(ask|say|type)\b',
        r'\bwhat\s+was\s+the\s+(last|previous)\s+(question|message|inquiry|topic)\b',
        r'\bwhat\s+(did\s+i|was\s+my)\s+ask\s+(in\s+)?(the\s+)?(last|previous|prior)\s+(message|msg|turn)\b',
        r'\bwhat\s+did\s+i\s+ask\b',
        r'\bwhat\s+was\s+i\s+asking\b',
        r'\bcan\s+you\s+remind\s+me\s+what\s+i\s+(asked|said)\b',
        r'\bwhat\s+(did|were)\s+we\s+(talk|talking|discuss|discussing)\b',
        r'\bwhat\s+have\s+we\s+discussed(\s+so\s+far)?\b',
        r'\brecap\s+(our\s+)?(chat|conversation|discussion)\b'
    ]
    return any(re.search(pat, q, re.I) for pat in patterns)


def is_repeat_inquiry(query: str) -> bool:
    """
    Identifies whether the query is asking the assistant to repeat its previous answer
    (e.g., 'repeat your last answer', 'what did you say?').
    """
    q, _ = correct_query_typos(query.lower().strip())
    patterns = [
        r'\brepeat\s+(your\s+)?(last\s+|previous\s+)?(answer|response|message)\b',
        r'\bwhat\s+did\s+you\s+(just\s+)?(say|answer|reply|state)\b',
        r'\bsay\s+that\s+again\b',
        r'\bwhat\s+was\s+your\s+(last|previous)\s+(answer|response)\b'
    ]
    return any(re.search(pat, q, re.I) for pat in patterns)
