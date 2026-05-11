# utils/llm_utils.py  –  LLM wrappers for OpenAI, Google Gemini, and Anthropic Claude

import json
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional


# ─────────────────────────────────────────────────────────────
#  Country knowledge block loader
# ─────────────────────────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).parent.parent / 'prompts'

_COUNTRY_INTRO = {
    'india':    'historical Survey of India (SOI) topographical maps, primarily covering the Indian subcontinent from the late 1800s to mid-1900s',
    'uk':       'Ordnance Survey (OS) topographical maps of Great Britain and Northern Ireland from the 1800s to 1970s',
    'usa':      'USGS topographic quadrangle maps of the United States from the 1880s to present',
    'germany':  'German Topographische Karte / Messtischblatt maps from the 1870s to 1980s',
    'france':   'French Institut Géographique National (IGN) topographic maps and historical Carte de France from the 1870s to present',
    'pakistan': 'Survey of Pakistan (SOP) topographical maps, including pre-1947 Survey of India heritage sheets',
}


def _load_country_block(country: str) -> str:
    """Return the country-specific knowledge block as a string, or empty string if not found."""
    prompt_file = _PROMPTS_DIR / f'{country}.txt'
    if prompt_file.exists():
        return prompt_file.read_text(encoding='utf-8').strip()
    return ''


# Keywords printed on real maps that identify their country of origin.
# Each tuple is (keyword_lowercase, country).  First match wins.
_COUNTRY_KEYWORDS = [
    # India — Survey of India is the dominant agency
    ('survey of india',         'india'),
    ('published by the survey of india', 'india'),
    ('printed at the survey of india', 'india'),
    ('dehra dun',               'india'),
    ('dehradun',                'india'),
    ('s.o.i.',                  'india'),
    # Pakistan — Survey of Pakistan / pre-partition SOI heritage sheets
    ('survey of pakistan',      'pakistan'),
    ('s.o.p.',                  'pakistan'),
    # UK — Ordnance Survey
    ('ordnance survey',         'uk'),
    ('published by ordnance',   'uk'),
    ('crown copyright',         'uk'),
    ('o.s. sheet',              'uk'),
    # USA — USGS
    ('u.s. geological survey',  'usa'),
    ('united states geological', 'usa'),
    ('usgs',                    'usa'),
    ('department of the interior', 'usa'),
    # Germany
    ('topographische karte',    'germany'),
    ('landesvermessung',        'germany'),
    ('messtischblatt',          'germany'),
    ('bayerisches landesamt',   'germany'),
    # France
    ('institut géographique',   'france'),
    ('institut geographique',   'france'),
    ('carte de france',         'france'),
    ('ign',                     'france'),
    ('service géographique',    'france'),
    ('service geographique',    'france'),
]


def detect_country(ocr_texts: list, fallback: str = 'india') -> str:
    """
    Scan a flat list of OCR strings extracted from a map and return the
    most likely country code ('india', 'uk', 'usa', 'germany', 'france',
    'pakistan').  Falls back to *fallback* (default 'india') if nothing
    matches.

    *ocr_texts* can be a list of strings, a list of dicts with a 'text'
    key, or a nested list — the function flattens automatically.
    """
    # Flatten whatever structure is passed in
    flat: list[str] = []
    def _collect(obj):
        if isinstance(obj, str):
            flat.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                _collect(v)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                _collect(item)
    _collect(ocr_texts)

    combined = ' '.join(flat).lower()

    for keyword, country in _COUNTRY_KEYWORDS:
        if keyword in combined:
            return country

    return fallback


