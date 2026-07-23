"""
AI & Consulting Daily Newsletter — webpage version (upgraded)
----------------------------------------------------------------
Fetches the last 24 hours of headlines from credible AI and consulting
sources, asks Gemini to structure them into a newsletter, and publishes
it as a webpage (docs/index.html) with:
  - a "story of the day" hero section
  - colour-coded category tags per story
  - topic weighting toward things you say you care about (optional)
  - a dark mode toggle
  - an RSS feed of past editions (docs/feed.xml)
  - a read-time estimate and weekly stats footer
  - an archive of past editions

You should not need to edit this file. Everything that changes between
setups is read from GitHub secrets: GEMINI_API_KEY (required),
YOUR_NAME (optional), TOPICS (optional).
"""

import os
import re
import json
import glob
import time
import datetime
import html as html_lib

import feedparser
import requests

# ---------------------------------------------------------------------
# 1. SOURCES — feel free to add/remove RSS feeds later. If a feed URL
#    is wrong or a site is briefly down, the script just skips it and
#    keeps going, so it's safe to experiment.
# ---------------------------------------------------------------------
AI_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://arstechnica.com/ai/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://openai.com/news/rss.xml",
    "https://blog.google/technology/ai/rss/",
    "https://www.marktechpost.com/feed/",
    "https://huggingface.co/blog/feed.xml",
    "https://www.artificialintelligence-news.com/feed/",
]

CONSULTING_FEEDS = [
    "https://hbr.org/feed",
    "https://www.mckinsey.com/insights/rss",
    "https://www.bain.com/rss/insights/",
    "https://www.consultancy.uk/rss",
    "https://www.strategy-business.com/all_updates.xml",
    "https://www.ft.com/companies/professional-services?format=rss",
    "https://www.managementtoday.co.uk/rss",
]

# Deliberately spans different editorial vantage points (UK public broadcaster,
# Qatar-based regional outlet, Israel-based outlet, independent UK paper) so
# coverage isn't filtered through only one narrative.
CONFLICT_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://www.timesofisrael.com/feed/",
    "https://www.theguardian.com/world/rss",
]

DOCS_DIR = "docs"
ARCHIVE_DIR = os.path.join(DOCS_DIR, "archive")
STATE_PATH = os.path.join(DOCS_DIR, "last_run_state.json")

# Safety net: if the workflow hasn't run successfully in a while (e.g. it was
# broken for a few days), don't pull an enormous backlog — cap how far back
# we'll ever look.
MAX_LOOKBACK_HOURS = 72

# Category tags Gemini can choose from, each with its own colour.
CATEGORY_COLORS = {
    "Funding": "#0b8a3e",
    "Product Launch": "#0b5fff",
    "Research": "#7a3fd6",
    "Policy & Regulation": "#c2410c",
    "M&A": "#b91c1c",
    "Partnership": "#0f766e",
    "Earnings": "#a16207",
    "Hiring & Leadership": "#4338ca",
    "Military Action": "#991b1b",
    "Diplomacy & Talks": "#1d4ed8",
    "Sanctions & Economy": "#92400e",
    "Humanitarian Impact": "#78350f",
    "Other": "#525252",
}


def load_last_run_cutoff():
    """Return the UTC datetime of the last successful run, or None if
    this is the first ever run. Also enforces MAX_LOOKBACK_HOURS so a
    long outage doesn't dump days of backlog into one edition."""
    now = datetime.datetime.now(datetime.timezone.utc)
    floor_cutoff = now - datetime.timedelta(hours=MAX_LOOKBACK_HOURS)

    if not os.path.exists(STATE_PATH):
        # First run ever — just look back 24 hours as a sensible default.
        return now - datetime.timedelta(hours=24)

    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        last_run = datetime.datetime.fromisoformat(state["last_run_utc"])
        return max(last_run, floor_cutoff)
    except Exception as e:
        print(f"Could not read last run state ({e}), defaulting to 24h lookback.")
        return now - datetime.timedelta(hours=24)


def save_last_run_state(run_time):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"last_run_utc": run_time.isoformat()}, f)


# ---------------------------------------------------------------------
# 2. FETCH RECENT ITEMS
# ---------------------------------------------------------------------
def fetch_recent_items(feed_urls, cutoff):
    """Return a list of dicts for items published after `cutoff` (a
    timezone-aware UTC datetime). Silently skips broken feeds."""
    items = []

    for url in feed_urls:
        try:
            parsed = feedparser.parse(url)
            source_name = parsed.feed.get("title", url)

            for entry in parsed.entries:
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if not published:
                    continue
                pub_dt = datetime.datetime(*published[:6], tzinfo=datetime.timezone.utc)
                if pub_dt < cutoff:
                    continue

                items.append({
                    "source": source_name,
                    "title": entry.get("title", "").strip(),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:400],
                })
        except Exception as e:
            print(f"Skipping feed {url}: {e}")

    return items


