import json
import hashlib
import os
from typing import Dict, Set

class ArticleCache:
    MAX_CACHE_SIZE = 50
    def __init__(self, cache_file: str = "article_cache.json", max_size: int = 50):
        self.cache_file = cache_file
        self.cache: Dict[str, Set[str]] = self._load_cache()
        self.MAX_CACHE_SIZE = max_size
        self._check_and_trim_initial_load() 
    def _load_cache(self) -> Dict[str, Set[str]]:
        default_cache = {"urls": set(), "title_hashes": set(), "order": []}
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    cache_data = json.load(f)
                    urls = set(cache_data.get("urls", []))
                    title_hashes = set(cache_data.get("title_hashes", []))
                    order = [list(item) for item in cache_data.get("order", [])]
                    return {"urls": urls, "title_hashes": title_hashes, "order": order}
            #adding error handling and all bro 🙏🙏🙏
            except json.JSONDecodeError:
                print(f"Warning: Cache file '{self.cache_file}' corrupted. Starting new cache.")
                return default_cache
        return default_cache

    def _save_cache(self):
        cache_data = {
            "urls": list(self.cache["urls"]),
            "title_hashes": list(self.cache["title_hashes"]),
            "order": self.cache["order"]
        }
        with open(self.cache_file, 'w') as f:
            json.dump(cache_data, f, indent=4)

    def _hash_title(self, title: str) -> str:
        return hashlib.md5(title.lower().encode()).hexdigest()
    def _evict_oldest(self):
        if self.cache["order"]:
            oldest_url, oldest_hash = self.cache["order"].pop(0)
            #Set discard   
            self.cache["urls"].discard(oldest_url)
            self.cache["title_hashes"].discard(oldest_hash)
            
            print(f"Cache limit of ({self.MAX_CACHE_SIZE}) URLS exceeded. Trashing oldest URL: {oldest_url}...")
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
        #Add to OL
        self.cache["order"].append([url, title_hash])
        #clearington
        if len(self.cache["order"]) > self.MAX_CACHE_SIZE:
            self._evict_oldest()
        self._save_cache()
    def clear_cache(self):
        self.cache = {"urls": set(), "title_hashes": set(), "order":[]}
        self._save_cache()
        print("Cache cleared.")

    def _check_and_trim_initial_load(self): # Self-explanatory
        while len(self.cache["order"]) > self.MAX_CACHE_SIZE:
            self._evict_oldest()
        if len(self.cache["order"]) < self.MAX_CACHE_SIZE:
             print(f"Loaded cache with {len(self.cache['order'])} articles.")