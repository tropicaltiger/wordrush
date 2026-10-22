"""Growth Opportunity Finder — free-first edition.

Run locally: streamlit run app.py
AI enrichment is optional. Without an Anthropic key, the app uses rule-based
profiling/classification and DuckDuckGo search (when internet access is available).
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

try:
    import anthropic
except ImportError:  # AI is optional in free mode
    anthropic = None

MODEL = os.getenv("CLAUDE_MODEL", "claude-3-7-sonnet-latest")
UA = {"User-Agent": "Mozilla/5.0 (compatible; GrowthFinder/1.1)"}
SEED_FILE = Path(__file__).parent / "seed_sources.json"
BUILD = "2026-10-22-multi-country"


def clean_key(value) -> str:
    """Strip whitespace and stray quotes that often get pasted along with API keys."""
    return (value or "").strip().strip("\"'").strip()


def get_tavily_key():
    """Return (key, source). A key typed in the sidebar wins over the environment variable."""
    typed = clean_key(st.session_state.get("tavily_key"))
    env = clean_key(os.getenv("TAVILY_API_KEY"))
    if typed:
        return typed, ("Railway variable" if typed == env else "sidebar box")
    if env:
        return env, "Railway variable"
    return "", "none"


def key_hint(key: str, source: str) -> str:
    return f"[key from {source}; ends …{key[-4:]}; {len(key)} chars]"

CATEGORIES = [
    "Local Directory / Citation", "Niche Directory", "Forum / Community",
    "Q&A Platform", "Free Content Platform", "Social / Bookmarking",
    "Review Platform", "Guest Post / Blog Outreach", "Press / PR",
    "Awards / Lists / Associations", "Paid Ads / Sponsorship", "Events / Podcasts",
    "Marketplace / Partner Network", "Other",
]

SYSTEM = (
    "You are a senior digital-marketing researcher. Be specific, practical and honest. "
    "Never invent URLs; only use URLs you were given. Treat search snippets as unverified evidence. "
    "Reply with JSON only when asked for JSON."
)


def get_client():
    key = st.session_state.get("anthropic_key") or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    if anthropic is None:
        st.warning("Anthropic SDK is not installed. Continuing in free, non-AI mode.")
        return None
    return anthropic.Anthropic(api_key=key)


def ask(client, system: str, prompt: str, max_tokens: int = 4000) -> str:
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def parse_json(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    return None


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def fetch_site(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        response = requests.get(url, headers=UA, timeout=12, allow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        meta_tag = soup.find("meta", attrs={"name": re.compile("description", re.I)})
        description = meta_tag.get("content", "").strip() if meta_tag else ""
        body = " ".join(soup.get_text(" ").split())[:5000]
        return f"TITLE: {title}\nMETA: {description}\nTEXT: {body}"
    except Exception as exc:  # noqa: BLE001
        return f"(Could not fetch site: {exc})"


def web_search(query: str, n: int = 8) -> list[dict]:
    """Use Tavily when configured; otherwise try DuckDuckGo."""
    tavily, tavily_source = get_tavily_key()
    try:
        if tavily:
            response = requests.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {tavily}"},
                json={"query": query, "max_results": n}, timeout=20,
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Tavily HTTP {response.status_code} {key_hint(tavily, tavily_source)}: {response.text[:120]}"
                )
            return [
                {"title": item.get("title", "Untitled"), "url": item.get("url", ""),
                 "snippet": item.get("content", "")[:350]}
                for item in response.json().get("results", []) if item.get("url", "").startswith("http")
            ]
        from ddgs import DDGS
        with DDGS() as search:
            return [
                {"title": item.get("title", "Untitled"), "url": item.get("href", ""),
                 "snippet": item.get("body", "")[:350]}
                for item in search.text(query, max_results=n) if item.get("href", "").startswith("http")
            ]
    except Exception as exc:
        safe_msg = re.sub(r"tvly-[A-Za-z0-9_-]+", "[REDACTED]", str(exc))[:220]
        return [{"_search_error": f"{type(exc).__name__}: {safe_msg}"}]


# name, then "|"-separated terms (country names, major cities, regions). Longest match wins.
COUNTRIES = {
    "US": ("United States", "usa|u.s.a|united states|america|new york|los angeles|chicago|houston|phoenix|philadelphia|san antonio|san diego|dallas|san jose|austin|san francisco|seattle|denver|boston|miami|atlanta|las vegas|california|texas|florida|new mexico|washington dc|illinois|ohio|michigan|pennsylvania|massachusetts|new jersey|north carolina|arizona|colorado|oregon|nevada|minnesota|tennessee"),
    "GB": ("United Kingdom", "uk|u.k|united kingdom|great britain|britain|england|scotland|wales|northern ireland|london|manchester|leeds|glasgow|liverpool|edinburgh|bristol|sheffield|cardiff|belfast"),
    "CA": ("Canada", "canada|toronto|vancouver|montreal|calgary|ottawa|edmonton|winnipeg|ontario|quebec|alberta|british columbia"),
    "AU": ("Australia", "australia|sydney|melbourne|brisbane|perth|adelaide|canberra|gold coast|new south wales|queensland"),
    "NZ": ("New Zealand", "new zealand|auckland|wellington|christchurch"),
    "IE": ("Ireland", "ireland|dublin|cork|galway|limerick"),
    "IN": ("India", "india|mumbai|delhi|new delhi|bangalore|bengaluru|hyderabad|chennai|kolkata|pune|ahmedabad|jaipur|surat|lucknow|kochi|cochin|coimbatore|madurai|trichy|tiruchirappalli|tamil nadu|kerala|karnataka|maharashtra|gujarat|telangana|andhra pradesh|noida|gurgaon|gurugram|chandigarh|indore|bhopal|nagpur|visakhapatnam|vijayawada|thiruvananthapuram|trivandrum|mysore|mysuru|mangalore|vellore|tirunelveli|erode|tiruppur|pondicherry|puducherry|thanjavur|nashik|rajkot|patna|ranchi|bhubaneswar|guwahati|dehradun|goa"),
    "AE": ("United Arab Emirates", "uae|united arab emirates|dubai|abu dhabi|sharjah|ajman|ras al khaimah|fujairah|umm al quwain|al ain"),
    "SA": ("Saudi Arabia", "saudi arabia|saudi|riyadh|jeddah|dammam|mecca|makkah|medina|khobar"),
    "QA": ("Qatar", "qatar|doha"),
    "KW": ("Kuwait", "kuwait"),
    "BH": ("Bahrain", "bahrain|manama"),
    "OM": ("Oman", "oman|muscat"),
    "JO": ("Jordan", "jordan|amman"),
    "EG": ("Egypt", "egypt|cairo|alexandria|giza"),
    "IL": ("Israel", "israel|tel aviv|jerusalem|haifa"),
    "TR": ("Turkey", "turkey|turkiye|türkiye|istanbul|ankara|izmir"),
    "SG": ("Singapore", "singapore"),
    "MY": ("Malaysia", "malaysia|kuala lumpur|penang|johor|selangor"),
    "ID": ("Indonesia", "indonesia|jakarta|bali|surabaya|bandung"),
    "PH": ("Philippines", "philippines|manila|cebu|davao|quezon"),
    "TH": ("Thailand", "thailand|bangkok|phuket|chiang mai|pattaya"),
    "VN": ("Vietnam", "vietnam|viet nam|hanoi|ho chi minh|saigon|da nang"),
    "HK": ("Hong Kong", "hong kong"),
    "TW": ("Taiwan", "taiwan|taipei|kaohsiung"),
    "JP": ("Japan", "japan|tokyo|osaka|kyoto|yokohama|nagoya|fukuoka|sapporo"),
    "KR": ("South Korea", "south korea|korea|seoul|busan|incheon"),
    "CN": ("China", "china|beijing|shanghai|shenzhen|guangzhou"),
    "DE": ("Germany", "germany|deutschland|berlin|munich|münchen|hamburg|frankfurt|cologne|köln|stuttgart|düsseldorf"),
    "AT": ("Austria", "austria|österreich|vienna|wien|salzburg|graz"),
    "CH": ("Switzerland", "switzerland|schweiz|suisse|zurich|zürich|geneva|basel|bern|lausanne"),
    "FR": ("France", "france|paris|lyon|marseille|toulouse|bordeaux|lille"),
    "ES": ("Spain", "spain|españa|madrid|barcelona|valencia|seville|sevilla|malaga|málaga|bilbao"),
    "PT": ("Portugal", "portugal|lisbon|lisboa|porto|faro"),
    "IT": ("Italy", "italy|italia|rome|roma|milan|milano|naples|napoli|turin|torino|florence|firenze"),
    "NL": ("Netherlands", "netherlands|holland|amsterdam|rotterdam|the hague|utrecht|eindhoven"),
    "BE": ("Belgium", "belgium|brussels|antwerp|ghent"),
    "SE": ("Sweden", "sweden|stockholm|gothenburg|göteborg|malmö|malmo"),
    "NO": ("Norway", "norway|oslo|bergen"),
    "DK": ("Denmark", "denmark|copenhagen|aarhus"),
    "FI": ("Finland", "finland|helsinki|espoo|tampere"),
    "PL": ("Poland", "poland|warsaw|krakow|kraków|wroclaw|gdansk"),
    "CZ": ("Czech Republic", "czech republic|czechia|prague|brno"),
    "GR": ("Greece", "greece|athens|thessaloniki"),
    "RO": ("Romania", "romania|bucharest"),
    "HU": ("Hungary", "hungary|budapest"),
    "BR": ("Brazil", "brazil|brasil|são paulo|sao paulo|rio de janeiro|brasília|brasilia|belo horizonte|curitiba"),
    "MX": ("Mexico", "mexico|méxico|mexico city|guadalajara|monterrey|cancun|cancún"),
    "AR": ("Argentina", "argentina|buenos aires|rosario"),
    "CL": ("Chile", "chile|santiago"),
    "CO": ("Colombia", "colombia|bogotá|bogota|medellín|medellin"),
    "PE": ("Peru", "peru|perú|lima"),
    "UY": ("Uruguay", "uruguay|montevideo"),
    "ZA": ("South Africa", "south africa|johannesburg|cape town|durban|pretoria"),
    "NG": ("Nigeria", "nigeria|lagos|abuja|port harcourt"),
    "KE": ("Kenya", "kenya|nairobi|mombasa"),
    "GH": ("Ghana", "ghana|accra|kumasi"),
    "MA": ("Morocco", "morocco|casablanca|rabat|marrakech"),
    "PK": ("Pakistan", "pakistan|karachi|lahore|islamabad"),
    "BD": ("Bangladesh", "bangladesh|dhaka|chittagong"),
    "LK": ("Sri Lanka", "sri lanka|colombo"),
    "NP": ("Nepal", "nepal|kathmandu"),
}

_TERM_TO_CODE = sorted(
    ((term.strip(), code) for code, (_, terms) in COUNTRIES.items() for term in terms.split("|") if term.strip()),
    key=lambda pair: -len(pair[0]),
)
_TERM_REGEX = [(re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)"), code) for term, code in _TERM_TO_CODE]
CC_TLDS = {("uk" if code == "GB" else code.lower()) for code in COUNTRIES if code not in ("US", "CO")}


def infer_country(location: str) -> str:
    """Country code from free text like 'Dubai', 'Madurai, India' or 'Leeds UK'. '' if unknown."""
    loc = (location or "").lower().strip()
    if not loc:
        return ""
    last = loc.split(",")[-1].strip()
    for term, code in _TERM_TO_CODE:
        if term == last:
            return code
    for pattern, code in _TERM_REGEX:
        if pattern.search(loc):
            return code
    return ""


def country_tld(code: str) -> str:
    if not code or code == "US":
        return ""
    return "." + ("uk" if code == "GB" else code.lower())


NICHE_KEYWORDS = {
    "healthcare": "clinic|hospital|doctor|dental|dentist|physio*|health*|medical|medicine|ivf|fertility|surgery|surgeon|skin|dermat*|ortho*|pediatric*|paediatric*|therapy|therapist|wellness|diagnostic*|pharmacy|ophthalm*|cardio*|gynae*|gyne*|oncolog*|cosmetic surgery|hair transplant|eye care|nursing|ayurved*|homeopath*",
    "safety": "hse|safety|nebosh|iosh|osha|first aid|occupational|fire safety|ehs",
    "education": "training|course|academy|school|college|institute|tuition|coaching|learning|certification|education|university|tutor*|workshop",
    "software": "software|saas|app development|mobile app|web development|website development|startup|api|developer|it services|artificial intelligence|chatbot|cloud|cybersecurity|erp|crm",
    "b2b": "b2b|wholesale|manufactur*|supplier|export*|import*|industrial|logistics|consulting|consultancy|enterprise|distributor|trading company|freight",
    "local_services": "plumb*|electrician|electrical|cleaning|repair|pest control|painting|movers|moving company|home service*|contractor|handyman|ac service|ac repair|maintenance|carpent*|landscap*|interior design*|renovation|laundry|security services",
    "food": "restaurant|cafe|catering|bakery|food|bar|kitchen|coffee|biryani|pizza|bistro|dessert",
    "beauty": "salon|spa|beauty|makeup|barber|hair|nail*|skincare|cosmetic*|tattoo|lash*",
    "real_estate": "real estate|property|properties|realtor|broker|apartment|villa|rental|builder|construction",
    "legal": "lawyer|law firm|attorney|legal|advocate|notary|solicitor",
    "ecommerce": "online store|ecommerce|e-commerce|retail|fashion|jewel*|furniture|electronics|boutique|shopify",
    "auto": "car|auto|vehicle|garage|dealership|tyre|tire|motorcycle|car rental|detailing",
    "travel": "travel|tour|tourism|hotel|resort|holiday|visa|airline|hostel|trip",
}


def _kw_regex(tokens: str):
    parts = []
    for tok in tokens.split("|"):
        tok = tok.strip()
        if not tok:
            continue
        if tok.endswith("*"):
            parts.append(r"\b" + re.escape(tok[:-1]) + r"\w*")
        else:
            parts.append(r"\b" + re.escape(tok) + r"(?:s|es)?\b")
    return re.compile("|".join(parts), re.I)


NICHE_PATTERNS = {tag: _kw_regex(words) for tag, words in NICHE_KEYWORDS.items()}


def detect_niches(strong_text: str, weak_text: str = "") -> list:
    """Tags from what the user typed (any hit) plus website text (needs 3 different hits)."""
    tags = []
    for tag, pattern in NICHE_PATTERNS.items():
        if pattern.search(strong_text or ""):
            tags.append(tag)
            continue
        hits = {m.group(0).lower() for m in pattern.finditer((weak_text or "")[:2000])}
        if len(hits) >= 3:
            tags.append(tag)
    return tags


def local_queries(base: str, location: str, country_name: str) -> list:
    city = (location or "").split(",")[0].strip()
    place = country_name or location
    qs = [
        f"{base} {city} Facebook group community",
        f"{base} {city} forum discussion",
        f"{base} directory {place} submit listing",
        f"{base} chamber of commerce association {place}",
    ]
    return [" ".join(q.split()) for q in qs]


def enrich_profile(profile: dict, business: str, location: str, keyword: str, site_text: str) -> dict:
    """Adds country, niche tags and local search terms, whether the profile came from AI or free mode."""
    detected = infer_country(location)
    code = detected or str(profile.get("country_code") or "").upper()
    if code not in COUNTRIES:
        code = ""
    profile["country_code"] = code
    profile["country_name"] = COUNTRIES[code][0] if code else ""
    city = (location or "").split(",")[0].strip()
    profile["city"] = profile.get("city") or city
    profile["local_terms"] = sorted({t.lower() for t in (city, profile["country_name"]) if t})
    profile["country_tld"] = country_tld(code)
    strong = " ".join([
        business or "", keyword or "", str(profile.get("niche", "")),
        " ".join(str(x) for x in profile.get("services", []) or []),
        " ".join(str(x) for x in profile.get("core_keywords", []) or []),
    ])
    profile["niche_tags"] = detect_niches(strong, site_text)
    base = (keyword or "").strip() or (business or "").strip()
    queries = list(profile.get("search_queries") or [])
    known = {q.lower() for q in queries}
    for q in local_queries(base, location, profile["country_name"]):
        if q.lower() not in known:
            queries.append(q)
            known.add(q.lower())
    profile["search_queries"] = queries[:18]
    return profile


def fallback_profile(business, location, keyword, site_text) -> dict:
    combined = f"{business} {keyword or ''} {site_text[:1200]}".lower()
    niche = keyword.strip() if keyword else business.strip()
    if any(x in combined for x in ["dentist", "dental", "teeth", "orthodont"]):
        niche = "Dental / oral healthcare"
    elif any(x in combined for x in ["physio", "physiotherapy", "rehabilitation"]):
        niche = "Physiotherapy / rehabilitation"
    elif any(x in combined for x in ["restaurant", "catering", "food", "cafe"]):
        niche = "Food / catering"
    elif any(x in combined for x in ["software", "saas", "plugin"]):
        niche = "Software / digital product"
    country = infer_country(location)
    country_name = COUNTRIES[country][0] if country else ""
    base = keyword.strip() if keyword else business.strip()
    city = (location or "").split(",")[0].strip()
    place = country_name or location
    queries = [
        f'"{base}" {city} business directory',
        f"free business listings {city} {place}",
        f"{base} directory {place} submit listing",
        f"{base} {city} local directory listing",
        f"{base} {place} industry association membership",
        f"{base} {city} chamber of commerce business networking",
        f"{base} {city} Facebook group community",
        f"{base} professional forum community discussion {place}",
        f"{base} subreddit community rules {city}",
        f"{base} questions answers forum {place}",
        f"{base} guest post contribute write for us {place}",
        f"{base} podcast guest opportunities",
        f"{base} awards industry directory {place}",
        f"{base} sponsorship advertising rates {city}",
        f"{base} review sites {city} {place}",
        f"{base} local events networking {city}",
    ]
    queries = [" ".join(q.split()) for q in queries]
    return {
        "niche": niche, "services": [keyword] if keyword else [],
        "audience": "Needs confirmation from the business profile",
        "scope": "local" if location else "national", "country_code": country,
        "city": city or location, "core_keywords": [keyword] if keyword else [business],
        "search_queries": queries,
    }


def profile_business(client, business, location, keyword, site_text) -> dict:
    if client is None:
        return fallback_profile(business, location, keyword, site_text)
    prompt = f"""Analyse this business.