# ---------------------------------------------------------------------
# 3. ASK GEMINI FOR STRUCTURED NEWSLETTER DATA
# ---------------------------------------------------------------------
def call_gemini(prompt, api_key):
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    last_response = None
    retryable_codes = {429, 500, 502, 503, 504}

    for attempt in range(5):
        try:
            response = requests.post(
                url,
                params={"key": api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=60,
            )
        except requests.exceptions.RequestException as e:
            # Network-level hiccup (timeout, connection reset, etc.) — also worth retrying.
            wait_seconds = 15 * (attempt + 1)
            print(f"Request failed ({e}), waiting {wait_seconds}s and retrying...")
            time.sleep(wait_seconds)
            continue

        if response.status_code in retryable_codes:
            wait_seconds = 15 * (attempt + 1)
            print(f"Got {response.status_code} (temporary issue on Google's side), "
                  f"waiting {wait_seconds}s and retrying...")
            last_response = response
            time.sleep(wait_seconds)
            continue

        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    if last_response is not None:
        last_response.raise_for_status()
    raise RuntimeError("Gemini request failed repeatedly (network errors), giving up.")


def build_newsletter_data(ai_items, consulting_items, conflict_items, today_str, name, topics):
    api_key = os.environ["GEMINI_API_KEY"]
    category_list = ", ".join(f'"{c}"' for c in CATEGORY_COLORS)

    def format_items(items):
        if not items:
            return "No notable items found in the last 24 hours."
        lines = []
        for i in items:
            lines.append(f"- [{i['source']}] {i['title']} — {i['summary']} ({i['link']})")
        return "\n".join(lines)

    greeting_instruction = (
        f'Address the reader by name ("{name}") naturally in the greeting, e.g. "Morning, {name} —".'
        if name else
        "Keep the greeting short and friendly, no name needed."
    )

    topics_instruction = (
        f'The reader has told you they especially care about: {topics}. '
        f'When genuinely relevant stories exist, prioritize them in ordering and '
        f'strongly prefer one as the hero. Do not force a connection if nothing '
        f'in today\'s items actually relates — never fabricate relevance.'
        if topics else
        "No specific topic preferences given — just pick the most objectively important stories."
    )

    prompt = f"""You are writing a daily newsletter called "AI & Consulting Daily" for {today_str}.
You are given raw headlines/snippets from the last 24 hours, covering three topics: AI news,
Consulting industry news, and developments in the Israel-Iran-US conflict.

{greeting_instruction}
{topics_instruction}

The hero story (single most important story of the day) should be chosen from AI or
Consulting only — the conflict section is reported separately below and should not be
selected as the hero, since it deserves its own dedicated, careful treatment rather than
competing for the top spot.

Respond with ONLY valid JSON (no markdown fences, no commentary before or after),
matching exactly this shape:

{{
  "greeting": "one short punchy sentence to open the newsletter",
  "hero": {{
    "title": "the single most important AI or Consulting story",
    "summary": "2-3 sentences explaining it and why it matters",
    "link": "url or empty string",
    "source": "publication name",
    "category": "one of: {category_list}",
    "context": ["2-4 short bullets giving objective background on WHY this is happening"],
    "knock_on_effects": ["2-3 short bullets on plausible secondary/downstream effects"]
  }},
  "ai_stories": [
    {{"title": "...", "summary": "1-2 sentences", "link": "url or empty string", "source": "...", "category": "one of: {category_list}", "context": ["2-4 short bullets"], "knock_on_effects": ["2-3 short bullets"]}}
  ],
  "consulting_stories": [
    {{"title": "...", "summary": "1-2 sentences", "link": "url or empty string", "source": "...", "category": "one of: {category_list}", "context": ["2-4 short bullets"], "knock_on_effects": ["2-3 short bullets"]}}
  ],
  "conflict_stories": [
    {{"title": "...", "summary": "1-2 sentences, strictly factual", "link": "url or empty string", "source": "...", "category": "one of: {category_list}", "context": ["2-4 short bullets"], "knock_on_effects": ["2-3 short bullets on broader implications"]}}
  ],
  "closer": "one sentence recommending the single best AI or Consulting story to read in full, or empty string if nothing stands out"
}}

Rules:
- Do NOT include the hero story again inside ai_stories or consulting_stories.
- Include 4-7 stories in ai_stories and 4-7 in consulting_stories (fewer is fine if
  there genuinely isn't more real material — never pad with filler).
- Include up to 5 stories in conflict_stories — only genuinely significant
  developments, not every minor update. Empty array if nothing substantive happened.
- Do not invent facts. Only use what's provided below.
- If a whole section has no real items, return an empty array for it.
- Keep summaries concise — this must be readable in under 3 minutes total.
- For "context" on every story: write 2-4 short bullets (each under 20 words) that
  give the reader a holistic, objective understanding of WHY this is happening —
  root causes, incentives, or background the headline alone doesn't explain. Where
  there is genuine disagreement or multiple angles (e.g. company vs. critics vs.
  regulators vs. competitors), briefly represent the different viewpoints neutrally
  rather than picking a side. Stay factual — do not speculate beyond what a
  well-informed, neutral analyst could reasonably infer from the story itself.
- For "knock_on_effects" on every story: write 2-3 short bullets (each under 20
  words) on plausible secondary or downstream effects. Clearly signal these are
  plausible/likely outcomes, not confirmed facts (e.g. "could pressure...", "may
  prompt...", "likely to..."). Never state a knock-on effect as if it has already
  happened. If a story is too minor for meaningful knock-on effects, return an
  empty array rather than inventing weak ones.

ADDITIONAL RULES SPECIFIC TO conflict_stories — follow these strictly:
- Report only what named sources actually state; attribute claims to their source
  (e.g. "Israeli officials said...", "Iranian state media reported...", "the AP
  reports...") rather than stating contested claims as settled fact.
- Where casualty figures, attributions of responsibility, or accounts of events are
  disputed or unverified, say so explicitly and give the differing figures/accounts
  rather than picking one.
- Do not use loaded, one-sided, or emotive language for any party. Describe actions
  factually (what was reported to have happened) rather than characterizing motives.
- "context" bullets should explain the background neutrally (e.g. relevant history,
  stated positions of each side) without endorsing any party's framing.
- "knock_on_effects" here should focus on genuine implications — diplomatic,
  economic, regional security, or humanitarian — phrased as possibilities, not
  predictions of fact.
- Do not include graphic descriptions of violence, injuries, or casualties beyond
  what is necessary for factual reporting (e.g. reported death tolls are fine;
  graphic physical detail is not).
- If sources conflict, sit with that ambiguity rather than resolving it yourself.

RAW AI ITEMS:
{format_items(ai_items)}

RAW CONSULTING ITEMS:
{format_items(consulting_items)}

RAW CONFLICT ITEMS:
{format_items(conflict_items)}
"""

    last_error = None
    for attempt in range(2):
        raw = call_gemini(prompt, api_key)
        try:
            return parse_json_response(raw)
        except json.JSONDecodeError as e:
            last_error = e
            print(f"Attempt {attempt + 1}: failed to parse Gemini's JSON response ({e}). Raw output was:")
            print(raw)
            if attempt == 0:
                print("Retrying with a fresh request to Gemini...")

    raise last_error


def parse_json_response(raw):
    """Robustly extract a JSON object from an LLM response, tolerating
    code fences, stray leading/trailing characters, or extra commentary
    the model adds despite instructions not to."""
    text = raw.strip()

    # Strip a leading/trailing code fence if present (```json ... ``` or ``` ... ```)
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    text = text.strip()

    # Belt and braces: slice from the first '{' to the matching last '}',
    # which discards any stray characters/commentary outside the JSON object
    # (this is what actually failed before — a trailing stray character after
    # the closing fence was being fed into json.loads).
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        text = text[first_brace:last_brace + 1]

    return json.loads(text)


# ---------------------------------------------------------------------
# 4. HTML BUILDING HELPERS
# ---------------------------------------------------------------------
def tag_badge(category):
    color = CATEGORY_COLORS.get(category, CATEGORY_COLORS["Other"])
    safe_category = html_lib.escape(category or "Other")
    return f'<span class="tag" style="background:{color}22;color:{color};border:1px solid {color}55;--glow:{color};">{safe_category}</span>'


def story_card(story, is_hero=False):
    title = html_lib.escape(story.get("title", ""))
    summary = html_lib.escape(story.get("summary", ""))
    source = html_lib.escape(story.get("source", ""))
    link = story.get("link", "") or ""
    category = story.get("category", "Other")
    context = story.get("context") or []
    knock_on_effects = story.get("knock_on_effects") or []

    title_html = f'<a href="{html_lib.escape(link)}">{title}</a>' if link else title
    css_class = "hero-card" if is_hero else "story-card"

    def details_block(css_name, label, bullet_list):
        bullets = "\n".join(f"<li>{html_lib.escape(b)}</li>" for b in bullet_list if b)
        if not bullets:
            return ""
        return f"""<details class="{css_name}">
  <summary>{label}</summary>
  <ul>{bullets}</ul>
</details>"""

    context_html = details_block("context", "Why this is happening", context)
    knock_on_html = details_block("knock-on", "Possible knock-on effects", knock_on_effects)

    return f"""<div class="{css_class}">
  <div class="story-meta">{tag_badge(category)}<span class="source">{source}</span></div>
  <h3 class="story-title">{title_html}</h3>
  <p class="story-summary">{summary}</p>
  {context_html}
  {knock_on_html}
</div>"""


def estimate_read_minutes(data):
    text_parts = [data.get("greeting", ""), data.get("closer", "")]
    hero = data.get("hero", {})
    text_parts.append(hero.get("title", ""))
    text_parts.append(hero.get("summary", ""))
    text_parts.extend(hero.get("context") or [])
    text_parts.extend(hero.get("knock_on_effects") or [])
    for story in data.get("ai_stories", []) + data.get("consulting_stories", []) + data.get("conflict_stories", []):
        text_parts.append(story.get("title", ""))
        text_parts.append(story.get("summary", ""))
        text_parts.extend(story.get("context") or [])
        text_parts.extend(story.get("knock_on_effects") or [])

    word_count = sum(len(t.split()) for t in text_parts if t)
    minutes = max(1, round(word_count / 200))
    return minutes


# ---------------------------------------------------------------------
# 5. PAGE TEMPLATE
# ---------------------------------------------------------------------
PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{page_title}</title>
<link rel="alternate" type="application/rss+xml" title="{header_title_plain} RSS Feed" href="feed.xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #f5f5f1;
    --text: #14161a;
    --muted: #6b7075;
    --border: #e3e2dc;
    --card-bg: #ffffff;
    --link: #0f8c82;
    --accent-a: #0f8c82;
    --accent-b: #5b4fe8;
    --accent-c: #c08a1f;
    --shadow: rgba(20, 20, 30, 0.09);
    --shadow-rest: rgba(20, 20, 30, 0.05);
    --grad-a: rgba(15, 140, 130, 0.20);
    --grad-b: rgba(91, 79, 232, 0.16);
    --grad-c: rgba(192, 138, 31, 0.16);
    --font-display: "Fraunces", Georgia, "Times New Roman", serif;
    --font-body: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --font-mono: "IBM Plex Mono", "SF Mono", Consolas, monospace;
  }}
  html[data-theme="dark"] {{
    --bg: #0c0e12;
    --text: #ecebe7;
    --muted: #8b9097;
    --border: #262a31;
    --card-bg: #161920;
    --link: #3fc4b5;
    --accent-a: #3fc4b5;
    --accent-b: #9089ff;
    --accent-c: #e0a530;
    --shadow: rgba(0, 0, 0, 0.5);
    --shadow-rest: rgba(0, 0, 0, 0.3);
    --grad-a: rgba(63, 196, 181, 0.20);
    --grad-b: rgba(144, 137, 255, 0.16);
    --grad-c: rgba(224, 165, 48, 0.14);
  }}
  * {{ box-sizing: border-box; }}
  html {{
    background: var(--bg);
  }}
  body {{
    font-family: var(--font-body);
    margin: 0;
    color: var(--text);
    line-height: 1.6;
    transition: background-color 0.2s, color 0.2s;
    min-height: 100vh;
    background-color: var(--bg);
    background-image:
      radial-gradient(circle at 15% 20%, var(--grad-a), transparent 42%),
      radial-gradient(circle at 85% 15%, var(--grad-b), transparent 42%),
      radial-gradient(circle at 50% 90%, var(--grad-c), transparent 45%);
    background-repeat: no-repeat;
    background-size: 160% 160%, 160% 160%, 160% 160%;
    background-attachment: fixed, fixed, fixed;
    animation: driftGradient 22s ease-in-out infinite alternate;
  }}
  @keyframes driftGradient {{
    0%   {{ background-position: 0% 10%, 100% 0%, 40% 100%; }}
    50%  {{ background-position: 25% 45%, 70% 40%, 60% 65%; }}
    100% {{ background-position: 55% 75%, 40% 80%, 20% 30%; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    body {{ animation: none; }}
  }}
  .layout {{
    max-width: 1120px;
    margin: 0 auto;
    padding: 32px 24px 80px;
    display: grid;
    grid-template-columns: 1fr;
    gap: 28px;
  }}
  .main {{ min-width: 0; grid-column: 1; grid-row: 1; }}
  .sidebar-chat, .sidebar-stats {{
    display: flex;
    flex-direction: column;
    gap: 18px;
    min-width: 0;
    grid-column: 1;
  }}
  .sidebar-chat {{ grid-row: 2; }}
  .sidebar-stats {{ grid-row: 3; }}

  /* Two columns: news + a right rail (chat on top, stats below) */
  @media (min-width: 960px) {{
    .layout {{
      grid-template-columns: minmax(0, 700px) 300px;
      align-items: start;
    }}
    .main {{ grid-column: 1; grid-row: 1; }}
    .sidebar-chat {{ grid-column: 2; grid-row: 1; position: sticky; top: 24px; }}
    .sidebar-stats {{ grid-column: 2; grid-row: 2; }}
    /* No chat widget configured -> stats simply takes the top of the column */
    .layout:not(:has(.sidebar-chat)) .sidebar-stats {{ grid-row: 1; }}
  }}
  /* Wide screens with the assistant enabled: give it its own dedicated column */
  @media (min-width: 1340px) {{
    .layout:has(.sidebar-chat) {{
      max-width: 1340px;
      grid-template-columns: 300px minmax(0, 680px) 300px;
    }}
    .layout:has(.sidebar-chat) .sidebar-chat {{ grid-column: 1; grid-row: 1; position: sticky; top: 24px; }}
    .layout:has(.sidebar-chat) .main {{ grid-column: 2; grid-row: 1; }}
    .layout:has(.sidebar-chat) .sidebar-stats {{ grid-column: 3; grid-row: 1; position: sticky; top: 24px; }}
    .layout:has(.sidebar-chat) .chat-messages {{ max-height: 480px; }}
  }}
  .widget {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 16px 18px;
    transition: transform 0.2s ease, box-shadow 0.2s ease;
  }}
  .widget:hover {{
    transform: translateY(-2px);
    box-shadow: 0 6px 16px var(--shadow);
  }}
  .widget h3 {{
    font-family: var(--font-mono);
    margin: 0 0 12px;
    font-size: 0.74rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
  }}
  .widget-list {{
    list-style: none;
    padding: 0;
    margin: 0;
  }}
  .widget-list li {{
    margin-bottom: 8px;
    font-size: 0.9rem;
  }}
  .widget-list li:last-child {{ margin-bottom: 0; }}
  .widget-archive {{
    max-height: 260px;
    overflow-y: auto;
  }}
  .category-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
    font-size: 0.88rem;
  }}
  .category-row:last-child {{ margin-bottom: 0; }}
  .bar-track {{
    width: 100%;
    height: 5px;
    border-radius: 3px;
    background: var(--border);
    overflow: hidden;
    margin: 4px 0 12px;
  }}
  .bar-fill {{
    height: 100%;
    width: 0%;
    border-radius: 3px;
    background: var(--bar-color, var(--link));
    animation: fillBar 1.1s ease forwards;
    animation-delay: 0.15s;
  }}
  @keyframes fillBar {{
    from {{ width: 0%; }}
    to {{ width: var(--bar-pct, 0%); }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .bar-fill {{ animation: none; width: var(--bar-pct, 0%); }}
  }}
  .category-count {{
    color: var(--muted);
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: 0.85rem;
  }}
  .stat-number {{
    font-family: var(--font-mono);
    font-size: 1.5rem;
    font-weight: 600;
    margin: 0;
  }}
  .stat-label {{
    color: var(--muted);
    font-size: 0.74rem;
    margin: 2px 0 0;
  }}
  .stat-pair {{
    display: flex;
    gap: 12px;
  }}
  .stat-pair > div {{
    flex: 1;
    min-width: 0;
  }}
  .subscribe-note {{
    font-size: 0.85rem;
    color: var(--muted);
    margin: 0 0 12px;
  }}
  .rss-link {{
    display: inline-block;
    font-size: 0.85rem;
    font-weight: 600;
  }}
  header {{
    padding-bottom: 18px;
    margin-bottom: 22px;
    position: relative;
  }}
  header::after {{
    content: "";
    display: block;
    height: 3px;
    margin-top: 16px;
    border-radius: 3px;
    background: linear-gradient(90deg, var(--accent-a), var(--accent-b), var(--accent-c));
  }}
  .header-top {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
  }}
  .eyebrow {{
    display: flex;
    align-items: center;
    gap: 7px;
    font-family: var(--font-mono);
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--accent-a);
    margin-bottom: 8px;
  }}
  .eyebrow .dot {{
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--accent-a);
    animation: pulseDot 2.2s ease-in-out infinite;
  }}
  @keyframes pulseDot {{
    0%, 100% {{ opacity: 1; box-shadow: 0 0 0 0 var(--grad-a); }}
    50% {{ opacity: 0.65; box-shadow: 0 0 0 5px transparent; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .eyebrow .dot {{ animation: none; }}
  }}
  header h1 {{
    font-family: var(--font-display);
    font-weight: 600;
    font-size: 2rem;
    letter-spacing: -0.01em;
    margin: 0 0 6px;
    line-height: 1.15;
  }}
  header .date {{
    font-family: var(--font-mono);
    color: var(--muted);
    font-size: 0.85rem;
  }}
  #theme-toggle {{
    border: 1px solid var(--border);
    background: var(--card-bg);
    color: var(--text);
    border-radius: 20px;
    padding: 6px 14px;
    font-size: 0.85rem;
    cursor: pointer;
    white-space: nowrap;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
  }}
  #theme-toggle:hover {{
    transform: translateY(-1px);
    box-shadow: 0 4px 12px var(--shadow-rest);
  }}
  .meta-line {{
    font-family: var(--font-mono);
    color: var(--muted);
    font-size: 0.82rem;
    margin-bottom: 26px;
  }}
  .greeting {{
    font-size: 1.08rem;
    margin-bottom: 28px;
  }}
  h2 {{
    font-family: var(--font-display);
    font-weight: 600;
    font-size: 1.35rem;
    margin-top: 40px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 8px;
  }}
  .hero-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent-a);
    border-radius: 4px 14px 14px 4px;
    padding: 22px 24px;
    margin: 16px 0 32px;
    transition: transform 0.25s ease, box-shadow 0.25s ease;
  }}
  .hero-card:hover {{
    transform: translateY(-3px);
    box-shadow: 0 10px 28px var(--shadow);
  }}
  .hero-card .story-title {{
    font-family: var(--font-display);
    font-weight: 600;
    font-size: 1.4rem;
  }}
  .story-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 14px 16px;
    margin-bottom: 12px;
    transition: transform 0.2s ease, box-shadow 0.2s ease;
  }}
  .story-card:hover {{
    transform: translateY(-2px);
    box-shadow: 0 6px 18px var(--shadow);
  }}
  .story-meta {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }}
  .tag {{
    font-family: var(--font-mono);
    font-size: 0.7rem;
    font-weight: 600;
    padding: 2px 9px;
    border-radius: 20px;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    animation: tagGlow 3.5s ease-in-out infinite;
  }}
  @keyframes tagGlow {{
    0%, 100% {{ box-shadow: 0 0 0 rgba(0,0,0,0); }}
    50% {{ box-shadow: 0 0 7px var(--glow); }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .tag {{ animation: none; }}
  }}
  .source {{
    color: var(--muted);
    font-size: 0.8rem;
  }}
  .story-title {{
    margin: 0 0 6px;
    font-size: 1rem;
  }}
  .story-summary {{
    margin: 0;
    color: var(--text);
    font-size: 0.95rem;
  }}
  .context {{
    margin-top: 10px;
  }}
  .context summary {{
    cursor: pointer;
    font-size: 0.83rem;
    font-weight: 600;
    color: var(--link);
    list-style: none;
  }}
  .context summary::-webkit-details-marker {{
    display: none;
  }}
  .context summary::before {{
    content: "▸ ";
  }}
  .context[open] summary::before {{
    content: "▾ ";
  }}
  .context ul {{
    margin: 8px 0 0;
    padding-left: 18px;
  }}
  .context li {{
    font-size: 0.88rem;
    color: var(--muted);
    margin-bottom: 5px;
  }}
  .context li:last-child {{ margin-bottom: 0; }}
  .knock-on {{
    margin-top: 8px;
  }}
  .knock-on summary {{
    cursor: pointer;
    font-size: 0.83rem;
    font-weight: 600;
    color: #a16207;
    list-style: none;
  }}
  html[data-theme="dark"] .knock-on summary {{
    color: #e0a530;
  }}
  .knock-on summary::-webkit-details-marker {{
    display: none;
  }}
  .knock-on summary::before {{
    content: "▸ ";
  }}
  .knock-on[open] summary::before {{
    content: "▾ ";
  }}
  .knock-on ul {{
    margin: 8px 0 0;
    padding-left: 18px;
  }}
  .knock-on li {{
    font-size: 0.88rem;
    color: var(--muted);
    margin-bottom: 5px;
  }}
  .knock-on li:last-child {{ margin-bottom: 0; }}
  a {{ color: var(--link); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .closer {{
    margin-top: 28px;
    font-style: italic;
    color: var(--muted);
  }}
  .section-note {{
    font-size: 0.82rem;
    color: var(--muted);
    margin: -8px 0 14px;
    font-style: italic;
  }}
  .widget-chat {{
    display: flex;
    flex-direction: column;
  }}
  .chat-messages {{
    max-height: 280px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-bottom: 10px;
    padding-right: 2px;
  }}
  .chat-msg {{
    padding: 8px 11px;
    border-radius: 12px;
    font-size: 0.85rem;
    line-height: 1.45;
    max-width: 92%;
  }}
  .chat-msg.user {{
    background: var(--link);
    color: #ffffff;
    align-self: flex-end;
    border-bottom-right-radius: 3px;
  }}
  .chat-msg.assistant {{
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    align-self: flex-start;
    border-bottom-left-radius: 3px;
  }}
  .chat-input-row {{
    display: flex;
    gap: 6px;
  }}
  #chat-input {{
    flex: 1;
    min-width: 0;
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--text);
    font-size: 0.85rem;
    font-family: inherit;
  }}
  #chat-input:focus {{
    outline: 2px solid var(--link);
    outline-offset: 1px;
  }}
  #chat-send {{
    padding: 8px 14px;
    border-radius: 8px;
    border: none;
    background: var(--link);
    color: #ffffff;
    font-size: 0.85rem;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.15s ease;
  }}
  #chat-send:hover {{ opacity: 0.85; }}
  #chat-send:disabled, #chat-input:disabled {{ opacity: 0.6; cursor: default; }}
  .chat-disclaimer {{
    font-size: 0.72rem;
    color: var(--muted);
    margin: 8px 0 0;
  }}
