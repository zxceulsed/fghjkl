import time
import requests
import logging

logger = logging.getLogger(__name__)

DOMAINS = ["www.vinted.pl", "www.vinted.fr"]


class VintedClient:
    def __init__(self):
        self._sessions: dict[str, requests.Session] = {}

    def _create_session(self, domain: str) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })
        session.get(f"https://{domain}", timeout=10)
        return session

    def _get_session(self, domain: str) -> requests.Session:
        if domain not in self._sessions:
            self._sessions[domain] = self._create_session(domain)
        return self._sessions[domain]

    def _refresh_session(self, domain: str) -> requests.Session:
        self._sessions.pop(domain, None)
        return self._get_session(domain)

    def _fetch_page(self, domain: str, query: str, page: int, per_page: int) -> list[dict]:
        """Fetch a single page of results."""
        params = {
            "search_text": query,
            "per_page": per_page,
            "page": page,
            "time": int(time.time()),
            "order": "newest_first",
        }
        for attempt in range(2):
            session = self._get_session(domain) if attempt == 0 else self._refresh_session(domain)
            try:
                resp = session.get(
                    f"https://{domain}/api/v2/catalog/items",
                    params=params,
                    headers={"Accept": "application/json, text/plain, */*"},
                    timeout=15,
                )
                if resp.status_code == 401 and attempt == 0:
                    continue
                resp.raise_for_status()
                return resp.json().get("items", [])
            except Exception as e:
                logger.warning(f"[{domain}] page {page} attempt {attempt+1} failed: {e}")
                if attempt == 0:
                    continue
                return []

    def search(self, domain: str, query: str, per_page: int = 96) -> list[dict]:
        """Search all pages of items on a specific Vinted domain."""
        all_items = []
        page = 1
        while True:
            items = self._fetch_page(domain, query, page, per_page)
            if not items:
                break
            all_items.extend(items)
            if len(items) < per_page:
                break
            page += 1
        logger.info(f"[{domain}] '{query}': {len(all_items)} items across {page} pages")
        return all_items

    def search_all(self, query: str) -> dict[str, list[dict]]:
        """Search across all Vinted domains."""
        return {domain: self.search(domain, query) for domain in DOMAINS}