def build_system_prompt(country: str = 'india') -> str:
    """Build the full LLM system prompt with country-specific knowledge injected."""
    country = country.lower().strip()
    intro = _COUNTRY_INTRO.get(country, f'{country} topographical maps')
    country_block = _load_country_block(country)

    country_section = ''
    if country_block:
        country_section = f"""
════════════════════════════════════════════════════════
COUNTRY-SPECIFIC KNOWLEDGE — {country.upper()}
════════════════════════════════════════════════════════
{country_block}

"""

    return f"""You are an expert at analyzing OCR text extracted from {intro}.

Place names on these maps are romanised or printed in the local script conventions of the region. OCR errors are common due to aged ink, curved typography, and map symbols overlapping text.
{country_section}
════════════════════════════════════════════════════════
GOLDEN RULE — PRESERVE EXACTLY WHAT IS ON THE MAP
════════════════════════════════════════════════════════
Your ONLY job is to recover the exact spelling physically printed on the map.

NEVER do any of the following:
• NEVER modernise or update a place name  (e.g. do NOT change "Cawnpore" → "Kanpur",
  "Bombay" → "Mumbai", "Calcutta" → "Kolkata", "Poona" → "Pune",
  "Dacca" → "Dhaka", "Peshawur" → "Peshawar", "Simla" → "Shimla",
  "Allahabad" → "Prayagraj", "Madras" → "Chennai", "Kotah" → "Kota")
• NEVER translate a name into another language
• NEVER correct a historically accepted spelling variant
• NEVER add, remove or rearrange words

If a name looks old-fashioned or colonial — keep it EXACTLY as the map shows it.
The user will cross-check results against the physical map; the output must match character-for-character (apart from machine OCR errors below).

════════════════════════════════════════════════════════
TASK 1 — CLEAN  (fix MACHINE OCR misreads only)
════════════════════════════════════════════════════════
Fix only character-level substitutions caused by the OCR scanner misreading ink:
• l → I  or  1    (e.g. "llpur" → "Ilpur" or "1lpur" → "Ilpur")
• 0 → O           (e.g. "0ld Fort" → "Old Fort")
• rn → m          (e.g. "Rarnpur" → "Rampur")
• ii → u or n     (e.g. "Naiini Tal" → "Naini Tal")
• vv → w          (e.g. "Nevv Delhi" → "New Delhi")
• 8 → B           (e.g. "8ijnor" → "Bijnor")
• 6 → G or b      (e.g. "6anga" → "Ganga")
• broken spaces in compound names are usually OCR artifacts — rejoin them
Apply country-specific OCR corrections listed above where relevant.

════════════════════════════════════════════════════════
TASK 2 — CLASSIFY  (assign feature_type)
════════════════════════════════════════════════════════
Use exactly one of these types (refer to country-specific suffixes above):
  settlement  – village, town, city, hamlet
  river       – river, stream, canal, nala, nadi, beck, burn, creek, ruisseau, bach
  mountain    – hill, ridge, peak, range, ghat, ben, tor, puy, berg, mont
  lake        – lake, tank, reservoir, jheel, loch, étang, see, pond, tarn
  forest      – jungle, reserve forest, wood, bois, wald, forst
  road        – road, track, path, chemin, weg, bridle path
  landmark    – fort, temple, church, abbey, bench mark, trigonometric station, shelter, inn

════════════════════════════════════════════════════════
TASK 3 — FILTER  (discard noise — set feature_type = "noise")
════════════════════════════════════════════════════════
Discard ALL of these — they are map annotations, NOT geographic names:
  • Scale / measurement text: "1:50000", "1:63360", "Miles", "Yards", "Feet", "Kilometres", "Chains"
  • Contour elevation values: any standalone number like "100", "200", "500", "1000", "2500"
  • Spot heights: "▲ 342", numbers near triangles
  • Grid reference labels alone: "A", "B", "C", "1", "2", "A1", "B-3"
  • Compass / direction labels: "N", "S", "E", "W", "NE", "NW", "SE", "SW"
  • Sheet metadata: "Sheet No", "Revised", "Edition", "Printed", "Surveyed", year numbers alone
  • Latitude/longitude markers: "78°30'", "25°N", degree symbols, grid zone labels
  • Hachure / symbol legends: "Sand", "Marsh", "Scrub", "Cultivation" when printed as map legends
  • Publisher / copyright text: any text identifying the mapping agency, copyright notice, edition note
  Refer to country-specific noise patterns listed above for additional items to discard.

════════════════════════════════════════════════════════
TASK 4 — SCORE  (confidence 0.0–1.0)
════════════════════════════════════════════════════════
  1.0 = clearly a real geographic name, OCR is clean
  0.8 = recognisable name, minor OCR artefact corrected
  0.6 = plausible place name but some uncertainty
  0.4 = OCR heavily garbled, best guess only
  0.2 = very uncertain — could be noise or a name

════════════════════════════════════════════════════════
OUTPUT FORMAT — STRICT
════════════════════════════════════════════════════════
Return ONLY a valid JSON array. Each element must have exactly these four fields:
{{
  "original":     "<raw OCR text exactly as given>",
  "cleaned":      "<corrected name with proper capitalisation>",
  "feature_type": "settlement|river|mountain|lake|forest|road|landmark|noise",
  "confidence":   0.0-1.0
}}

Rules:
- Never omit any input item — every item must appear in output as either a real feature or noise
- Never add text, markdown, or commentary outside the JSON array
- Return [] only if the input list is empty
"""