</style>
</head>
<body>
<div class="layout">
<div class="main">
<header>
  <div class="header-top">
    <div>
      <div class="eyebrow"><span class="dot"></span>Daily Briefing</div>
      <h1>{header_title}</h1>
      <div class="date">{date_title}</div>
    </div>
    <button id="theme-toggle" onclick="toggleTheme()">🌙 Dark mode</button>
  </div>
</header>

<div class="meta-line">{read_minutes} min read · <a href="feed.xml">RSS feed</a></div>

<p class="greeting">{greeting}</p>

{hero_html}

<h2>🤖 AI</h2>
{ai_html}

<h2>📊 Consulting</h2>
{consulting_html}

{conflict_section_html}

{closer_html}
</div>

{chat_widget_html}

<aside class="sidebar-stats">
  <div class="widget">
    <h3>This week</h3>
    <div class="stat-pair">
      <div>
        <p class="stat-number">{week_ai_count}</p>
        <p class="stat-label">AI stories</p>
      </div>
      <div>
        <p class="stat-number">{week_consulting_count}</p>
        <p class="stat-label">Consulting stories</p>
      </div>
      <div>
        <p class="stat-number">{week_conflict_count}</p>
        <p class="stat-label">Conflict updates</p>
      </div>
    </div>
  </div>

  <div class="widget">
    <h3>Today's mix</h3>
    {category_breakdown_html}
  </div>

  <div class="widget">
    <h3>Stay in the loop</h3>
    <p class="subscribe-note">Add this feed to any RSS reader to get each edition automatically.</p>
    <a class="rss-link" href="feed.xml">📡 Subscribe via RSS</a>
  </div>

  <div class="widget widget-archive">
    <h3>Past editions</h3>
    <ul class="widget-list">
    {archive_links}
    </ul>
  </div>
