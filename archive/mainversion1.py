import feedparser
import json
import pathlib

config_path=pathlib.Path("config.json")

def fetch_rss(parameterurl, max_per_feed):
    feed = feedparser.parse(parameterurl)
    print(f"\n=== {feed.feed.title} ===\n")

    for entry in feed.entries[:max_per_feed]:
        print("Title:", entry.title)
        print("Link:", entry.link)
        print("-" * 40)

config = json.loads(config_path.read_text(encoding="utf-8"))

rss_urls = config["rss_urls"]

max_per_feed = config.get("max_per_feed", 5)

if __name__ == "__main__":
    for variableurl in rss_urls:
        fetch_rss(variableurl, max_per_feed)