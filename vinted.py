from __future__ import annotations

import json
import time
import requests
import logging

from deep_translator import GoogleTranslator

logger = logging.getLogger(__name__)

DOMAINS = ["www.vinted.pl", "www.vinted.fr"]

DOMAIN_LANG = {
    "www.vinted.pl": "pl",
    "www.vinted.fr": "fr",
}


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

    def get_catalogs(self, domain: str) -> list[dict]:
        """Fetch catalog tree from Vinted.

        Returns list of top-level categories, each with nested 'catalogs'.
        Each entry: {id, title, catalogs: [...]}.
        """
        session = self._get_session(domain)
        try:
            resp = session.get(
                f"https://{domain}/catalog?search_text=test",
                timeout=15,
            )
            resp.raise_for_status()
            html = resp.text
        except Exception:
            logger.exception(f"[{domain}] Failed to fetch catalog page")
            return []

        idx = html.find("catalogTree")
        if idx == -1:
            logger.warning(f"[{domain}] catalogTree not found in page")
            return []

        chunk = html[idx:]
        arr_start = chunk.find("[")
        if arr_start == -1:
            return []

        raw = chunk[arr_start:]
        depth = 0
        end = 0
        i = 0
        while i < len(raw):
            if raw[i:i + 2] == "\\\\":
                i += 2
                continue
            if raw[i:i + 2] == '\\"':
                i += 2
                continue
            if raw[i] == "[":
                depth += 1
            elif raw[i] == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            i += 1

        arr_str = raw[:end].replace('\\"', '"').replace("\\\\", "\\")
        try:
            return json.loads(arr_str)
        except json.JSONDecodeError:
            logger.warning(f"[{domain}] Failed to parse catalogTree JSON")
            return []

    @staticmethod
    def translate_titles(titles: list[str], source_lang: str) -> dict[str, str]:
        """Translate a list of titles to Russian. Returns {original: translated}."""
        if not titles:
            return {}
        try:
            translator = GoogleTranslator(source=source_lang, target="ru")
            translated = translator.translate_batch(titles)
            return dict(zip(titles, translated))
        except Exception:
            logger.exception("Translation failed")
            return {t: t for t in titles}

    def _fetch_page(
        self, domain: str, query: str, page: int, per_page: int,
        catalog_ids: list[int] | None = None,
    ) -> list[dict]:
        """Fetch a single page of results."""
        params = {
            "search_text": query,
            "per_page": per_page,
            "page": page,
            "time": int(time.time()),
            "order": "newest_first",
        }
        if catalog_ids:
            params["catalog_ids[]"] = catalog_ids
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

    def search(
        self, domain: str, query: str, per_page: int = 96, max_pages: int = 10,
        catalog_ids: list[int] | None = None,
    ) -> list[dict]:
        """Search items on a specific Vinted domain with pagination."""
        all_items = []
        page = 1
        while page <= max_pages:
            items = self._fetch_page(domain, query, page, per_page, catalog_ids)
            if not items:
                break
            all_items.extend(items)
            if len(items) < per_page:
                break
            page += 1
        logger.info(f"[{domain}] '{query}': {len(all_items)} items across {page} pages")
        return all_items

    def search_all(self, query: str, max_pages: int = 10) -> dict[str, list[dict]]:
        """Search across all Vinted domains."""
        return {domain: self.search(domain, query, max_pages=max_pages) for domain in DOMAINS}