</aside>
</div>

<script>
  function applyTheme(theme) {{
    document.documentElement.setAttribute('data-theme', theme);
    document.getElementById('theme-toggle').textContent = theme === 'dark' ? '☀️ Light mode' : '🌙 Dark mode';
  }}
  function toggleTheme() {{
    const current = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    const next = current === 'dark' ? 'light' : 'dark';
    localStorage.setItem('theme', next);
    applyTheme(next);
  }}
  (function() {{
    const saved = localStorage.getItem('theme');
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    applyTheme(saved || (prefersDark ? 'dark' : 'light'));
  }})();

  // --- AI assistant (answers questions using today's stories as context) ---
  const CHAT_ENDPOINT = {chat_endpoint_js};
  const CHAT_CONTEXT = {chat_context_js};
  let chatHistory = [];

  function escapeHtml(str) {{
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }}

  function appendChatMessage(role, text) {{
    const container = document.getElementById('chat-messages');
    if (!container) return null;
    const el = document.createElement('div');
    el.className = 'chat-msg ' + role;
    el.innerHTML = escapeHtml(text).replace(/\\n/g, '<br>');
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
    return el;
  }}

  async function sendChatMessage() {{
    const input = document.getElementById('chat-input');
    const sendBtn = document.getElementById('chat-send');
    if (!input) return;
    const question = input.value.trim();
    if (!question) return;

    input.value = '';
    input.disabled = true;
    if (sendBtn) sendBtn.disabled = true;

    appendChatMessage('user', question);
    const thinkingEl = appendChatMessage('assistant', 'Thinking…');

    const isFirstMessage = chatHistory.length === 0;
    const messageText = isFirstMessage
      ? "You are a helpful assistant embedded in a daily AI & Consulting newsletter page. " +
        "Answer the reader's questions about today's stories in more depth than the newsletter " +
        "itself, using the context below. If a question goes beyond what's in today's stories, " +
        "say so briefly and answer from general knowledge if you reasonably can. Be concise but " +
        "substantive, and stay neutral/objective on contested topics.\\n\\nTODAY'S STORIES:\\n" +
        CHAT_CONTEXT + "\\n\\nReader's question: " + question
      : question;

    chatHistory.push({{ role: 'user', parts: [{{ text: messageText }}] }});

    try {{
      const response = await fetch(
        CHAT_ENDPOINT,
        {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ contents: chatHistory }})
        }}
      );
      const data = await response.json();
      const reply = (data.candidates && data.candidates[0] && data.candidates[0].content &&
                     data.candidates[0].content.parts && data.candidates[0].content.parts[0] &&
                     data.candidates[0].content.parts[0].text) ||
                     "Sorry, I couldn't generate a response just then — try again?";
      chatHistory.push({{ role: 'model', parts: [{{ text: reply }}] }});
      if (thinkingEl) {{
        thinkingEl.innerHTML = escapeHtml(reply).replace(/\\n/g, '<br>');
      }}
    }} catch (err) {{
      if (thinkingEl) {{
        thinkingEl.innerHTML = "Couldn't reach the assistant just now — check your connection and try again.";
      }}
      chatHistory.pop();
    }} finally {{
      input.disabled = false;
      if (sendBtn) sendBtn.disabled = false;
      input.focus();
      const container = document.getElementById('chat-messages');
      if (container) container.scrollTop = container.scrollHeight;
    }}
  }}

  document.addEventListener('DOMContentLoaded', function() {{
    const input = document.getElementById('chat-input');
    if (input) {{
      input.addEventListener('keydown', function(e) {{
        if (e.key === 'Enter') {{
          e.preventDefault();
          sendChatMessage();
        }}
      }});
    }}
  }});
