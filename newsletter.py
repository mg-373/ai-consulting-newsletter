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
    "https://www.technologyreview.com/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://arstechnica.com/ai/feed/",
    "https://www.artificialintelligence-news.com/feed/",
]

CONSULTING_FEEDS = [
    "https://hbr.org/feed",
    "https://www.mckinsey.com/insights/rss",
    "https://www.consultancy.uk/rss",
    "https://www.bain.com/rss/insights/",
]

DOCS_DIR = "docs"
ARCHIVE_DIR = os.path.join(DOCS_DIR, "archive")

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
    "Other": "#525252",
}


# ---------------------------------------------------------------------
# 2. FETCH RECENT ITEMS
# ---------------------------------------------------------------------
def fetch_recent_items(feed_urls, hours=24):
    """Return a list of dicts for items published within the last
    `hours` hours. Silently skips broken feeds."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
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

    for attempt in range(4):
        response = requests.post(
            url,
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        if response.status_code == 429:
            wait_seconds = 20 * (attempt + 1)
            print(f"Got 429 (rate limited), waiting {wait_seconds}s and retrying...")
            last_response = response
            time.sleep(wait_seconds)
            continue
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    last_response.raise_for_status()


def build_newsletter_data(ai_items, consulting_items, today_str, name, topics):
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
You are given raw headlines/snippets from the last 24 hours, split into AI news and
Consulting industry news.

{greeting_instruction}
{topics_instruction}

Respond with ONLY valid JSON (no markdown fences, no commentary before or after),
matching exactly this shape:

{{
  "greeting": "one short punchy sentence to open the newsletter",
  "hero": {{
    "title": "the single most important story across both topics",
    "summary": "2-3 sentences explaining it and why it matters",
    "link": "url or empty string",
    "source": "publication name",
    "category": "one of: {category_list}"
  }},
  "ai_stories": [
    {{"title": "...", "summary": "1-2 sentences", "link": "url or empty string", "source": "...", "category": "one of: {category_list}"}}
  ],
  "consulting_stories": [
    {{"title": "...", "summary": "1-2 sentences", "link": "url or empty string", "source": "...", "category": "one of: {category_list}"}}
  ],
  "closer": "one sentence recommending the single best story to read in full, or empty string if nothing stands out"
}}

Rules:
- Do NOT include the hero story again inside ai_stories or consulting_stories.
- Include 4-7 stories in ai_stories and 4-7 in consulting_stories (fewer is fine if
  there genuinely isn't more real material — never pad with filler).
- Do not invent facts. Only use what's provided below.
- If a whole section has no real items, return an empty array for it.
- Keep summaries concise — this must be readable in under 3 minutes total.

RAW AI ITEMS:
{format_items(ai_items)}

RAW CONSULTING ITEMS:
{format_items(consulting_items)}
"""

    raw = call_gemini(prompt, api_key)

    # Gemini sometimes wraps JSON in ```json fences despite instructions — strip them.
    cleaned = re.sub(r"^```json\s*|^```\s*|```\s*$", "", raw.strip(), flags=re.MULTILINE).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        print("Failed to parse Gemini's JSON response. Raw output was:")
        print(raw)
        raise e


# ---------------------------------------------------------------------
# 4. HTML BUILDING HELPERS
# ---------------------------------------------------------------------
def tag_badge(category):
    color = CATEGORY_COLORS.get(category, CATEGORY_COLORS["Other"])
    safe_category = html_lib.escape(category or "Other")
    return f'<span class="tag" style="background:{color}22;color:{color};border:1px solid {color}55;">{safe_category}</span>'


def story_card(story, is_hero=False):
    title = html_lib.escape(story.get("title", ""))
    summary = html_lib.escape(story.get("summary", ""))
    source = html_lib.escape(story.get("source", ""))
    link = story.get("link", "") or ""
    category = story.get("category", "Other")

    title_html = f'<a href="{html_lib.escape(link)}">{title}</a>' if link else title
    css_class = "hero-card" if is_hero else "story-card"

    return f"""<div class="{css_class}">
  <div class="story-meta">{tag_badge(category)}<span class="source">{source}</span></div>
  <h3 class="story-title">{title_html}</h3>
  <p class="story-summary">{summary}</p>
</div>"""