Input: {business}
Location: {location}
Keyword (optional): {keyword or 'none'}
Website content:
{site_text}

Return JSON keys: niche, services (list), audience, scope (local/national/global),
country_code (ISO-2), city, core_keywords (5-8), search_queries (14 diverse queries).
Search only for promotion opportunities: free listings, niche directories, forums, Q&A,
content platforms, guest-post opportunities, press/PR, awards, associations, events, podcasts,
and paid listing/sponsorship options. Do NOT search for competitors."""
    try:
        data = parse_json(ask(client, SYSTEM, prompt, 2500))
        if isinstance(data, dict) and data.get("search_queries"):
            return data
    except Exception as exc:  # noqa: BLE001
        st.warning(f"AI profiling failed; using free-mode profiling instead. ({exc})")
    return fallback_profile(business, location, keyword, site_text)


def collect_candidates(queries: list[str]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=6) as executor:
        result_sets = list(executor.map(web_search, queries))
    seen, candidates = set(), []
    errors = []
    for result_set in result_sets:
        for result in result_set:
            if "_search_error" in result:
                errors.append(result["_search_error"])
                continue
            domain = domain_of(result.get("url", ""))
            if domain and domain not in seen:
                seen.add(domain)
                candidates.append(result)
    st.session_state["search_diagnostics"] = {"errors": errors, "queries": len(queries), "results": len(candidates)}
    return candidates[:90]


def heuristic_classify(candidate: dict, profile: dict) -> dict:
    text = f"{candidate.get('title', '')} {candidate.get('snippet', '')} {candidate.get('url', '')}".lower()
    if any(x in text for x in ["directory", "business listing", "citation", "list your business"]):
        category, action = "Local Directory / Citation", "Check eligibility, claim or submit the business profile, and keep contact details consistent."
    elif any(x in text for x in ["reddit", "forum", "community", "discussion", "discourse"]):
        category, action = "Forum / Community", "Read community rules first; answer relevant questions helpfully before sharing a link."
    elif any(x in text for x in ["quora", "question", "answers"]):
        category, action = "Q&A Platform", "Find relevant questions and provide a useful answer; link only when it genuinely helps."
    elif any(x in text for x in ["guest post", "write for us", "contributor", "submit article"]):
        category, action = "Guest Post / Blog Outreach", "Review editorial guidelines and pitch an original, audience-relevant article."
    elif any(x in text for x in ["press release", "journalist", "pr news", "media"]):
        category, action = "Press / PR", "Check submission criteria and prepare a newsworthy announcement or expert response."
    elif any(x in text for x in ["award", "association", "chamber", "membership"]):
        category, action = "Awards / Lists / Associations", "Check membership or entry requirements, fees, deadlines, and audience fit."
    elif any(x in text for x in ["advertising", "ads", "sponsor", "pricing", "media kit"]):
        category, action = "Paid Ads / Sponsorship", "Request current pricing and audience data; test a small budget only with tracking in place."
    elif any(x in text for x in ["review", "trustpilot", "g2", "clutch", "yelp"]):
        category, action = "Review Platform", "Check whether the platform serves your market and invite genuine, policy-compliant reviews."
    elif any(x in text for x in ["podcast", "event", "conference", "webinar"]):
        category, action = "Events / Podcasts", "Check guest, speaker, exhibitor, or event-listing criteria and pitch a relevant topic."
    elif any(x in text for x in ["medium", "substack", "blog", "publish", "article"]):
        category, action = "Free Content Platform", "Publish useful original content and link to a relevant resource where allowed."
    elif any(x in text for x in ["marketplace", "partner", "vendor", "supplier"]):
        category, action = "Marketplace / Partner Network", "Check seller or partner eligibility, commissions, and lead quality before joining."
    else:
        category, action = "Other", "Open the page and confirm it accepts this business type before spending time or money."

    cost = "Unknown"
    if any(x in text for x in ["free listing", "free to submit", "free directory", "no cost"]):
        cost = "Free"
    elif any(x in text for x in ["pricing", "advertising rates", "paid listing", "sponsored listing", "membership fee"]):
        cost = "Unknown"  # pricing mention alone does not prove the listing itself is paid
    relevance = 5
    terms = [w.lower() for w in profile.get("core_keywords", []) if len(str(w)) > 2]
    matches = sum(1 for term in terms if term in text)
    if matches:
        relevance = min(9, 5 + matches)
    if any(x in text for x in ["submit", "add listing", "write for us", "advertise", "join"]):
        relevance = min(10, relevance + 1)
    # Prefer sources that clearly belong to the target place; demote other countries' sites.
    local_terms = [t for t in profile.get("local_terms", []) if len(t) > 2]
    if any(t in text for t in local_terms):
        relevance = min(10, relevance + 1)
    tld = profile.get("country_tld", "")
    if tld:
        dom = domain_of(candidate.get("url", ""))
        last_label = dom.rsplit(".", 1)[-1]
        if dom.endswith(tld):
            relevance = min(10, relevance + 1)
        elif last_label in CC_TLDS and last_label != tld[1:]:
            relevance = max(1, relevance - 2)
    return {
        "Name": candidate.get("title") or domain_of(candidate.get("url", "")),
        "Category": category, "Cost": cost, "Est. cost": "",
        "Relevance": relevance, "Link type": "unknown", "What to do": action,
        "Rules / notes": "Auto-classified without AI; verify submission rules, availability and pricing on the destination site.",
        "URL": candidate.get("url", ""), "Source": "Live search (rule-based)",
    }


def classify(client, profile, candidates) -> list[dict]:
    if client is None:
        return [heuristic_classify(candidate, profile) for candidate in candidates]
    rows = []
    for start in range(0, len(candidates), 25):
        batch = candidates[start:start + 25]
        listing = "\n".join(
            f"{idx}. {item['title']} | {item['url']} | {item['snippet']}"
            for idx, item in enumerate(batch)
        )
        prompt = f"""Business profile: {json.dumps(profile)[:1800]}