</script>
</body>
</html>
"""


CHAT_WIDGET_HTML = """<aside class="sidebar-chat">
<div class="widget widget-chat">
  <h3>Ask about today's news</h3>
  <div id="chat-messages" class="chat-messages">
    <div class="chat-msg assistant">Ask me anything about today's stories — I'll go deeper than the summaries above.</div>
  </div>
  <div class="chat-input-row">
    <input id="chat-input" type="text" placeholder="e.g. Why does the sanctions story matter?" autocomplete="off">
    <button id="chat-send" onclick="sendChatMessage()">Ask</button>
  </div>
  <p class="chat-disclaimer">AI-generated answers based on today's stories — worth double-checking anything important.</p>
</div>
</aside>"""

CHAT_DISABLED_HTML = ""  # No CHAT_WORKER_URL set — assistant widget (and its whole column) is simply omitted.


def build_category_breakdown_html(data):
    hero = data.get("hero") or {}
    all_stories = ([hero] if hero.get("title") else []) + (data.get("ai_stories") or []) + (data.get("consulting_stories") or []) + (data.get("conflict_stories") or [])

    counts = {}
    for story in all_stories:
        category = story.get("category") or "Other"
        counts[category] = counts.get(category, 0) + 1

    if not counts:
        return "<p style=\"font-size:0.88rem;color:var(--muted);margin:0;\">No stories today.</p>"

    max_count = max(counts.values())
    rows = []
    for category, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        color = CATEGORY_COLORS.get(category, CATEGORY_COLORS["Other"])
        pct = round((count / max_count) * 100)
        rows.append(f"""<div class="category-row">
  {tag_badge(category)}<span class="category-count">{count}</span>