def estimate_read_minutes(data):
    text_parts = [data.get("greeting", ""), data.get("closer", "")]
    hero = data.get("hero", {})
    text_parts.append(hero.get("title", ""))
    text_parts.append(hero.get("summary", ""))
    for story in data.get("ai_stories", []) + data.get("consulting_stories", []):
        text_parts.append(story.get("title", ""))
        text_parts.append(story.get("summary", ""))

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
<style>
  :root {{
    --bg: #fafafa;
    --text: #1a1a1a;
    --muted: #666;
    --border: #ddd;
    --card-bg: #ffffff;
    --link: #0b5fff;
  }}
  html[data-theme="dark"] {{
    --bg: #14161a;
    --text: #eaeaea;
    --muted: #9a9a9a;
    --border: #2c2f36;
    --card-bg: #1d2027;
    --link: #6ea8ff;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    margin: 0;
    color: var(--text);
    background: var(--bg);
    line-height: 1.55;
    transition: background 0.2s, color 0.2s;
  }}
  .layout {{
    max-width: 1120px;
    margin: 0 auto;
    padding: 32px 24px 80px;
    display: grid;
    grid-template-columns: 1fr;
    gap: 32px;
  }}
  @media (min-width: 960px) {{
    .layout {{
      grid-template-columns: minmax(0, 700px) 300px;
      align-items: start;
    }}
  }}
  .main {{ min-width: 0; }}
  .sidebar {{
    display: flex;
    flex-direction: column;
    gap: 18px;
  }}
  @media (min-width: 960px) {{
    .sidebar {{ position: sticky; top: 24px; }}
  }}
  .widget {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 18px;
  }}
  .widget h3 {{
    margin: 0 0 12px;
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
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
  .category-count {{
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }}
  .stat-number {{
    font-size: 1.6rem;
    font-weight: 700;
    margin: 0;
  }}
  .stat-label {{
    color: var(--muted);
    font-size: 0.82rem;
    margin: 2px 0 0;
  }}
  .stat-pair {{
    display: flex;
    gap: 20px;
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
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    border-bottom: 3px solid var(--text);
    padding-bottom: 14px;
    margin-bottom: 20px;
    gap: 12px;
  }}
  header h1 {{
    font-size: 1.4rem;
    margin: 0 0 4px;
  }}
  header .date {{
    color: var(--muted);
    font-size: 0.9rem;
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
  }}
  .meta-line {{
    color: var(--muted);
    font-size: 0.85rem;
    margin-bottom: 24px;
  }}
  .greeting {{
    font-size: 1.05rem;
    margin-bottom: 28px;
  }}
  h2 {{
    font-size: 1.1rem;
    margin-top: 36px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 6px;
  }}
  .hero-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    margin: 16px 0 32px;
  }}
  .hero-card .story-title {{
    font-size: 1.25rem;
  }}
  .story-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 12px;
  }}
  .story-meta {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }}
  .tag {{
    font-size: 0.72rem;
    font-weight: 600;
    padding: 2px 9px;
    border-radius: 20px;
    text-transform: uppercase;
    letter-spacing: 0.03em;
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
  a {{ color: var(--link); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .closer {{
    margin-top: 28px;
    font-style: italic;
    color: var(--muted);
  }}
</style>
</head>
<body>
<div class="layout">
<div class="main">
<header>
  <div>
    <h1>{header_title}</h1>
    <div class="date">{date_title}</div>
  </div>
  <button id="theme-toggle" onclick="toggleTheme()">🌙 Dark mode</button>
</header>

<div class="meta-line">{read_minutes} min read · <a href="feed.xml">RSS feed</a></div>

<p class="greeting">{greeting}</p>

{hero_html}

<h2>🤖 AI</h2>
{ai_html}

<h2>📊 Consulting</h2>
{consulting_html}

{closer_html}
</div>

<aside class="sidebar">
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
</script>
</body>
</html>
"""


def build_category_breakdown_html(data):
    hero = data.get("hero") or {}
    all_stories = ([hero] if hero.get("title") else []) + (data.get("ai_stories") or []) + (data.get("consulting_stories") or [])

    counts = {}
    for story in all_stories:
        category = story.get("category") or "Other"
        counts[category] = counts.get(category, 0) + 1

    if not counts:
        return "<p style=\"font-size:0.88rem;color:var(--muted);margin:0;\">No stories today.</p>"

    rows = []
    for category, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        rows.append(
            f'<div class="category-row">{tag_badge(category)}<span class="category-count">{count}</span></div>'
        )
    return "\n".join(rows)


def render_html(data, date_title, archive_link_items, name, week_ai_count, week_consulting_count):
    header_title_plain = f"{name}'s AI & Consulting Daily" if name else "AI & Consulting Daily"
    page_title = f"{header_title_plain} — {date_title}"
    archive_html = "\n".join(archive_link_items) if archive_link_items else "<li>No past editions yet.</li>"

    hero = data.get("hero") or {}
    hero_html = story_card(hero, is_hero=True) if hero.get("title") else ""

    ai_stories = data.get("ai_stories") or []
    consulting_stories = data.get("consulting_stories") or []
    ai_html = "\n".join(story_card(s) for s in ai_stories) or "<p>No notable AI stories in the last 24 hours.</p>"
    consulting_html = "\n".join(story_card(s) for s in consulting_stories) or "<p>No notable consulting stories in the last 24 hours.</p>"

    closer = data.get("closer", "")
    closer_html = f'<p class="closer">{html_lib.escape(closer)}</p>' if closer else ""

    read_minutes = estimate_read_minutes(data)
    category_breakdown_html = build_category_breakdown_html(data)

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
        closer_html=closer_html,
        category_breakdown_html=category_breakdown_html,
        week_ai_count=week_ai_count,
        week_consulting_count=week_consulting_count,
        archive_links=archive_html,
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

    today = datetime.date.today()
    today_str = today.strftime("%A, %B %d, %Y")
    today_slug = today.strftime("%Y-%m-%d")

    print("Fetching AI feeds...")
    ai_items = fetch_recent_items(AI_FEEDS)
    print(f"Found {len(ai_items)} AI items")

    print("Fetching consulting feeds...")
    consulting_items = fetch_recent_items(CONSULTING_FEEDS)
    print(f"Found {len(consulting_items)} consulting items")

    print("Asking Gemini to structure the newsletter...")
    data = build_newsletter_data(ai_items, consulting_items, today_str, name, topics)

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
    for m in past_manifest_entries:
        try:
            m_date = datetime.datetime.strptime(m["date_slug"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        if m_date >= week_cutoff:
            week_ai_count += m.get("ai_count", 0)
            week_consulting_count += m.get("consulting_count", 0)

    page_html = render_html(data, today_str, archive_link_items, name, week_ai_count, week_consulting_count)

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

    print("Done! Wrote", index_path, archive_path, "and feed.xml")