Candidate pages from web search:
{listing}

Return a JSON array. For each genuinely useful promotion source (skip competitors, irrelevant pages,
news and spam), provide idx, name, category (one of {CATEGORIES}), cost_type (Free/Paid/Freemium/Unknown),
est_cost (empty if unknown), relevance (1-10), link_type (dofollow/nofollow/unknown),
what_to_do (concrete action), rules_notes. Do not infer a price or link attribute without evidence."""
        try:
            data = parse_json(ask(client, SYSTEM, prompt, 5500)) or []
        except Exception as exc:  # noqa: BLE001
            st.warning(f"AI classification failed for one batch; using rule-based classification. ({exc})")
            data = []
        accepted = set()
        for item in data:
            try:
                idx = int(item["idx"])
                candidate = batch[idx]
            except (KeyError, ValueError, IndexError, TypeError):
                continue
            accepted.add(idx)
            rows.append({
                "Name": item.get("name") or candidate["title"],
                "Category": item.get("category", "Other"),
                "Cost": item.get("cost_type", "Unknown"), "Est. cost": item.get("est_cost", ""),
                "Relevance": item.get("relevance", 5), "Link type": item.get("link_type", "unknown"),
                "What to do": item.get("what_to_do", ""), "Rules / notes": item.get("rules_notes", ""),
                "URL": candidate["url"], "Source": "Live search (AI-assisted)",
            })
        # Don't lose valid search results just because the model returned malformed/incomplete JSON.
        rows.extend(heuristic_classify(c, profile) for i, c in enumerate(batch) if i not in accepted)
    return rows


def load_seed(country_code: str, scope: str, niche_tags=None) -> list[dict]:
    """Curated sources for this country (plus global ones), filtered to the business niche."""
    if not SEED_FILE.exists():
        return []
    try:
        seed_data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    code = (country_code or "").upper()
    tags = set(niche_tags or [])
    rows = []
    for source in seed_data:
        regions = source.get("region", "global")
        regions = {str(r).upper() for r in (regions if isinstance(regions, list) else [regions])}
        niches = set(source.get("niche") or ["all"])
        if "GLOBAL" not in regions and code not in regions:
            continue
        if "all" not in niches and not (niches & tags):
            continue
        if source.get("local_only") and scope == "global":
            continue
        relevance = source.get("relevance", 6)
        if code and code in regions:
            relevance += 1  # local sources matter more than global ones
        if "all" not in niches:
            relevance += 1  # matches the business niche specifically
        rows.append({
            "Name": source["name"], "Category": source["category"], "Cost": source["cost"],
            "Est. cost": source.get("est_cost", ""), "Relevance": min(10, relevance),
            "Link type": source.get("link_type", "unknown"), "What to do": source.get("what_to_do", ""),
            "Rules / notes": source.get("notes", ""), "URL": source["url"], "Source": "Curated starter list",
            "Region": "Global" if "GLOBAL" in regions else ", ".join(sorted(regions)),
        })
    return rows


def verify(rows: list[dict]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=10) as executor:
        checks = list(executor.map(lambda row: check_alive(row["URL"]), rows))
    today = date.today().isoformat()
    for row, check in zip(rows, checks):
        row["Live?"] = "Yes" if check["alive"] else "Unverified"
        row["HTTP status"] = check["status"] or "No response"
        row["Page hints"] = check["page_hints"]
        row["Last checked"] = today
    return rows


def check_alive(url: str) -> dict:
    try:
        response = requests.get(url, headers=UA, timeout=8, allow_redirects=True)
        text = response.text.lower()[:60000]
        hints = []
        if any(word in text for word in ("pricing", "premium", "paid listing", "per month", "₹", "$")):
            hints.append("mentions pricing (not proof the listing is paid)")
        if any(word in text for word in ("sign up", "register", "create account", "login")):
            hints.append("account may be required")
        if any(word in text for word in ("submit", "add your business", "add listing", "list your")):
            hints.append("possible submission flow")
        return {"alive": response.status_code < 400, "status": response.status_code, "page_hints": ", ".join(hints)}
    except Exception:  # noqa: BLE001
        return {"alive": False, "status": 0, "page_hints": "Could not verify automatically; open the URL manually."}


def fallback_plan(profile: dict, df: pd.DataFrame) -> str:
    top = df.sort_values("Relevance", ascending=False).head(12) if not df.empty else pd.DataFrame()
    lines = [
        "## 30-day action plan", "",
        "**Week 1 — Establish the basics**", "- Complete the most relevant business profiles and local citations.",
        "- Confirm name, address, phone, website and service descriptions are consistent.",
        "- Prioritise free sources with strong niche and location fit.", "",
        "**Week 2 — Participate where customers ask questions**", "- Identify 2–3 relevant communities and read their promotion rules.",
        "- Answer real questions with useful, non-promotional guidance.", "- Track visits, enquiries and referral sources.", "",
        "**Week 3 — Publish and build relationships**", "- Publish one useful article, checklist, case study or short video.",
        "- Pitch one relevant industry blog, podcast, association or local publication.",
        "- Repurpose the content for suitable social and Q&A platforms.", "",
        "**Week 4 — Review and test selectively**", "- Review referral traffic, leads and engagement from each source.",
        "- Consider a paid listing or campaign only after checking current costs and audience quality.",
        "- Stop activities that attract irrelevant traffic or low-quality leads.", "",
        "## First opportunities to review", "",
    ]
    for _, row in top.iterrows():
        lines.append(f"- **{row['Name']}** ({row['Category']}, cost: {row['Cost']}) — {row['What to do']}")
    lines += ["", "## Content ideas to adapt", "",
              f"- Beginner's guide to {profile.get('niche', 'your service')}",
              "- Frequently asked customer questions and clear answers",
              "- Common mistakes and how to avoid them", "- A checklist buyers can save or download",
              "- A short case study or before-and-after process (with permission)",
              "- Local guide tailored to the target location", "- Expert tips from the team",
              "- Comparison of approaches, without unsupported claims", "",
              "## Avoid", "- Mass-posting identical links across unrelated sites",
              "- Paying before checking real audience, terms, and cancellation policy",
              "- Treating a live URL as proof that a listing is free, relevant, or accepting submissions",
              "- Assuming backlinks are dofollow or guaranteed to improve rankings"]
    return "\n".join(lines)


def build_plan(client, profile, df: pd.DataFrame) -> str:
    if client is None:
        return fallback_plan(profile, df)
    top = df.sort_values("Relevance", ascending=False).head(35)[
        ["Name", "Category", "Cost", "Relevance", "What to do"]
    ]
    prompt = f"""Business profile: {json.dumps(profile)[:1800]}