</div>
<div class="bar-track">
  <div class="bar-fill" style="--bar-color:{color};--bar-pct:{pct}%;"></div>
</div>""")
    return "\n".join(rows)


def build_chat_context_text(data, date_title):
    """A compact plain-text version of today's newsletter for the chat
    assistant to use as context — includes context/knock-on bullets so
    it can go deeper than the page itself, but skips HTML/formatting."""
    lines = [f"Edition date: {date_title}", ""]

    hero = data.get("hero") or {}
    if hero.get("title"):
        lines.append(f"TOP STORY: {hero.get('title')} (source: {hero.get('source', '')})")
        lines.append(f"Summary: {hero.get('summary', '')}")
        for b in hero.get("context") or []:
            lines.append(f"Background: {b}")
        for b in hero.get("knock_on_effects") or []:
            lines.append(f"Possible knock-on effect: {b}")
        lines.append("")

    def add_section(title, stories):
        if not stories:
            return
        lines.append(f"{title}:")
        for s in stories:
            lines.append(f"- {s.get('title', '')} (source: {s.get('source', '')})")
            lines.append(f"  Summary: {s.get('summary', '')}")
            for b in s.get("context") or []:
                lines.append(f"  Background: {b}")
            for b in s.get("knock_on_effects") or []:
                lines.append(f"  Possible knock-on effect: {b}")
        lines.append("")

    add_section("AI STORIES", data.get("ai_stories") or [])
    add_section("CONSULTING STORIES", data.get("consulting_stories") or [])
    add_section("ISRAEL-IRAN-US CONFLICT STORIES (report neutrally; attribute disputed claims to their source rather than stating them as fact)", data.get("conflict_stories") or [])

    return "\n".join(lines)


def render_html(data, date_title, archive_link_items, name, week_ai_count, week_consulting_count, week_conflict_count, chat_worker_url):
    header_title_plain = f"{name}'s AI & Consulting Daily" if name else "AI & Consulting Daily"
    page_title = f"{header_title_plain} — {date_title}"
    archive_html = "\n".join(archive_link_items) if archive_link_items else "<li>No past editions yet.</li>"

    hero = data.get("hero") or {}
    hero_html = story_card(hero, is_hero=True) if hero.get("title") else ""

    ai_stories = data.get("ai_stories") or []
    consulting_stories = data.get("consulting_stories") or []
    conflict_stories = data.get("conflict_stories") or []
    ai_html = "\n".join(story_card(s) for s in ai_stories) or "<p>No notable AI stories in the last 24 hours.</p>"
    consulting_html = "\n".join(story_card(s) for s in consulting_stories) or "<p>No notable consulting stories in the last 24 hours.</p>"

    if conflict_stories:
        conflict_html = "\n".join(story_card(s) for s in conflict_stories)
        conflict_section_html = f"""<h2>🌍 Israel-Iran-US Conflict</h2>
