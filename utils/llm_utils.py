# utils/llm_utils.py  –  LLM wrappers for OpenAI, Google Gemini, and Anthropic Claude

import json
import sys
import time
from typing import List, Dict, Optional


# ─────────────────────────────────────────────────────────────
#  Shared prompt
# ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert at analyzing OCR text extracted from historical Survey of India topographical maps.

Your task is to:
1. CLEAN: Fix OCR errors (e.g. "Rlver" → "River", "Akb4rpur" → "Akbarpur")
2. CLASSIFY: Assign each item a feature_type from: settlement | river | mountain | lake | forest | road | landmark | noise
3. FILTER: Discard items that are clearly not geographic names:
   - Scale labels (e.g. "1:50000", "Miles", "Feet")
   - Contour values (e.g. "200", "500", "1000")
   - Grid numbers/letters alone (e.g. "A", "1", "B2")
   - Compass labels (N, S, E, W)
4. SCORE: Assign a confidence 0.0-1.0 for each cleaned item

Return ONLY a valid JSON array. Each element:
{
  "original": "<raw OCR text>",
  "cleaned":  "<corrected name>",
  "feature_type": "settlement|river|mountain|lake|forest|road|landmark",
  "confidence": 0.0-1.0
}

If an item should be discarded, set "feature_type" to "noise".
Return [] if nothing is geographic. Never add commentary outside the JSON.
"""


def _build_user_message(raw_texts: List[str]) -> str:
    numbered = '\n'.join(f'{i+1}. {t}' for i, t in enumerate(raw_texts))
    return f'Clean and classify these OCR-extracted map labels:\n\n{numbered}'


def _parse_llm_json(response_text: str) -> List[Dict]:
    """Extract JSON array from LLM response, handling markdown code blocks."""
    text = response_text.strip()
    # Strip markdown code fences if present
    if text.startswith('```'):
        lines = text.split('\n')
        text = '\n'.join(lines[1:])
        if text.rstrip().endswith('```'):
            text = text.rstrip()[:-3].strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    return []


# ─────────────────────────────────────────────────────────────
#  OpenAI
# ─────────────────────────────────────────────────────────────

def clean_with_openai(
    raw_texts: List[str],
    api_key: str,
    model: str = 'gpt-4o-mini',
    batch_size: int = 80
) -> List[Dict]:
    """
    Send raw OCR texts to OpenAI for cleaning and classification.
    Processes in batches to stay within token limits.
    """
    try:
        from openai import OpenAI
    except ImportError:
        print('[LLM] openai package not installed. Run: pip install openai')
        return []

    client = OpenAI(api_key=api_key, max_retries=0)  # fail fast on 429
    all_results = []
    rate_limited = False

    for start in range(0, len(raw_texts), batch_size):
        if rate_limited:
            break
        batch = raw_texts[start:start + batch_size]
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user',   'content': _build_user_message(batch)},
                ],
                temperature=0.1,
                max_tokens=4096,
            )
            content = response.choices[0].message.content
            parsed = _parse_llm_json(content)
            all_results.extend(parsed)
            print(f'[LLM/OpenAI] Batch {start//batch_size + 1}: {len(parsed)} features')
        except Exception as e:
            err_str = str(e)
            if '429' in err_str or 'rate' in err_str.lower() or 'quota' in err_str.lower() or 'billing' in err_str.lower():
                print(f'[LLM/OpenAI] Rate-limited / quota exceeded: {e}')
                rate_limited = True
            else:
                print(f'[LLM/OpenAI] Error: {e}')

    return all_results


# ─────────────────────────────────────────────────────────────
#  xAI Grok
# ─────────────────────────────────────────────────────────────

def clean_with_grok(
    raw_texts: List[str],
    api_key: str,
    model: str = 'grok-3-mini',
    batch_size: int = 80
) -> List[Dict]:
    """
    Send raw OCR texts to xAI Grok for cleaning and classification.
    Uses OpenAI-compatible endpoint at api.x.ai.
    """
    try:
        from openai import OpenAI
    except ImportError:
        print('[LLM] openai package not installed. Run: pip install openai')
        return []

    client = OpenAI(
        api_key=api_key,
        base_url='https://api.x.ai/v1',
        max_retries=0,
    )
    all_results = []

    for start in range(0, len(raw_texts), batch_size):
        batch = raw_texts[start:start + batch_size]
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user',   'content': _build_user_message(batch)},
                ],
                temperature=0.1,
                max_tokens=4096,
            )
            content = response.choices[0].message.content
            parsed = _parse_llm_json(content)
            all_results.extend(parsed)
            print(f'[LLM/Grok] Batch {start//batch_size + 1}: {len(parsed)} features')
        except Exception as e:
            err_str = str(e)
            if '401' in err_str or 'invalid' in err_str.lower():
                print(f'[LLM/Grok] Invalid API key: {e}')
                break
            if '429' in err_str or 'quota' in err_str.lower() or 'rate' in err_str.lower():
                print(f'[LLM/Grok] Quota/rate exceeded: {e}')
                raise QuotaExhaustedError(f'Grok quota exhausted: {e}')
            print(f'[LLM/Grok] Error: {e}')

    return all_results


# ─────────────────────────────────────────────────────────────
#  Google Gemini
# ─────────────────────────────────────────────────────────────

class QuotaExhaustedError(Exception):
    """Raised when the LLM API quota/rate-limit is fully exhausted."""


def clean_with_gemini(
    raw_texts: List[str],
    api_key: str,
    model: str = 'gemini-2.5-flash',
    batch_size: int = 80
) -> List[Dict]:
    """
    Send raw OCR texts to Google Gemini for cleaning and classification.
    Raises QuotaExhaustedError if every batch fails due to quota/rate-limit.
    """
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        print('[LLM] google-genai not installed. Run: python -m pip install google-genai')
        return []

    client = genai.Client(api_key=api_key)

    all_results = []
    quota_errors = 0
    total_batches = (len(raw_texts) + batch_size - 1) // batch_size

    for start in range(0, len(raw_texts), batch_size):
        batch = raw_texts[start:start + batch_size]
        batch_num = start // batch_size + 1
        if start > 0:
            time.sleep(4)  # 4s gap → ~15 RPM, stays within Gemini free tier limit

        # Retry loop: up to 3 attempts per batch on rate limit
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=_build_user_message(batch),
                    config=genai_types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.1,
                        max_output_tokens=16384,  # 80 items × ~150 tokens each needs ~12k
                    ),
                )
                # gemini-2.5-flash returns parts; join non-thought text parts
                parts = response.candidates[0].content.parts if response.candidates else []
                content = ''.join(p.text for p in parts if p.text and not p.thought)
                if not content and response.text:
                    content = response.text  # fallback for older models
                parsed = _parse_llm_json(content)
                all_results.extend(parsed)
                print(f'[LLM/Gemini] Batch {batch_num}/{total_batches}: {len(parsed)} features')
                break  # success, move to next batch
            except Exception as e:
                err_str = str(e)
                if '429' in err_str or 'quota' in err_str.lower() or 'rate' in err_str.lower():
                    quota_errors += 1
                    wait = 60 * (attempt + 1)  # 60s, 120s, 180s
                    print(f'[LLM/Gemini] Rate limit on batch {batch_num} (attempt {attempt+1}/3), waiting {wait}s...')
                    time.sleep(wait)
                else:
                    print(f'[LLM/Gemini] Error on batch {batch_num}: {e}')
                    break  # non-rate-limit error, skip batch

    if quota_errors > 0 and not all_results:
        raise QuotaExhaustedError(
            f'Gemini quota exhausted ({quota_errors}/{total_batches} batches failed)'
        )

    return all_results


# ─────────────────────────────────────────────────────────────
#  Anthropic Claude
# ─────────────────────────────────────────────────────────────

def clean_with_claude(
    raw_texts: List[str],
    api_key: str,
    model: str = 'claude-3-5-haiku-20241022',
    batch_size: int = 40
) -> List[Dict]:
    """
    Send raw OCR texts to Anthropic Claude for cleaning and classification.
    Raises QuotaExhaustedError if every batch hits a rate/quota limit.
    Uses smaller batches + retry-with-backoff to respect 10K tokens/min limit.
    """
    try:
        import anthropic
        import time
    except ImportError:
        print('[LLM] anthropic package not installed. Run: pip install anthropic')
        return []

    client = anthropic.Anthropic(api_key=api_key, max_retries=0)
    all_results = []
    quota_errors = 0
    total_batches = (len(raw_texts) + batch_size - 1) // batch_size

    for start in range(0, len(raw_texts), batch_size):
        batch = raw_texts[start:start + batch_size]
        batch_num = start // batch_size + 1
        retries = 0
        while retries <= 2:
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    messages=[
                        {'role': 'user', 'content': _build_user_message(batch)},
                    ],
                    temperature=0.1,
                )
                content = response.content[0].text
                parsed = _parse_llm_json(content)
                all_results.extend(parsed)
                print(f'[LLM/Claude] Batch {batch_num}/{total_batches}: {len(parsed)} features')
                time.sleep(8)  # respect 10K tokens/min rate limit
                break
            except Exception as e:
                err_str = str(e)
                if any(kw in err_str.lower() for kw in ('billing', 'credit', 'insufficient')):
                    quota_errors += 1
                    print(f'[LLM/Claude] Billing error: {e}')
                    raise QuotaExhaustedError(f'Claude billing error: {e}')
                elif '429' in err_str or 'rate' in err_str.lower() or 'overloaded' in err_str.lower():
                    retries += 1
                    wait = 65 * retries
                    print(f'[LLM/Claude] Rate-limited (batch {batch_num}), waiting {wait}s ... (retry {retries}/2)')
                    time.sleep(wait)
                else:
                    quota_errors += 1
                    print(f'[LLM/Claude] Error: {e}')
                    break
        else:
            quota_errors += 1
            print(f'[LLM/Claude] Batch {batch_num} failed after retries, skipping.')

    if quota_errors > 0 and not all_results:
        raise QuotaExhaustedError(
            f'Claude quota exhausted ({quota_errors}/{total_batches} batches failed)'
        )

    return all_results


# ─────────────────────────────────────────────────────────────
#  Groq  (OpenAI-compatible, free tier)
# ─────────────────────────────────────────────────────────────

def clean_with_groq(
    raw_texts: List[str],
    api_key: str,
    model: str = 'llama-3.1-8b-instant',
    batch_size: int = 60
) -> List[Dict]:
    """
    Send raw OCR texts to Groq (free tier) for cleaning and classification.
    Uses the OpenAI-compatible Groq client. Raises QuotaExhaustedError on
    rate-limit / billing failures.
    """
    try:
        from openai import OpenAI
    except ImportError:
        print('[LLM] openai package required for Groq too. Run: pip install openai')
        return []

    client = OpenAI(
        api_key=api_key,
        base_url='https://api.groq.com/openai/v1',
        max_retries=0,
    )
    all_results = []
    quota_errors = 0
    total_batches = (len(raw_texts) + batch_size - 1) // batch_size

    for start in range(0, len(raw_texts), batch_size):
        batch = raw_texts[start:start + batch_size]
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user',   'content': _build_user_message(batch)},
                ],
                temperature=0.1,
                max_tokens=4096,
            )
            content = response.choices[0].message.content
            parsed = _parse_llm_json(content)
            all_results.extend(parsed)
            print(f'[LLM/Groq] Batch {start//batch_size + 1}/{total_batches}: {len(parsed)} features')
        except Exception as e:
            err_str = str(e)
            if '401' in err_str or 'invalid_api_key' in err_str.lower() or 'invalid api key' in err_str.lower():
                print(f'[LLM/Groq] Invalid API key — check GROQ_API_KEY in .env')
                break  # no point retrying other batches
            elif any(kw in err_str.lower() for kw in ('429', 'rate', 'quota', 'billing', 'limit')):
                quota_errors += 1
                print(f'[LLM/Groq] Rate-limited: {e}')
            else:
                print(f'[LLM/Groq] Error: {e}')

    if quota_errors > 0 and not all_results:
        raise QuotaExhaustedError(
            f'Groq quota exhausted ({quota_errors}/{total_batches} batches failed)'
        )

    return all_results


# ─────────────────────────────────────────────────────────────
#  Local rules-based classifier  (no API required)
# ─────────────────────────────────────────────────────────────

import re as _re

# Suffixes / keywords drawn from Survey of India map conventions
_SETTLEMENT_SUFFIXES = (
    'pur', 'pura', 'nagar', 'puram', 'ganj', 'gunj', 'gaon', 'gon', 'ganj',
    'bad', 'abad', 'garh', 'kot', 'kota', 'wara', 'wali', 'ward',
    'tola', 'toli', 'khurd', 'kalan', 'buzurg', 'dihi', 'dih',
    'bazar', 'basti', 'mohalla', 'patti', 'khera', 'kheri',
)
_RIVER_KEYWORDS = (
    r'\bnadi\b', r'\bnala\b', r'\bnallah\b', r'\bnullah\b',
    r'\briver\b', r'\br\.\b', r'\bstream\b', r'\bkhad\b',
    r'\bjharna\b', r'\btorrent\b', r'\bsone\b', r'\bkoel\b',
    r'\bnorth koel\b', r'\bsouth koel\b',
)
_LAKE_KEYWORDS = (
    r'\blake\b', r'\bjheel\b', r'\bjhil\b', r'\btal\b', r'\btank\b',
    r'\bsagar\b', r'\bsamudra\b', r'\bDAM\b', r'\breservoir\b',
)
_MOUNTAIN_KEYWORDS = (
    r'\bhill\b', r'\bhills\b', r'\bpeak\b', r'\bmt\.\b', r'\bmount\b',
    r'\bpahar\b', r'\bpahad\b', r'\bghati\b', r'\bghat\b', r'\brange\b',
    r'\bridge\b', r'\bescarpment\b',
)
_FOREST_KEYWORDS = (
    r'\bforest\b', r'\brf\b', r'\breserved\b', r'\bjungle\b', r'\bvan\b',
    r'\bwildlife\b', r'\bsanctuary\b',
)
_ROAD_KEYWORDS = (
    r'\broad\b', r'\bnh\b', r'\bsh\b', r'\btrack\b', r'\bpath\b',
    r'\brailway\b', r'\bstation\b', r'\bjunction\b',
)
# Patterns that are almost certainly noise on topo maps
_NOISE_PATTERNS = [
    r'^\d+$',                         # pure numbers (contour values)
    r'^\d+\.\d+$',                    # decimals
    r'^[A-Z]$',                       # single capital letter
    r'^[A-Z]\d+$',                    # grid refs like A1, B3
    r'^\d+[A-Z]$',
    r'^(N|S|E|W|NE|NW|SE|SW)$',       # compass
    r'\d+:\d+',                       # scale e.g. 1:50000
    r'^(miles?|feet?|km|metres?|ft|m)$',
    r'^[\W_]+$',                      # only punctuation/whitespace
]


def _classify_local(text: str) -> tuple:
    """Return (feature_type, confidence, cleaned_text) using rule matching."""
    low = text.lower().strip()
    cleaned = text.strip()

    # --- noise check first ---
    for pat in _NOISE_PATTERNS:
        if _re.match(pat, low, _re.IGNORECASE):
            return 'noise', 0.95, cleaned

    # too short to be a place name (single char / number)
    if len(low) <= 1:
        return 'noise', 0.9, cleaned

    # --- keyword matching ---
    for pat in _RIVER_KEYWORDS:
        if _re.search(pat, low, _re.IGNORECASE):
            return 'river', 0.80, cleaned
    for pat in _LAKE_KEYWORDS:
        if _re.search(pat, low, _re.IGNORECASE):
            return 'lake', 0.80, cleaned
    for pat in _MOUNTAIN_KEYWORDS:
        if _re.search(pat, low, _re.IGNORECASE):
            return 'mountain', 0.80, cleaned
    for pat in _FOREST_KEYWORDS:
        if _re.search(pat, low, _re.IGNORECASE):
            return 'forest', 0.80, cleaned
    for pat in _ROAD_KEYWORDS:
        if _re.search(pat, low, _re.IGNORECASE):
            return 'road', 0.75, cleaned

    # --- settlement suffix check ---
    # strip leading punctuation / quotes from OCR artefacts
    word = _re.sub(r'^[^\w]+', '', low)
    for sfx in _SETTLEMENT_SUFFIXES:
        if word.endswith(sfx) and len(word) > len(sfx) + 1:
            return 'settlement', 0.72, cleaned

    # --- looks like a proper noun (starts with capital, >3 chars) ---
    if cleaned and cleaned[0].isupper() and len(cleaned) >= 4 and cleaned.replace(' ', '').isalpha():
        return 'settlement', 0.55, cleaned

    return 'unknown', 0.40, cleaned


# ─────────────────────────────────────────────────────────────
#  Vertex AI (Google Cloud — uses service_account.json, billed to GCP credit)
# ─────────────────────────────────────────────────────────────

def clean_with_vertex(
    raw_texts: List[str],
    project: str,
    location: str = 'us-central1',
    model: str = 'gemini-2.5-flash',
    batch_size: int = 80
) -> List[Dict]:
    """
    Send raw OCR texts to Vertex AI Gemini for cleaning and classification.
    Uses service_account.json (GOOGLE_APPLICATION_CREDENTIALS) — billed to GCP credit.
    Raises QuotaExhaustedError on quota/rate-limit failures.
    """
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        print('[LLM] google-genai not installed. Run: python -m pip install google-genai')
        return []

    client = genai.Client(vertexai=True, project=project, location=location)

    all_results = []
    quota_errors = 0
    total_batches = (len(raw_texts) + batch_size - 1) // batch_size

    for start in range(0, len(raw_texts), batch_size):
        batch = raw_texts[start:start + batch_size]
        batch_num = start // batch_size + 1
        if start > 0:
            time.sleep(1)  # Vertex AI has higher limits, 1s gap is fine

        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=_build_user_message(batch),
                    config=genai_types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.1,
                        max_output_tokens=16384,
                    ),
                )
                parts = response.candidates[0].content.parts if response.candidates else []
                content = ''.join(p.text for p in parts if p.text and not getattr(p, 'thought', False))
                if not content and hasattr(response, 'text') and response.text:
                    content = response.text
                parsed = _parse_llm_json(content)
                all_results.extend(parsed)
                print(f'[LLM/Vertex] Batch {batch_num}/{total_batches}: {len(parsed)} features')
                break
            except Exception as e:
                err_str = str(e)
                if '429' in err_str or 'quota' in err_str.lower() or 'rate' in err_str.lower():
                    quota_errors += 1
                    wait = 30 * (attempt + 1)
                    print(f'[LLM/Vertex] Rate limit batch {batch_num} (attempt {attempt+1}/3), waiting {wait}s...')
                    time.sleep(wait)
                else:
                    print(f'[LLM/Vertex] Error on batch {batch_num}: {e}')
                    break

    if quota_errors > 0 and not all_results:
        raise QuotaExhaustedError(f'Vertex AI quota exhausted ({quota_errors}/{total_batches} batches failed)')

    return all_results


def clean_with_local(raw_texts: List[str]) -> List[Dict]:
    """Rules-based classifier — zero API calls, zero cost."""
    print(f'[LLM/Local] Classifying {len(raw_texts)} items with rules-based classifier...')
    results = []
    counts: Dict[str, int] = {}
    for t in raw_texts:
        ftype, conf, cleaned = _classify_local(t)
        results.append({
            'original': t,
            'cleaned': cleaned,
            'feature_type': ftype,
            'confidence': conf,
        })
        counts[ftype] = counts.get(ftype, 0) + 1
    summary = ', '.join(f'{k}:{v}' for k, v in sorted(counts.items()))
    print(f'[LLM/Local] Done — {summary}')
    return results


# ─────────────────────────────────────────────────────────────
#  Unified entry point
# ─────────────────────────────────────────────────────────────

def _raw_passthrough(raw_texts: List[str]) -> List[Dict]:
    """Absolute last resort: return raw texts fully unclassified."""
    print('[LLM] Using raw-passthrough fallback (no LLM available).')
    return [
        {'original': t, 'cleaned': t, 'feature_type': 'unknown', 'confidence': 0.5}
        for t in raw_texts
    ]


def clean_with_llm(raw_texts: List[str]) -> List[Dict]:
    """
    Automatically pick the configured LLM provider and clean/classify OCR texts.

    Fallback chain by quality (best first):
      gemini (key1→key2) → grok → claude → openai → groq → local
    """
    import config
    if not raw_texts:
        return []

    provider = config.LLM_PROVIDER
    print(f'[LLM] Using provider: {provider.upper()}  ({len(raw_texts)} items)')

    def _try_groq(texts):
        key = getattr(config, 'GROQ_API_KEY', '')
        mdl = getattr(config, 'GROQ_MODEL', 'llama-3.1-8b-instant')
        if not key:
            return None
        try:
            r = clean_with_groq(texts, key, mdl)
            return r if r else None
        except QuotaExhaustedError as e:
            print(f'[LLM] {e}')
            return None

    def _try_grok(texts):
        key = getattr(config, 'GROK_API_KEY', '')
        mdl = getattr(config, 'GROK_MODEL', 'grok-3-mini')
        if not key:
            return None
        try:
            r = clean_with_grok(texts, key, mdl)
            return r if r else None
        except QuotaExhaustedError as e:
            print(f'[LLM] {e}')
            return None

    def _try_claude(texts):
        key = getattr(config, 'CLAUDE_API_KEY', '')
        mdl = getattr(config, 'CLAUDE_MODEL', 'claude-haiku-4-5')
        if not key:
            return None
        try:
            r = clean_with_claude(texts, key, mdl)
            return r if r else None
        except QuotaExhaustedError as e:
            print(f'[LLM] {e}')
            return None

    def _try_vertex(texts):
        project = getattr(config, 'VERTEX_PROJECT', '')
        location = getattr(config, 'VERTEX_LOCATION', 'us-central1')
        mdl = getattr(config, 'VERTEX_MODEL', 'gemini-2.5-flash')
        if not project:
            return None
        try:
            r = clean_with_vertex(texts, project=project, location=location, model=mdl)
            return r if r else None
        except QuotaExhaustedError as e:
            print(f'[LLM] Vertex AI quota exhausted: {e}')
            return None

    def _try_gemini(texts):
        mdl = getattr(config, 'GEMINI_MODEL', 'gemini-2.5-flash')
        keys = [k for k in [
            getattr(config, 'GEMINI_API_KEY', ''),
            getattr(config, 'GEMINI_API_KEY_2', ''),
        ] if k]
        if not keys:
            return None
        for idx, key in enumerate(keys, 1):
            try:
                r = clean_with_gemini(texts, key, mdl)
                if r:
                    return r
            except QuotaExhaustedError as e:
                print(f'[LLM] Gemini key {idx} exhausted: {e}')
                if idx < len(keys):
                    print(f'[LLM] Trying Gemini key {idx + 1}...')
        return None

    def _try_openai(texts):
        key = getattr(config, 'OPENAI_API_KEY', '')
        mdl = getattr(config, 'OPENAI_MODEL', 'gpt-4o-mini')
        if not key:
            return None
        r = clean_with_openai(texts, key, mdl)
        return r if r else None

    def _try_local(texts):
        return clean_with_local(texts)

    # Build ordered list: configured provider first, then fallbacks, local always last
    # Best speed+cost order: vertex → openai → grok → gemini → claude → groq → local
    _all = [_try_vertex, _try_openai, _try_grok, _try_gemini, _try_claude, _try_local]
    _named = {
        'vertex': _try_vertex,
        'claude': _try_claude,
        'openai': _try_openai,
        'grok':   _try_grok,
        'gemini': _try_gemini,
        'groq':   _try_groq,
        'local':  _try_local,
    }

    if provider not in _named:
        print(f'[LLM] Unknown provider "{provider}". Valid: grok, groq, claude, gemini, openai, local')
        return clean_with_local(raw_texts)

    # Primary first, then remaining order, local always last
    primary = _named[provider]
    fallbacks = [f for f in _all if f is not primary]
    order = [primary] + fallbacks

    for attempt in order:
        result = attempt(raw_texts)
        if result is not None:
            return result
        print('[LLM] Provider unavailable, trying next fallback...')

    # Should never reach here since _try_local always returns
    return _raw_passthrough(raw_texts)