Top opportunities:
{top.to_csv(index=False)}

Write a practical 30-day action plan in Markdown: week-by-week tasks, estimated time, 8 niche-specific
post ideas, risks to avoid, and other opportunities such as events, associations, awards and podcasts.
Do not invent costs or claim that a site guarantees rankings."""
    try:
        return ask(client, SYSTEM, prompt, 3500)
    except Exception as exc:  # noqa: BLE001
        st.warning(f"AI plan generation failed; using a practical template. ({exc})")
        return fallback_plan(profile, df)


# --------------------------------------------------------------------------- UI
st.set_page_config(page_title="Growth Opportunity Finder", page_icon="🔎", layout="wide")
st.title("🔎 Growth Opportunity Finder")
st.caption("Find relevant places to promote a business: free opportunities, paid options, communities, directories and a practical action plan.")

with st.sidebar:
    st.header("Settings")
    st.caption("AI is optional. Leave the Anthropic key blank to use free-mode analysis.")
    st.session_state["anthropic_key"] = st.text_input(
        "Anthropic API key (optional)", type="password", value=os.getenv("ANTHROPIC_API_KEY", ""),
        autocomplete="new-password",
    )
    st.session_state["tavily_key"] = st.text_input(
        "Tavily API key (optional)", type="password", value=os.getenv("TAVILY_API_KEY", ""),
        autocomplete="new-password",
    )
    verify_links = st.checkbox("Check destination links", value=False,
                               help="Adds time and some sites block automated checks. It does not verify prices or submission eligibility.")
    active_tavily, active_source = get_tavily_key()
    provider = "Tavily" if active_tavily else "DuckDuckGo"
    st.info(f"Search provider selected: **{provider}**. This confirms configuration, not successful API authentication.")
    key_note = f"from {active_source}, ends …{active_tavily[-4:]}, {len(active_tavily)} chars" if active_tavily else "none"
    st.caption(f"Build: {BUILD} · Tavily key in use: {key_note}")

with st.form("inputs"):
    c1, c2, c3 = st.columns([2, 1.5, 1.5])
    business = c1.text_input("Website or business", placeholder="example.com or Dental clinic")
    location = c2.text_input("Target location", placeholder="Coimbatore, India")
    keyword = c3.text_input("Target keyword (optional)", placeholder="teeth whitening")
    go = st.form_submit_button("Find opportunities", type="primary")

if go:
    if not business.strip() or not location.strip():
        st.warning("Enter the website/business and the target location.")
        st.stop()
    client = get_client()
    with st.status("Researching promotion opportunities…", expanded=True) as status:
        st.write("Reading the website and identifying the business niche")
        site_text = fetch_site(business.strip()) if "." in business and " " not in business else ""
        profile = profile_business(client, business.strip(), location.strip(), keyword.strip(), site_text)
        profile = enrich_profile(profile, business.strip(), location.strip(), keyword.strip(), site_text)
        if not profile.get("country_code"):
            st.warning("Could not recognise the country from the location, so only global sources are added. Type it as 'City, Country'.")
        st.write(f"Searching the web with {len(profile['search_queries'])} queries")
        candidates = collect_candidates(profile["search_queries"])
        diag = st.session_state.get("search_diagnostics", {})
        st.write(f"Search provider: {provider} | Live candidates: {len(candidates)} | Failed queries: {len(diag.get('errors', []))}")
        if diag.get("errors"):
            st.warning("Search errors occurred. Expand diagnostics below for details.")
        st.write(f"Classifying {len(candidates)} live-search results")
        rows = classify(client, profile, candidates)
        rows += load_seed(profile.get("country_code", ""), profile.get("scope", "local"), profile.get("niche_tags", []))
        unique, seen = [], set()
        for row in rows:
            url = row.get("URL", "")
            domain = domain_of(url)
            if not domain:
                continue
            is_seed = str(row.get("Source", "")).startswith("Curated")
            # Live results: one row per site. Curated rows: one per page, unless live search already found the site.
            key = (domain, urlparse(url).path.rstrip("/")) if is_seed else (domain, "")
            if key in seen or (is_seed and (domain, "") in seen):
                continue
            seen.add(key)
            unique.append(row)
        if verify_links and unique:
            st.write(f"Checking {len(unique)} destination links")
            unique = verify(unique)
        df = pd.DataFrame(unique)
        if "Region" in df.columns:
            df["Region"] = df["Region"].fillna("")
        plan = build_plan(client, profile, df) if not df.empty else fallback_plan(profile, df)
        status.update(label="Research complete", state="complete")
    st.session_state["result"] = {"profile": profile, "df": df, "plan": plan}

result = st.session_state.get("result")
if result:
    profile, df, plan = result["profile"], result["df"], result["plan"]
    if df.empty:
        st.warning("No sources were returned. Check internet access or add a search API key.")
        st.stop()
    diag = st.session_state.get("search_diagnostics", {})
    st.caption(f"Search provider: {provider} · Live candidates: {diag.get('results', 0)} · Failed queries: {len(diag.get('errors', []))} · Curated sources are separate")
    if diag.get("errors"):
        with st.expander("Search diagnostics"):
            for err in list(dict.fromkeys(diag["errors"]))[:5]:
                st.error(err)
    st.caption(f"Detected country: {profile.get('country_name') or 'not recognised'} · Business type tags: {', '.join(profile.get('niche_tags', [])) or 'none detected'}")
    with st.expander("Business profile", expanded=False):
        st.json(profile)
    st.info("Cost, rules, and relevance are research indicators, not guarantees. Confirm details on each destination before publishing or paying.")
    f1, f2, f3 = st.columns(3)
    cost_filter = f1.multiselect("Cost classification", ["Free", "Freemium", "Paid", "Unknown"],
                                 default=["Free", "Freemium", "Paid", "Unknown"])
    min_relevance = f2.slider("Minimum relevance", 1, 10, 5)
    only_live = f3.checkbox("Only links that returned a successful response", value=False, disabled="Live?" not in df.columns)
    view = df[df["Cost"].isin(cost_filter) & pd.to_numeric(df["Relevance"], errors="coerce").fillna(1).ge(min_relevance)]
    if only_live and "Live?" in view.columns:
        view = view[view["Live?"] == "Yes"]
    view = view.sort_values("Relevance", ascending=False)
    st.download_button("Download results as CSV", view.to_csv(index=False).encode("utf-8-sig"),
                       "growth-opportunities.csv", "text/csv")
    tabs = st.tabs(["All opportunities", "Free / Freemium", "Paid options", "30-day plan"])
    config = {"URL": st.column_config.LinkColumn("Open source")}
    with tabs[0]:
        st.dataframe(view, use_container_width=True, hide_index=True, column_config=config)
    with tabs[1]:
        free = view[view["Cost"].isin(["Free", "Freemium"])]
        if free.empty:
            st.write("No sources are confidently classified as free. Review Unknown entries before excluding them.")
        else:
            for category in CATEGORIES:
                subset = free[free["Category"] == category]
                if not subset.empty:
                    st.subheader(category)
                    st.dataframe(subset, use_container_width=True, hide_index=True, column_config=config)
    with tabs[2]:
        paid = view[view["Cost"].isin(["Paid", "Freemium", "Unknown"])]
        st.caption("Unknown is included because a source may have paid upgrades even when its basic listing is free.")
        st.dataframe(paid, use_container_width=True, hide_index=True, column_config=config)
    with tabs[3]:
        st.markdown(plan)