<p class="section-note">Reported factually from multiple outlets across different vantage points — see "Why this is happening" on each story for context, and note where accounts differ.</p>
{conflict_html}"""
    else:
        conflict_section_html = ""

    closer = data.get("closer", "")
    closer_html = f'<p class="closer">{html_lib.escape(closer)}</p>' if closer else ""

    read_minutes = estimate_read_minutes(data)
    category_breakdown_html = build_category_breakdown_html(data)

    chat_context_text = build_chat_context_text(data, date_title)
    # json.dumps produces a safely-escaped JS string literal (handles quotes,
    # newlines, unicode, etc.) — this is embedded directly into a <script> tag.
    # Note: the Worker URL itself is not a secret (the Worker enforces access
    # via CORS/origin checking, not by hiding this URL) — only the real
    # Gemini key, which lives only inside the Worker, needs to stay private.
    chat_endpoint_js = json.dumps(chat_worker_url or "")
    chat_context_js = json.dumps(chat_context_text)
    chat_enabled = bool(chat_worker_url)

    return PAGE_TEMPLATE.format(
        page_title=html_lib.escape(page_title),
        header_title=html_lib.escape(header_title_plain),
        header_title_plain=html_lib.escape(header_title_plain),
        date_title=html_lib.escape(date_title),
        read_minutes=read_minutes,
        greeting=html_lib.escape(data.get("greeting", "")),
        hero_html=hero_html,
        ai_html=ai_html,
        consulting_html=consulting_html,
        conflict_section_html=conflict_section_html,
        closer_html=closer_html,
        category_breakdown_html=category_breakdown_html,
        week_ai_count=week_ai_count,
        week_consulting_count=week_consulting_count,
        week_conflict_count=week_conflict_count,
        archive_links=archive_html,
        chat_endpoint_js=chat_endpoint_js,
        chat_context_js=chat_context_js,
        chat_widget_html=CHAT_WIDGET_HTML if chat_enabled else CHAT_DISABLED_HTML,
    )


# ---------------------------------------------------------------------
# 6. RSS FEED
# ---------------------------------------------------------------------
def build_rss_feed(manifest_entries, site_title):
    """manifest_entries: list of dicts with date_slug, date_title, hero_title, link (relative)."""
    items_xml = []
    for entry in manifest_entries[:30]:
        try:
            pub_date = datetime.datetime.strptime(entry["date_slug"], "%Y-%m-%d")
        except ValueError:
            continue
        pub_date_str = pub_date.strftime("%a, %d %b %Y 21:00:00 +0000")
        title = html_lib.escape(f"{entry['date_title']}: {entry.get('hero_title', '')}".strip(": "))
        link = entry["link"]
        items_xml.append(f"""  <item>
    <title>{title}</title>
    <link>{link}</link>
    <guid>{link}</guid>
    <pubDate>{pub_date_str}</pubDate>
  </item>""")

    items_block = "\n".join(items_xml)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>{html_lib.escape(site_title)}</title>
  <description>Daily AI and consulting industry news roundup</description>
{items_block}
</channel>
</rss>
"""


