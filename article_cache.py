import json
import hashlib
import os
import time
from typing import Dict

class ArticleCache:
    DEFAULT_MAX_SIZE = 50

    def __init__(self, cache_file: str = "article_cache.json", max_size: int = DEFAULT_MAX_SIZE):
        self.cache_file = cache_file
        self.max_size = max_size
        self.cache = self._load_cache()
        self._trim_to_limit()

    def _load_cache(self) -> Dict:
        default_cache = {"urls": set(), "title_hashes": set(), "entries": []}
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    cache_data = json.load(f)
                    urls = set(cache_data.get("urls", []))
                    title_hashes = set(cache_data.get("title_hashes", []))
                    entries = cache_data.get("entries")
                    if entries is None:
                        # Older cache files stored plain [url, title_hash] pairs with no
                        # timestamp; treat them as created "now" so they age out normally.
                        entries = [
                            {"url": url, "title_hash": title_hash, "timestamp": time.time()}
                            for url, title_hash in cache_data.get("order", [])
                        ]
                    return {"urls": urls, "title_hashes": title_hashes, "entries": entries}
            #adding error handling and all bro 🙏🙏🙏
            except json.JSONDecodeError:
                print(f"Warning: Cache file '{self.cache_file}' corrupted. Starting new cache.")
                return default_cache
        return default_cache

    def _save_cache(self):
        cache_data = {
            "urls": list(self.cache["urls"]),
            "title_hashes": list(self.cache["title_hashes"]),
            "entries": self.cache["entries"]
        }
        with open(self.cache_file, 'w') as f:
            json.dump(cache_data, f, indent=4)

    def _hash_title(self, title: str) -> str:
        return hashlib.md5(title.lower().encode()).hexdigest()

    def _evict_oldest(self):
        entries = self.cache["entries"]
        if not entries:
            return
        oldest_idx = min(range(len(entries)), key=lambda i: entries[i]["timestamp"])
        oldest = entries.pop(oldest_idx)
        self.cache["urls"].discard(oldest["url"])
        self.cache["title_hashes"].discard(oldest["title_hash"])

        print(f"Cache limit of ({self.max_size}) entries exceeded. Evicting oldest entry: {oldest['url']}")

    def _trim_to_limit(self):
        while len(self.cache["entries"]) > self.max_size:
            self._evict_oldest()

    def is_article_processed(self, url: str, title: str) -> bool:
        title_hash = self._hash_title(title)
        return url in self.cache["urls"] or title_hash in self.cache["title_hashes"]

    def add_article(self, url: str, title: str):
        if self.is_article_processed(url, title):
            return
        title_hash = self._hash_title(title)
        #Add to set
        self.cache["urls"].add(url)
        self.cache["title_hashes"].add(title_hash)
        #Track creation time so the oldest entry can be evicted once we're over the limit
        self.cache["entries"].append({"url": url, "title_hash": title_hash, "timestamp": time.time()})
        self._trim_to_limit()
        self._save_cache()

    def clear_cache(self):
        """Reset in-memory state and remove the cache file from disk entirely."""
        self.cache = {"urls": set(), "title_hashes": set(), "entries": []}
        if os.path.exists(self.cache_file):
            os.remove(self.cache_file)
        print(f"Cache cleared and '{self.cache_file}' removed.")