# ─────────────────────────────────────────────────────────────
#  Backward-compatible alias: SYSTEM_PROMPT uses default (india)
# ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = build_system_prompt('india')


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
                max_tokens=8192,
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
#  Shared exceptions (must be defined before any provider uses them)
# ─────────────────────────────────────────────────────────────

class QuotaExhaustedError(Exception):
    """Raised when the LLM API quota/rate-limit is fully exhausted."""


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

def clean_with_gemini(
    raw_texts: List[str],
    api_key: str,
    model: str = 'gemini-2.5-flash',
    batch_size: int = 100
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

    try:
        import config as _cfg
        _gemini_sleep = float(getattr(_cfg, 'GEMINI_BATCH_SLEEP', 2.0))
    except Exception:
        _gemini_sleep = 2.0

    for start in range(0, len(raw_texts), batch_size):
        batch = raw_texts[start:start + batch_size]
        batch_num = start // batch_size + 1
        if start > 0:
            time.sleep(_gemini_sleep)  # configurable via GEMINI_BATCH_SLEEP in .env

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
                content = ''.join(p.text for p in parts if p.text and not getattr(p, 'thought', False))
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
                time.sleep(1)  # paid tier — 1s gap is sufficient
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
    batch_size: int = 80
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
#  Vertex AI (Google Cloud — uses service_account2.json, billed to GCP credit)
# ─────────────────────────────────────────────────────────────

def clean_with_vertex(
    raw_texts: List[str],
    project: str,
    location: str = 'us-central1',
    model: str = 'gemini-2.5-flash',
    batch_size: int = 200
) -> List[Dict]:
    """
    Send raw OCR texts to Vertex AI Gemini for cleaning and classification.
    Uses service_account2.json (GOOGLE_APPLICATION_CREDENTIALS) — billed to GCP credit.
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
            time.sleep(0.5)  # Vertex AI has higher limits, 0.5s gap is fine

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

    # ── Ensemble mode: run all configured LLMs in parallel and vote ───────────
    if getattr(config, 'ENSEMBLE_MODE', False):
        from utils.cpu_utils import throttler as _throttler
        # Use env-capped value, but never exceed what the CPU can handle right now
        cfg_workers = getattr(config, 'LLM_ENSEMBLE_WORKERS', _throttler.max_workers)
        workers = min(cfg_workers, _throttler.workers)
        print(f'[LLM] ENSEMBLE MODE — running all configured LLMs in parallel (workers={workers}  {_throttler.status()})')
        return clean_with_ensemble(raw_texts, max_workers=workers)

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
    _all = [_try_vertex, _try_openai, _try_grok, _try_gemini, _try_claude, _try_groq, _try_local]
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


# ─────────────────────────────────────────────────────────────
#  Ensemble: run ALL available LLMs in parallel and vote
# ─────────────────────────────────────────────────────────────

def clean_with_ensemble(raw_texts: List[str], max_workers: int = 6) -> List[Dict]:
    """
    Run every configured LLM simultaneously via ThreadPoolExecutor, then merge
    results by majority vote.

    Voting rules (per original text token):
      - feature_type  → plurality vote across all LLMs that returned it
      - cleaned       → most-common cleaned form
      - confidence    → mean of individual confidences × agreement ratio
        (full consensus = no penalty, 50 % agreement ≈ 0.85× penalty)

    Falls back to clean_with_local() if zero LLMs are configured.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from collections import Counter
    import config

    if not raw_texts:
        return []

    # ── Build job list from whatever API keys are present ────────────────────
    jobs: List[tuple] = []   # (display_name, callable)

    # Gemini (supports two keys → two parallel calls for speed + quota)
    gemini_keys = [k for k in [
        getattr(config, 'GEMINI_API_KEY', ''),
        getattr(config, 'GEMINI_API_KEY_2', ''),
    ] if k]
    gemini_mdl = getattr(config, 'GEMINI_MODEL', 'gemini-2.5-flash')
    for i, gkey in enumerate(gemini_keys, 1):
        _gkey, _gmdl = gkey, gemini_mdl   # capture in closure
        jobs.append((f'gemini-k{i}', lambda t, k=_gkey, m=_gmdl: clean_with_gemini(t, k, m)))

    # OpenAI / GPT
    if getattr(config, 'OPENAI_API_KEY', ''):
        _ok = config.OPENAI_API_KEY
        _om = getattr(config, 'OPENAI_MODEL', 'gpt-4o-mini')
        jobs.append(('openai', lambda t, k=_ok, m=_om: clean_with_openai(t, k, m)))

    # Grok (xAI)
    if getattr(config, 'GROK_API_KEY', ''):
        _grok_k = config.GROK_API_KEY
        _grok_m = getattr(config, 'GROK_MODEL', 'grok-3-mini')
        jobs.append(('grok', lambda t, k=_grok_k, m=_grok_m: clean_with_grok(t, k, m)))

    # Claude (Anthropic)
    if getattr(config, 'CLAUDE_API_KEY', ''):
        _ck = config.CLAUDE_API_KEY
        _cm = getattr(config, 'CLAUDE_MODEL', 'claude-3-5-haiku-20241022')
        jobs.append(('claude', lambda t, k=_ck, m=_cm: clean_with_claude(t, k, m)))

    # Groq (Llama)
    if getattr(config, 'GROQ_API_KEY', ''):
        _grq_k = config.GROQ_API_KEY
        _grq_m = getattr(config, 'GROQ_MODEL', 'llama-3.1-8b-instant')
        jobs.append(('groq', lambda t, k=_grq_k, m=_grq_m: clean_with_groq(t, k, m)))

    # Vertex AI (Google Cloud / service account)
    if getattr(config, 'VERTEX_PROJECT', ''):
        _vp = config.VERTEX_PROJECT
        _vl = getattr(config, 'VERTEX_LOCATION', 'us-central1')
        _vm = getattr(config, 'VERTEX_MODEL', 'gemini-2.5-flash')
        jobs.append(('vertex', lambda t, p=_vp, l=_vl, m=_vm: clean_with_vertex(t, p, l, m)))

    if not jobs:
        print('[Ensemble] No LLM APIs configured — falling back to local classifier')
        return clean_with_local(raw_texts)

    names = [n for n, _ in jobs]
    print(f'[Ensemble] Launching {len(jobs)} LLMs in parallel: {names}')

    # ── Run all LLMs concurrently ─────────────────────────────────────────────
    llm_outputs: Dict[str, List[Dict]] = {}   # name → result list

    def _run_one(name: str, fn) -> tuple:
        try:
            result = fn(raw_texts)
            print(f'[Ensemble] {name}: {len(result)} items returned')
            return name, result
        except QuotaExhaustedError as exc:
            print(f'[Ensemble] {name}: quota exhausted — {exc}')
            return name, []
        except Exception as exc:
            print(f'[Ensemble] {name}: error — {exc}')
            return name, []

    workers = min(max_workers, len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_one, n, fn): n for n, fn in jobs}
        for future in as_completed(futures):
            name, result = future.result()
            if result:
                llm_outputs[name] = result

    if not llm_outputs:
        print('[Ensemble] All LLMs failed — falling back to local classifier')
        return clean_with_local(raw_texts)

    n_llms = len(llm_outputs)
    print(f'[Ensemble] {n_llms}/{len(jobs)} LLMs succeeded — merging by vote...')

    # ── Vote: aggregate per original-text token ───────────────────────────────
    # votes[original] = {
    #     'cleaned':      Counter of cleaned strings,
    #     'feature_type': Counter of feature_type strings,
    #     'confidences':  list of float,
    # }
    votes: Dict[str, Dict] = {}

    for _name, items in llm_outputs.items():
        for item in items:
            orig = item.get('original', '').strip()
            if not orig:
                continue
            if orig not in votes:
                votes[orig] = {
                    'cleaned':      Counter(),
                    'feature_type': Counter(),
                    'confidences':  [],
                }
            votes[orig]['cleaned'][item.get('cleaned', orig)] += 1
            votes[orig]['feature_type'][item.get('feature_type', 'unknown')] += 1
            votes[orig]['confidences'].append(float(item.get('confidence', 0.5)))

    # ── Build merged output ───────────────────────────────────────────────────
    merged: List[Dict] = []
    for orig, data in votes.items():
        best_cleaned, top_cleaned_votes = data['cleaned'].most_common(1)[0]
        best_type,    top_type_votes    = data['feature_type'].most_common(1)[0]

        total_votes   = sum(data['feature_type'].values())
        agreement     = top_type_votes / total_votes          # 0..1
        avg_conf      = sum(data['confidences']) / len(data['confidences'])

        # Boost for consensus: full agreement → no deduction; 50 % → 0.85x
        final_conf = min(0.99, avg_conf * (0.70 + 0.30 * agreement))

        merged.append({
            'original':     orig,
            'cleaned':      best_cleaned,
            'feature_type': best_type,
            'confidence':   round(final_conf, 4),
            'llm_count':    total_votes,
            'agreement':    round(agreement, 3),
        })

    # Sort to preserve stable ordering (highest confidence first)
    merged.sort(key=lambda x: x['confidence'], reverse=True)

    kept    = sum(1 for m in merged if m['feature_type'] != 'noise')
    noise   = len(merged) - kept
    print(f'[Ensemble] Merged {len(merged)} items '
          f'({kept} features, {noise} noise) from {n_llms} LLMs')
    return merged


