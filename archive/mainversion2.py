import feedparser
import json
from pathlib import Path
from datetime import datetime

config_path=Path("config.json")

def fetch_rss(parameterurl, max_per_feed):
    feed = feedparser.parse(parameterurl)

    # Build a markdown section as a string
    md = ""
    md += f"## {feed.feed.get('title', 'Untitled feed')}\n\n"

    for entry in feed.entries[:max_per_feed]:
        title = getattr(entry, "title", "(no title)")
        link = getattr(entry, "link", "")
        if link:
            md += f"- [{title}]({link})\n"
        else:
            md += f"- {title}\n"

    md += "\n"
    return md

config = json.loads(config_path.read_text(encoding="utf-8"))

rss_urls = config["rss_urls"]

max_per_feed = config.get("max_per_feed", 5)

if __name__ == "__main__":
    report = "# Trend Agent Report\n\n"

    for variableurl in rss_urls:
        report += fetch_rss(variableurl, max_per_feed)

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
filename = f"trend_report_{timestamp}.md"

Path(filename).write_text(report, encoding="utf-8")
print(f"Saved: {filename}")

   