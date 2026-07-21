"""
AI & Consulting Daily Newsletter — webpage version
----------------------------------------------------
Fetches the last 24 hours of headlines from credible AI and consulting
sources, asks Gemini to write a balanced-length newsletter, and publishes
it as a webpage (docs/index.html) with an archive of past editions.

GitHub Pages serves the docs/ folder, so once this script runs and the
workflow commits the result, your page is live automatically.

You should not need to edit this file. The only thing that changes
between setups is your Gemini API key, set as a GitHub secret.
"""

import os
import glob
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
# 3. ASK GEMINI TO WRITE THE NEWSLETTER
# ---------------------------------------------------------------------
def build_newsletter(ai_items, consulting_items, today_str):
    api_key = os.environ["GEMINI_API_KEY"]

    def format_items(items):
        if not items:
            return "No notable items found in the last 24 hours."
        lines = []
        for i in items:
            lines.append(f"- [{i['source']}] {i['title']} — {i['summary']} ({i['link']})")
        return "\n".join(lines)

    prompt = f"""You are writing a daily newsletter called "AI & Consulting Daily"
for {today_str}. You are given raw headlines/snippets from the last 24 hours,
split into two sections: AI news, and Consulting industry news.

Write a newsletter that:
- Has a short, punchy intro line (1 sentence).
- Has two clear sections: "AI" and "Consulting"
- Under each section, covers the most important 4-7 stories as short bullet
  points (1-2 sentences each) — enough detail to actually understand what
  happened and why it matters, but NOT long paragraphs. Skip minor/duplicate
  stories.
- Ends with a one-line "Worth a deeper look" pick if anything stands out.
- Keep the whole thing readable in under 3 minutes.
- Do not invent facts. Only use what's provided below. If a section has
  no real items, say so briefly instead of padding.
- Output valid HTML fragment only (no <html>/<head>/<body> tags, no
  markdown). Use <h2> for section headers, <p> for the intro/closing lines,
  and <ul><li> for bullet points. Wrap story titles in <strong>. Where a
  link is available, wrap the story title in an <a href="..."> tag.

RAW AI ITEMS:
{format_items(ai_items)}

RAW CONSULTING ITEMS:
{format_items(consulting_items)}
"""

    import time

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    last_error = None

    for attempt in range(4):
        response = requests.post(
            url,
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        if response.status_code == 429:
            # Rate-limited: wait a bit and try again rather than failing outright.
            wait_seconds = 20 * (attempt + 1)
            print(f"Got 429 (rate limited), waiting {wait_seconds}s and retrying...")
            last_error = response
            time.sleep(wait_seconds)
            continue
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    # If we got here, all retries were rate-limited.
    last_error.raise_for_status()


# ---------------------------------------------------------------------
# 4. RENDER THE HTML PAGE
# ---------------------------------------------------------------------
PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI & Consulting Daily — {date_title}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    max-width: 680px;
    margin: 0 auto;
    padding: 32px 20px 80px;
    color: #1a1a1a;
    line-height: 1.55;
    background: #fafafa;
  }}
  header {{
    border-bottom: 3px solid #1a1a1a;
    padding-bottom: 14px;
    margin-bottom: 28px;
  }}
  header h1 {{
    font-size: 1.5rem;
    margin: 0 0 4px;
  }}
  header .date {{
    color: #666;
    font-size: 0.95rem;
  }}
  h2 {{
    font-size: 1.15rem;
    margin-top: 32px;
    border-bottom: 1px solid #ddd;
    padding-bottom: 6px;
  }}
  ul {{
    padding-left: 20px;
  }}
  li {{
    margin-bottom: 10px;
  }}
  a {{
    color: #0b5fff;
    text-decoration: none;
  }}
  a:hover {{
    text-decoration: underline;
  }}
  footer {{
    margin-top: 48px;
    padding-top: 16px;
    border-top: 1px solid #ddd;
    font-size: 0.85rem;
    color: #777;
  }}
  footer h3 {{
    font-size: 0.9rem;
    color: #333;
    margin-bottom: 8px;
  }}
  footer ul {{
    padding-left: 0;
    list-style: none;
  }}
  footer li {{
    margin-bottom: 4px;
  }}
</style>
</head>
<body>
<header>
  <h1>AI &amp; Consulting Daily</h1>
  <div class="date">{date_title}</div>
</header>

{content}

<footer>
  <h3>Past editions</h3>
  <ul>
  {archive_links}
  </ul>
</footer>
</body>
</html>
"""


def render_html(content_fragment, date_title, archive_link_items):
    archive_html = "\n".join(archive_link_items) if archive_link_items else "<li>No past editions yet.</li>"
    return PAGE_TEMPLATE.format(
        date_title=html_lib.escape(date_title),
        content=content_fragment,
        archive_links=archive_html,
    )


# ---------------------------------------------------------------------
# 5. MAIN
# ---------------------------------------------------------------------
if __name__ == "__main__":
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    today = datetime.date.today()
    today_str = today.strftime("%A, %B %d, %Y")
    today_slug = today.strftime("%Y-%m-%d")

    print("Fetching AI feeds...")
    ai_items = fetch_recent_items(AI_FEEDS)
    print(f"Found {len(ai_items)} AI items")

    print("Fetching consulting feeds...")
    consulting_items = fetch_recent_items(CONSULTING_FEEDS)
    print(f"Found {len(consulting_items)} consulting items")

    print("Asking Gemini to write the newsletter...")
    content_fragment = build_newsletter(ai_items, consulting_items, today_str)

    # Gather existing archive files (before writing today's), newest first
    existing_archive_files = sorted(
        glob.glob(os.path.join(ARCHIVE_DIR, "*.html")), reverse=True
    )

    def link_item(slug, label):
        return f'<li><a href="archive/{slug}.html">{html_lib.escape(label)}</a></li>'

    archive_link_items = [link_item(today_slug, today_str)]
    for path in existing_archive_files[:20]:  # keep the list to the last 20
        slug = os.path.splitext(os.path.basename(path))[0]
        try:
            label = datetime.datetime.strptime(slug, "%Y-%m-%d").strftime("%A, %B %d, %Y")
        except ValueError:
            label = slug
        archive_link_items.append(link_item(slug, label))

    page_html = render_html(content_fragment, today_str, archive_link_items)

    # Write today's archive copy
    archive_path = os.path.join(ARCHIVE_DIR, f"{today_slug}.html")
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write(page_html)

    # Write/overwrite the homepage with today's edition
    index_path = os.path.join(DOCS_DIR, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(page_html)

    print("Done! Wrote", index_path, "and", archive_path)