# ─────────────────────────────────────────────────────────────
#  Historical spelling normalisation
# ─────────────────────────────────────────────────────────────

_SPELLING_VARIANTS: Optional[Dict[str, str]] = None


def _load_spelling_variants() -> Dict[str, str]:
    """
    Load prompts/spelling_variants.json and return a flat dict
    { historical_form_lower → canonical_form }.
    Cached after first call.
    """
    global _SPELLING_VARIANTS
    if _SPELLING_VARIANTS is not None:
        return _SPELLING_VARIANTS

    variants_path = _PROMPTS_DIR / 'spelling_variants.json'
    flat: Dict[str, str] = {}

    if not variants_path.exists():
        _SPELLING_VARIANTS = flat
        return flat

    try:
        raw = json.loads(variants_path.read_text(encoding='utf-8'))
        for section_key, mapping in raw.items():
            if section_key == 'meta':
                continue
            if isinstance(mapping, dict):
                for hist, canonical in mapping.items():
                    flat[hist.lower()] = canonical
    except Exception as exc:
        print(f'[Spelling] Failed to load spelling_variants.json: {exc}')

    _SPELLING_VARIANTS = flat
    print(f'[Spelling] Loaded {len(flat)} historical spelling variants')
    return flat


def normalise_spelling(text: str) -> str:
    """
    Replace a historical/colonial spelling with the canonical modern form.
    Performs whole-word, case-insensitive substitution.
    Returns the input unchanged if no variant is found.
    """
    variants = _load_spelling_variants()
    if not variants:
        return text

    import re as _re2
    # Try the full text first (exact match after stripping)
    lookup = text.strip().lower()
    if lookup in variants:
        # Preserve original capitalisation style of first char
        canonical = variants[lookup]
        if text and text[0].isupper() and canonical:
            return canonical[0].upper() + canonical[1:]
        return canonical

    # Word-by-word substitution for multi-word strings
    def _replace_word(m: '_re2.Match') -> str:
        word = m.group(0)
        replacement = variants.get(word.lower())
        if replacement is None:
            return word
        # Match capitalisation of the original word
        if word[0].isupper():
            return replacement[0].upper() + replacement[1:]
        return replacement

    return _re2.sub(r"[A-Za-z']+", _replace_word, text)


def apply_spelling_normalisation(items: List[Dict]) -> List[Dict]:
    """
    Run normalise_spelling() over the 'cleaned' field of every item in a
    list of LLM-output dicts.  Modifies in-place and returns the list.
    """
    _load_spelling_variants()   # ensure cache is warm
    for item in items:
        original_cleaned = item.get('cleaned', '')
        normalised = normalise_spelling(original_cleaned)
        if normalised != original_cleaned:
            item['cleaned']         = normalised
            item['spelling_source'] = original_cleaned   # keep provenance
    return items
