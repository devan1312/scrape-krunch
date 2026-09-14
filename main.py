import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from ollama import chat, ChatResponse
from duckduckgo_search import DDGS as ddgs
from article_cache import ArticleCache

article_cache = ArticleCache()

FALLBACK_CONTAINER_SELECTORS = [
    ("article", {}),
    ("div", {"class": "article-content"}),
    ("div", {"class": "article-body"}),
    ("div", {"class": "story-body"}),
    ("div", {"class": "entry-content"}),
    ("div", {"class": "post-content"}),
    ("div", {"class": "content-body"}),
    ("div", {"class": "content"}),
    ("main", {}),
]

MIN_MEANINGFUL_CONTENT_LENGTH = 200


def _text_from_container(container, paragraph_limit=None):
    paragraphs = container.find_all("p")
    if paragraph_limit:
        paragraphs = paragraphs[:paragraph_limit]
    return "\n".join(p.get_text(strip=True) for p in paragraphs).strip()


def extract_with_fallback(soup, site_selectors=None, url=""):
    candidates = list(site_selectors or []) + FALLBACK_CONTAINER_SELECTORS

    for tag, attrs in candidates:
        container = soup.find(tag, attrs) if attrs else soup.find(tag)
        if not container:
            continue
        content = _text_from_container(container)
        if len(content) >= MIN_MEANINGFUL_CONTENT_LENGTH:
            return content

    print(f"Warning: no known article container matched for '{url}'. "
          f"Falling back to generic <p> tag extraction.")
    content = _text_from_container(soup, paragraph_limit=10)
    return content if content else "Could not extract content."

SCRAPER_USER_AGENT = "scrape-krunch/1.0"


class RobotsPolicy:
    def __init__(self):
        self.parsers = {}
        self.last_requests = {}

    def _get_parser(self, url):
        parsed_url = urlparse(url)
        host = parsed_url.netloc.lower()
        if not parsed_url.scheme or not host:
            return None, host

        if host in self.parsers:
            return self.parsers[host], host

        robots_url = f"{parsed_url.scheme}://{parsed_url.netloc}/robots.txt"
        parser = RobotFileParser(robots_url)
        try:
            parser.read()
        except Exception as error:
            print(f"[robots] Could not read {robots_url}; skipping {url}: {error}")
            self.parsers[host] = None
            return None, host

        self.parsers[host] = parser
        self.last_requests[host] = time.monotonic()
        return parser, host

    def is_allowed(self, url, user_agent):
        parser, host = self._get_parser(url)
        if parser is None or not parser.can_fetch(user_agent, url):
            print(f"[robots] Disallowed by robots.txt; skipping URL: {url}")
            return False

        delay = parser.crawl_delay(user_agent) or parser.crawl_delay("*")
        if delay:
            elapsed = time.monotonic() - self.last_requests.get(host, 0)
            remaining = delay - elapsed
            if remaining > 0:
                time.sleep(remaining)
        return True

    def record_request(self, url):
        self.last_requests[urlparse(url).netloc.lower()] = time.monotonic()


robots_policy = RobotsPolicy()


def safe_get(url, headers=None, timeout=10):
    request_headers = dict(headers or {})
    user_agent = request_headers.setdefault("User-Agent", SCRAPER_USER_AGENT)
    if not robots_policy.is_allowed(url, user_agent):
        return None

    response = requests.get(url, headers=request_headers, timeout=timeout)
    robots_policy.record_request(url)
    return response