# ---------------------------------------------------------------------
# 7. MAIN
# ---------------------------------------------------------------------
if __name__ == "__main__":
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    name = os.environ.get("YOUR_NAME", "").strip()
    topics = os.environ.get("TOPICS", "").strip()
    chat_worker_url = os.environ.get("CHAT_WORKER_URL", "").strip()

    run_start_time = datetime.datetime.now(datetime.timezone.utc)
    today = datetime.date.today()
    today_str = today.strftime("%A, %B %d, %Y")
    today_slug = today.strftime("%Y-%m-%d")

    cutoff = load_last_run_cutoff()
    print(f"Fetching items published since {cutoff.isoformat()} (last successful run)...")

    print("Fetching AI feeds...")
    ai_items = fetch_recent_items(AI_FEEDS, cutoff)
    print(f"Found {len(ai_items)} AI items")

    print("Fetching consulting feeds...")
    consulting_items = fetch_recent_items(CONSULTING_FEEDS, cutoff)
    print(f"Found {len(consulting_items)} consulting items")

    print("Fetching conflict feeds...")
    conflict_items = fetch_recent_items(CONFLICT_FEEDS, cutoff)
    print(f"Found {len(conflict_items)} conflict items")

    print("Asking Gemini to structure the newsletter...")
    data = build_newsletter_data(ai_items, consulting_items, conflict_items, today_str, name, topics)

    # Gather existing manifest entries (before writing today's), newest first
    existing_manifests = sorted(
        glob.glob(os.path.join(ARCHIVE_DIR, "*.json")), reverse=True
    )
    manifest_entries = []
    for path in existing_manifests:
        try:
            with open(path, "r", encoding="utf-8") as f:
                manifest_entries.append(json.load(f))
        except Exception:
            continue

    def link_item(slug, label):
        return f'<li><a href="archive/{slug}.html">{html_lib.escape(label)}</a></li>'

    # Exclude any leftover manifest for *today* — if the workflow has already run
    # once today (e.g. manual test runs), we don't want to double count or
    # double-list it. Today's fresh data (computed above) is the source of truth.
    past_manifest_entries = [m for m in manifest_entries if m.get("date_slug") != today_slug]

    archive_link_items = [link_item(today_slug, today_str)]
    for m in past_manifest_entries[:20]:
        archive_link_items.append(link_item(m["date_slug"], m["date_title"]))

    # Weekly stats: today's fresh counts + last 6 days' manifest counts (today excluded above)
    week_cutoff = today - datetime.timedelta(days=6)
    week_ai_count = len(data.get("ai_stories") or [])
    week_consulting_count = len(data.get("consulting_stories") or [])
    week_conflict_count = len(data.get("conflict_stories") or [])
    for m in past_manifest_entries:
        try:
            m_date = datetime.datetime.strptime(m["date_slug"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        if m_date >= week_cutoff:
            week_ai_count += m.get("ai_count", 0)
            week_consulting_count += m.get("consulting_count", 0)
            week_conflict_count += m.get("conflict_count", 0)

    page_html = render_html(data, today_str, archive_link_items, name, week_ai_count, week_consulting_count, week_conflict_count, chat_worker_url)

    # Write today's archive copy
    archive_path = os.path.join(ARCHIVE_DIR, f"{today_slug}.html")
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write(page_html)

    # Write today's manifest (used for stats + RSS on future runs)
    hero_title = (data.get("hero") or {}).get("title", "")
    manifest = {
        "date_slug": today_slug,
        "date_title": today_str,
        "hero_title": hero_title,
        "ai_count": len(data.get("ai_stories") or []),
        "consulting_count": len(data.get("consulting_stories") or []),
        "conflict_count": len(data.get("conflict_stories") or []),
    }
    manifest_path = os.path.join(ARCHIVE_DIR, f"{today_slug}.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f)

    # Write/overwrite the homepage with today's edition
    index_path = os.path.join(DOCS_DIR, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(page_html)

    # Build RSS feed from today's + past manifests
    rss_entries = [{**manifest, "link": f"archive/{today_slug}.html"}]
    for m in manifest_entries:
        rss_entries.append({**m, "link": f"archive/{m['date_slug']}.html"})
    site_title = f"{name}'s AI & Consulting Daily" if name else "AI & Consulting Daily"
    rss_xml = build_rss_feed(rss_entries, site_title)
    with open(os.path.join(DOCS_DIR, "feed.xml"), "w", encoding="utf-8") as f:
        f.write(rss_xml)

    # Only mark this run as "successful" now that everything above completed —
    # if anything failed earlier, the timestamp stays at its last good value,
    # so next time we correctly catch up on everything missed in between.
    save_last_run_state(run_start_time)

    print("Done! Wrote", index_path, archive_path, "and feed.xml")
