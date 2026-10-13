# Growth Opportunity Finder

A free-first Streamlit research tool for finding places to promote a business or website. Enter a business/website, target location, and optional keyword. It looks for local citations, niche directories, communities, Q&A platforms, content platforms, guest-post opportunities, PR, associations, events/podcasts, reviews, and paid advertising/sponsorship options. It deliberately does not run competitor research.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Open the local URL printed by Streamlit (usually `http://localhost:8501`). Internet access is needed for live search and destination checks.

## Free mode (default)

- Leave the Anthropic API key blank.
- The app uses DuckDuckGo search when available, a rule-based classifier, and `seed_sources.json` as a curated starter list.
- This mode does not require paid AI credits. Search availability/rate limits depend on the search provider and network.

## Optional AI enrichment

Install the optional SDK and provide a key:

```bash
pip install anthropic
```

Set `ANTHROPIC_API_KEY` as an environment variable or enter it in the app sidebar. The app also accepts an optional `TAVILY_API_KEY` for search. API use may incur charges; check current provider pricing before using it.

## Important limitations

- Cost, relevance, link attributes, and submission rules are best-effort indicators. Confirm them on the destination site before acting or paying.
- A successful HTTP response does not prove a source accepts listings or that it is free.
- Some websites block automated search or link checks. A failed check can mean blocked automation rather than a dead site.
- `seed_sources.json` is a starter list, not an exhaustive or guaranteed-current directory database.
- Do not mass-submit duplicate promotional content or assume backlinks improve rankings.