def export_articles(articles, output_format, output_dir="exports"):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = output_path / f"articles_{timestamp}.{output_format}"

    if output_format == "json":
        file_path.write_text(json.dumps(articles, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        markdown = ["# Article Export", ""]
        for article in articles:
            markdown.extend([
                f"## {article['title']}",
                "",
                f"Source URL: {article['source_url']}",
                "",
                "### Raw text",
                "",
                article["raw_text"],
                "",
                "### LLM summary",
                "",
                article["summary"],
                "",
            ])
        file_path.write_text("\n".join(markdown), encoding="utf-8")

    print(f"Exported {len(articles)} article(s) to {file_path}")
    return file_path


def search_duckduckgo(query, max_results=10):
    with ddgs() as ddgs_instance:
        reddit_results = list(ddgs_instance.text(keywords=query, max_results=max_results))
    return reddit_results


def get_article_links(count=3):
    try:
        query = "latest business news 2025"
        print(f"biz articles getting..")

        results = search_duckduckgo(query, max_results=count * 3)
        articles = []

        for result in results:
            if result.get('title') and result.get('href'):
                url = result['href']
                if article_cache.is_article_processed(url, result['title']):
                    print(f"Skipping previously processed article: {result['title']}")
                    continue
                    
                if any(source in url.lower() for source in
                       ['reuters', 'bloomberg', 'wsj', 'marketwatch', 'cnbc', 'yahoo', 'finance']):
                    articles.append({
                        "title": result['title'],
                        "link": url
                    })
                    article_cache.add_article(url, result['title'])
                    if len(articles) >= count:
                        break

        if not articles:
            exported_articles = []
                if result.get('title') and result.get('href'):
                    if article_cache.is_article_processed(result['href'], result['title']):
                        print(f"Skipping previously processed article: {result['title']}")
                        continue
                    articles.append({
                        "title": result['title'],
                        "link": result['href']
                    })
                    article_cache.add_article(result['href'], result['title'])
                    if len(articles) >= count:
                        break

        return articles if articles else get_fallback_business_news(count)

    except Exception as e:
        print(f" search failed: {e}. fallback...")
        return get_fallback_business_news(count)


def get_fallback_business_news(count=3):
    return [{
        "title": "Business News: Global Markets Show Mixed Performance Amid Economic Uncertainty",
        "link": "https://example.com/business-news"
    }]


def get_bbc_business_articles(count=3):
    url = "https://www.bbc.com/business"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    try:
        response = safe_get(url, headers=headers, timeout=10)
        if response is None:
            return []
        soup = BeautifulSoup(response.text, "html.parser")

        articles = []
        article_links = soup.find_all("a", href=True)

        for link in article_links:
            href = link.get("href", "")
            if "/news/business-" in href or "/news/articles/" in href:
                title_elem = link.find("h3") or link.find("span") or link
                title = title_elem.get_text(strip=True)

                if title and len(title) > 10:

                    exported_articles.append({
                        "title": heading_text,
                        "source_url": "https://idrw.org/",
                        "raw_text": article_text,
                        "summary": response.message.content,
                    })
                    full_url = href if href.startswith("http") else f"https://www.bbc.com{href}"
                    
                    if article_cache.is_article_processed(full_url, title):
                        print(f"Skipping previously processed article: {title}")
                        continue
                        
                    articles.append({"title": title, "link": full_url})
                    article_cache.add_article(full_url, title)

                    if len(articles) >= count:
                        break

        return articles

    except Exception as e:
        print(f"BBC null: {e}")
        return [{"title": "unableto  fetch  articles", "link": ""}]


def extract_article_content(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    try:
        response = safe_get(url, headers=headers, timeout=10)
        if response is None:
            return "Skipped by robots.txt."
        soup = BeautifulSoup(response.text, "html.parser")

        site_selectors = []
        if "reuters.com" in url:
            site_selectors = [
                ("div", {"data-testid": "ArticleBody"}),
                ("div", {"class": "StandardArticleBody_body"}),
            ]
        elif "bbc.com" in url:
            site_selectors = [
                ("div", {"data-component": "text-block"}),
                ("div", {"class": "story-body"}),
            ]

        return extract_with_fallback(soup, site_selectors=site_selectors, url=url)

    except Exception as e:
        return f"Error extracting content: {e}"


def get_tech_articles(count=3):
    url = "https://techcrunch.com/latest/"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = safe_get(url, headers=headers)
    if response is None:
        return []
    soup = BeautifulSoup(response.text, "html.parser")

    articles = []
    article_links = soup.find_all("a", class_="post-block__title__link")

    for link in article_links:
        title = link.get_text(strip=True)
        href = link["href"]
        
        if article_cache.is_article_processed(href, title):
            print(f"Skipping previously processed article: {title}")
            continue
            
        articles.append({"title": title, "link": href})
        article_cache.add_article(href, title)
        if len(articles) >= count:
            break
    return articles


def extract_tech_content(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = safe_get(url, headers=headers, timeout=10)
        if response is None:
            return "Skipped by robots.txt."
        soup = BeautifulSoup(response.text, "html.parser")

        site_selectors = [
            ("div", {"class": "article-content"}),
            ("div", {"class": "entry-content"}),
        ]
        return extract_with_fallback(soup, site_selectors=site_selectors, url=url)

    except Exception as e:
        return f"Error: {e}"


def get_sports_articles(count=3):
    url = "https://www.espn.com/sports/"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = safe_get(url, headers=headers)
    if response is None:
        return []
    soup = BeautifulSoup(response.text, "html.parser")

    articles = []
    article_links = soup.find_all("a", href=True)

    for link in article_links:
        href = link["href"]
        title_elem = link.find("h3") or link.find("h2") or link.find("span")
        if title_elem and "/story/" in href:
            title = title_elem.get_text(strip=True)
            full_url = href if href.startswith("http") else "https://www.espn.com" + href
            
            if article_cache.is_article_processed(full_url, title):
                print(f"Skipping previously processed article: {title}")
                continue
                
            articles.append({"title": title, "link": full_url})
            article_cache.add_article(full_url, title)
            if len(articles) >= count:
                break
    return articles


def extract_sports_content(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = safe_get(url, headers=headers, timeout=10)
        if response is None:
            return "Skipped by robots.txt."
        soup = BeautifulSoup(response.text, "html.parser")

        site_selectors = [
            ("div", {"class": "story-body"}),
            ("div", {"class": "article-body"}),
        ]
        return extract_with_fallback(soup, site_selectors=site_selectors, url=url)

    except Exception as e:
        return f"Error: {e}"


def get_health_articles(count=3):
    url = "https://www.healthline.com/health-news"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = safe_get(url, headers=headers)
    if response is None:
        return []
    soup = BeautifulSoup(response.text, "html.parser")

    articles = []
    article_links = soup.find_all("a", href=True)

    for link in article_links:
        href = link["href"]
        title_elem = link.find("h2") or link.find("h3")
        if title_elem and "/health-news/" in href:
            title = title_elem.get_text(strip=True)
            full_url = href if href.startswith("http") else "https://www.healthline.com" + href
            
            if article_cache.is_article_processed(full_url, title):
                print(f"Skipping previously processed article: {title}")
                continue
                
            articles.append({"title": title, "link": full_url})
            article_cache.add_article(full_url, title)
            if len(articles) >= count:
                break
    return articles


def extract_health_content(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = safe_get(url, headers=headers, timeout=10)
        if response is None:
            return "Skipped by robots.txt."
        soup = BeautifulSoup(response.text, "html.parser")

        site_selectors = [
            ("div", {"class": "article-body"}),
            ("div", {"class": "content"}),
        ]
        return extract_with_fallback(soup, site_selectors=site_selectors, url=url)

    except Exception as e:
        return f"Error: {e}"


def get_entertainment_articles(count=3):
    url = "https://variety.com/latest/"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = safe_get(url, headers=headers)
    if response is None:
        return []
    soup = BeautifulSoup(response.text, "html.parser")

    articles = []
    article_links = soup.find_all("a", href=True)

    for link in article_links:
        href = link["href"]
        title_elem = link.find("h3") or link.find("h2")
        if title_elem and "variety.com" in href and "/news/" in href:
            title = title_elem.get_text(strip=True)
            
            if article_cache.is_article_processed(href, title):
                print(f"Skipping previously processed article: {title}")
                continue
                
            articles.append({"title": title, "link": href})
            article_cache.add_article(href, title)
            if len(articles) >= count:
                break
    return articles


def get_stuff(output_format=None):
    processed_articles = set()
    exported_articles = []

    html = safe_get("https://idrw.org/")
    if html is None:
        return
    soup = BeautifulSoup(html.text, "html.parser")
    articles = soup.find_all("article")

    for i, article in enumerate(articles):
        heading = article.find("h2")
        if not heading:
            continue

        heading_text = heading.text.strip()
        article_text = article.text.strip()

        if heading_text in processed_articles:
            continue
        processed_articles.add(heading_text)

        print(f"\nScraped heading {i + 1}: {heading_text}")
        print(f"Scraped content: {article_text}\n")

    for i, article in enumerate(articles):
        heading = article.find("h2")
        if not heading:
            continue

        heading_text = heading.text.strip()
        article_text = article.text.strip()

        if heading_text in processed_articles:
            continue
        processed_articles.add(heading_text)

        print(f"\nScraped heading {i + 1}: {heading_text}")
        print(f"Scraped content: {article_text}\n")


def get_reddit_posts(query, count=7):
    search_url = f"https://www.reddit.com/search.json?q={query}&sort=hot&limit={count}"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = safe_get(search_url, headers=headers)
        if response is None:
            return []
        data = response.json()

        posts = []
        for post in data['data']['children']:
            post_data = post['data']
            title = post_data['title']
            selftext = post_data.get('selftext', '')
            url = f"https://www.reddit.com{post_data['permalink']}"
            subreddit = post_data['subreddit']
            score = post_data['score']

            posts.append({
                'title': f"[r/{subreddit}] {title}",
                'link': url,
                'content': selftext,
                'score': score
            })

        return posts
    except Exception as e:
        return [{"title": f"Error fetching Reddit posts: {e}", "link": "", "content": "", "score": 0}]


def extract_reddit_content(post):
    if 'content' in post:
        return post['content'] if post['content'] else "No text content available."
    else:
        return "No text content available."


def get_reddit_comments(post_url, max_comments=50):
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        json_url = post_url.rstrip('/') + '.json'
        response = safe_get(json_url, headers=headers)
        if response is None:
            return "No comments available"
        data = response.json()

        comments = []
        if len(data) > 1 and 'data' in data[1]:
            comment_data = data[1]['data']['children']

            for comment in comment_data[:max_comments]:
                if comment['kind'] == 't1' and 'body' in comment['data']:
                    body = comment['data']['body']
                    author = comment['data']['author']
                    score = comment['data']['score']
                    comments.append(f"[{author}] ({score} points): {body}")

        return '\n\n'.join(comments) if comments else "No comments available"

    except Exception as e:
        return f"Error fetching comments: {e}"


def fetch_comments_continuously(post_url):
    all_comments = []
    comment_count = 0
    batch_size = 10

    print("\nfetching comments")
    print("ctrl+c to jump to the summarise part \n")

    try:
        while True:
            print(f" fetching comments {comment_count // batch_size + 1}...")

            comments = get_reddit_comments(post_url, max_comments=batch_size + comment_count)

            if comments and comments != "No comments available":
                comment_list = comments.split('\n\n')
                new_comments = comment_list[comment_count:]

                if new_comments:
                    all_comments.extend(new_comments)
                    comment_count = len(all_comments)

                    print(f" got {len(new_comments)} new comments (total num: {comment_count})")

                    for i, comment in enumerate(new_comments[-3:], 1):
                        print(f" {comment[:400]}...")
                else:
                    print(" no comments found.")
                    break
            else:
                print(" no comments there.")
                break

            time.sleep(2)

    except KeyboardInterrupt:
        print(f"\n fetch interrupted num of comments collected: {len(all_comments)}")

    return '\n\n'.join(all_comments) if all_comments else "no comments available"


def analyze_with_llm(title, content):
    if content.startswith("Error :") or len(content.split()) < 30:
        return "low content. skipping"
    try:
        response: ChatResponse = chat(model='llama3.2', messages=[
            {
                'role': 'system',
                'content': """You are a global news analyst. Given a news article, respond with the following format:
                 1. Summary: ... 
                 2. Sentiment: Positive / Negative / Neutral 
                 3. Socio-economic Impact: ... 
                 4. Political Impact: ... 
                 5. Stock Market Impact: ... """,
            },
            {
                'role': 'user',
                'content': f"Title: {title}\n\nContent:\n{content}",
            },
        ])
        return response.message.content
    except Exception as e:
        return f"  Error: {e}"


def analyze_reddit_discussion(title, combined_content):
    if combined_content.startswith("Error :") or len(combined_content.split()) < 50:
        return "Insufficient content for analysis. Skipping."

    try:
        response: ChatResponse = chat(model='llama3.2', messages=[
            {
                'role': 'system',
                'content': """You are a Reddit discussion analyst powered by Llama3.2. Analyze Reddit posts and their comments to provide comprehensive insights. Structure your response with:

1. DISCUSSION SUMMARY: Brief overview of the main post and key discussion points
2. KEY THEMES: Main topics and themes discussed in comments
3. COMMUNITY SENTIMENT: Overall sentiment and emotional tone of the discussion
4. HOT TAKES: Most upvoted or controversial viewpoints
5. INSIGHTS: Deeper analysis of what this discussion reveals about the topic/community
6. ENGAGEMENT PATTERNS: How users are interacting and what drives engagement
7. TAKEAWAYS: Key conclusions and implications

Be concise but thorough, focusing on the most interesting and relevant aspects of the discussion.""",
            },
            {
                'role': 'user',
                'content': f"Reddit Post Title: {title}\n\nContent and Comments:\n{combined_content}",
            },
        ])
        return response.message.content
    except Exception as e:
        return f"Analysis Error: {e}"


def get_stuff(output_format=None):
    processed_articles = set()
    exported_articles = []

    html = safe_get("https://idrw.org/")
    if html is None:
        return
    soup = BeautifulSoup(html.text, "html.parser")
    articles = soup.find_all("article")

    for i, article in enumerate(articles):
        heading = article.find("h2")
        if not heading:
            continue

        heading_text = heading.text.strip()
        article_text = article.text.strip()

        if heading_text in processed_articles:
            continue
        processed_articles.add(heading_text)

        print(f"\nScraped heading {i + 1}: {heading_text}")
        print(f"Scraped content: {article_text}\n")

        next_para = heading.find_next("p")
        if next_para:
            print(f"Preview of next: {next_para.text.strip()}")
        else:
            print("End reached.")

        try:
            response: ChatResponse = chat(model='llama3.2', messages=[
                {
                    'role': 'system',
                    'content': """summarize the defence article and provide
                     insights on its impact on the present state of global politics
                     and any future impacts it can have on INDIA
        """,
                },
                {
                    'role': 'user',
                    'content': f"Here is the news article:\n\n{article_text}",
                },
            ])

            print("\n--- LLM Response ---\n")
            print(response.message.content)
            print("\n--------------------\n")

            exported_articles.append({
                "title": heading_text,
                "source_url": "https://idrw.org/",
                "raw_text": article_text,
                "summary": response.message.content,
            })
        except Exception as e:
            print(f"dunno what happened: {e}")

    if output_format:
        export_articles(exported_articles, output_format)


def main():
    parser = argparse.ArgumentParser(description="Scrape and summarize news articles.")
    parser.add_argument(
        "--output-format",
        choices=("json", "markdown"),
        help="Write scraped articles and summaries to an exports/ file.",
    )
    args = parser.parse_args()

    print("Select content type to scrape and summarize:")
    print("1. Business  ")
    print("2. Technology [wip]")
    print("3. Sports  ")
    print("4. Health  ")
    print("5. DEFENCE ")
    print("6. Reddit [work in progress]")

    choice = input("Enter your choice (1-6): ").strip()

    if choice == "1":
        print("fetching  Business  articles...")
        articles = get_article_links()
        extract_func = extract_article_content
    elif choice == "2":
        print("fetching  Technology  articles...")
        articles = get_tech_articles()
        extract_func = extract_tech_content
    elif choice == "3":
        print(" fetching  Sports  articles...")
        articles = get_sports_articles()
        extract_func = extract_sports_content
    elif choice == "4":
        print("fetching  Health  articles...")
        articles = get_health_articles()
        extract_func = extract_health_content
    elif choice == "5":
        get_stuff(args.output_format)
        return
    elif choice == "6":
        get_reddit_posts_query = input("Enter the topic you want to search on Reddit: ")
        print(f"fetching Reddit posts for '{get_reddit_posts_query}'...")
        articles = get_reddit_posts(get_reddit_posts_query, count=1)
        extract_func = extract_reddit_content

        if not articles:
            print("no posted found ")
            return

        print(f"Found {len(articles)} Reddit posts.")
        if len(articles) < 1:
            print("limited postes avaliable")
            count = len(articles)
        else:
            count = 1

        articles = articles[:count]
        print(f" {count} reddit posts:")

        exported_articles = []

        for i in range(count):
            print(f"\n🔹 [{i + 1}] {articles[i]['title']}")
            print(f"🔗 {articles[i]['link']}")

            content = extract_func(articles[i])

            print(f"\n post preview:\n{content[:500]}...\n")

            print("\n  comments fetching...")
            all_comments = fetch_comments_continuously(articles[i]['link'])

            combined_content = f" CONTENT:\n{content}\n\nCOMMENTS:\n{all_comments}"

            print(f"\n analysis of post and comments...")
            analysis = analyze_reddit_discussion(articles[i]["title"], combined_content)
            print(f"\n  Analysis:\n{analysis}\n")

            exported_articles.append({
                "title": articles[i]["title"],
                "source_url": articles[i]["link"],
                "raw_text": combined_content,
                "summary": analysis,
            })

            print("------------------------------\n")

        if args.output_format:
            export_articles(exported_articles, args.output_format)

        return

    else:
        print("Invalid choice. default is business..")
        articles = get_article_links(count=1)
        extract_func = extract_article_content

    if not articles:
        print("No articles found.")
        return

    exported_articles = []
    for i, article in enumerate(articles, 1):
        print(f"\n🔹 [{i}] {article['title']}")
        print(f"🔗 {article['link']}")

        content = extract_func(article["link"])

        print(f"\n preview:\n{content[:1000]}...\n")

        analysis = analyze_with_llm(article["title"], content)
        print(f" analysis:\n{analysis}\n")

        exported_articles.append({
            "title": article["title"],
            "source_url": article["link"],
            "raw_text": content,
            "summary": analysis,
        })

        print("------------------------------\n")
        time.sleep(1)

    if args.output_format:
        export_articles(exported_articles, args.output_format)


def parse_args():
    parser = argparse.ArgumentParser(description="Scrape and summarize news articles.")
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Flush the stored article cache file and exit."
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.clear_cache:
        article_cache.clear_cache()
    else:
        main()
