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
    tavily = st.session_state.get("tavily_key") or os.getenv("TAVILY_API_KEY")
    try:
        if tavily:
            response = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": tavily, "query": query, "max_results": n}, timeout=20,
            )
            response.raise_for_status()
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
        return [{"_search_error": f"{type(exc).__name__}: {re.sub(r"tvly-[A-Za-z0-9_-]+", "[REDACTED]", str(exc))[:220]}"}]


def infer_country(location: str) -> str:
    loc = location.lower()
    mapping = {
        "IN": ["india", "coimbatore", "chennai", "madurai", "tamil nadu", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "kerala"],
        "US": ["usa", "united states", "new york", "california", "texas", "florida", "chicago"],
        "GB": ["uk", "united kingdom", "england", "london", "manchester", "scotland"],
        "CA": ["canada", "toronto", "vancouver", "montreal"],
        "AU": ["australia", "sydney", "melbourne", "brisbane"],
        "AE": ["uae", "dubai", "abu dhabi"],
    }
    for code, terms in mapping.items():
        if any(term in loc for term in terms):
            return code
    return ""


def fallback_profile(business, location, keyword, site_text) -> dict:
    combined = f"{business} {keyword or ''} {site_text[:1200]}".lower()
    niche = keyword.strip() if keyword else business.strip()
    if any(x in combined for x in ["dentist", "dental", "teeth", "orthodont"]):
        niche = "Dental / oral healthcare"
    elif any(x in combined for x in ["physio", "physiotherapy", "rehabilitation"]):
        niche = "Physiotherapy / rehabilitation"
    elif any(x in combined for x in ["restaurant", "catering", "food", "cafe"]):
        niche = "Food / catering"
    elif any(x in combined for x in ["software", "saas", "app", "plugin", "tool"]):
        niche = "Software / digital product"
    country = infer_country(location)
    base = keyword.strip() if keyword else business.strip()
    queries = [
        f'"{base}" {location} business directory',
        f"free business listings {location} {base}",
        f"{base} niche directory submit listing",
        f"{base} industry association membership {location}",
        f"{base} professional forum community discussion",
        f"{base} subreddit community rules",
        f"{base} questions answers forum",
        f"{base} guest post contribute write for us",
        f"{base} submit article industry blog",
        f"{base} podcast guest opportunities",
        f"{base} awards industry directory",
        f"{base} sponsorship advertising rates directory",
        f"{base} review sites {location}",
        f"{base} local events networking {location}",
    ]
    return {
        "niche": niche, "services": [keyword] if keyword else [],
        "audience": "Needs confirmation from the business profile",
        "scope": "local" if location else "national", "country_code": country,
        "city": location, "core_keywords": [keyword] if keyword else [business],
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


def load_seed(country_code: str, scope: str) -> list[dict]:
    if not SEED_FILE.exists():
        return []
    try:
        seed_data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    code = (country_code or "").upper()
    rows = []
    for source in seed_data:
        region = source.get("region", "global").upper()
        if region in ("GLOBAL", code):
            if source.get("local_only") and scope == "global":
                continue
            rows.append({
                "Name": source["name"], "Category": source["category"], "Cost": source["cost"],
                "Est. cost": source.get("est_cost", ""), "Relevance": source.get("relevance", 6),
                "Link type": source.get("link_type", "unknown"), "What to do": source.get("what_to_do", ""),
                "Rules / notes": source.get("notes", ""), "URL": source["url"], "Source": "Curated starter list",
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
        "Anthropic API key (optional)", type="password", value=os.getenv("ANTHROPIC_API_KEY", "")
    )
    st.session_state["tavily_key"] = st.text_input(
        "Tavily API key (optional)", type="password", value=os.getenv("TAVILY_API_KEY", "")
    )
    verify_links = st.checkbox("Check destination links", value=False,
                               help="Adds time and some sites block automated checks. It does not verify prices or submission eligibility.")
    provider = "Tavily" if (st.session_state.get("tavily_key") or os.getenv("TAVILY_API_KEY")) else "DuckDuckGo"
    st.info(f"Search provider selected: **{provider}**. This confirms configuration, not successful API authentication.")

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
        st.write(f"Searching the web with {len(profile['search_queries'])} queries")
        candidates = collect_candidates(profile["search_queries"])
        diag = st.session_state.get("search_diagnostics", {})
        st.write(f"Search provider: {provider} | Live candidates: {len(candidates)} | Failed queries: {len(diag.get('errors', []))}")
        if diag.get("errors"):
            st.warning("Search errors occurred. Expand diagnostics below for details.")
        st.write(f"Classifying {len(candidates)} live-search results")
        rows = classify(client, profile, candidates)
        rows += load_seed(profile.get("country_code") or infer_country(location), profile.get("scope", "local"))
        unique, seen = [], set()
        for row in rows:
            domain = domain_of(row.get("URL", ""))
            if domain and domain not in seen:
                seen.add(domain)
                unique.append(row)
        if verify_links and unique:
            st.write(f"Checking {len(unique)} destination links")
            unique = verify(unique)
        df = pd.DataFrame(unique)
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