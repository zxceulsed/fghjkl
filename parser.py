import requests
import json
import sys


def create_session() -> requests.Session:
    """Create a session with Vinted cookies."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })
    # Visit the main page to get session cookies
    session.get("https://www.vinted.pl")
    return session


def search_items(session: requests.Session, query: str, per_page: int = 24) -> list[dict]:
    """Search Vinted catalog and return list of items."""
    url = "https://www.vinted.pl/api/v2/catalog/items"
    params = {
        "search_text": query,
        "per_page": per_page,
        "order": "relevance",
    }
    session.headers.update({
        "Accept": "application/json, text/plain, */*",
    })

    resp = session.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    return data.get("items", [])


def print_items(items: list[dict]):
    """Print items in a readable format."""
    if not items:
        print("No items found.")
        return

    print(f"Found {len(items)} items:\n")
    print("-" * 80)

    for i, item in enumerate(items, 1):
        title = item.get("title", "N/A")
        price = item.get("price", "N/A")
        brand = item.get("brand_title", "N/A")
        size = item.get("size_title", "N/A")
        url = item.get("url", "N/A")
        photo_url = item.get("photo", {}).get("url", "N/A") if item.get("photo") else "N/A"

        print(f"  #{i}")
        print(f"  Title: {title}")
        print(f"  Brand: {brand}")
        print(f"  Price: {price}")
        print(f"  Size:  {size}")
        print(f"  URL:   {url}")
        print(f"  Photo: {photo_url}")
        print("-" * 80)


def save_json(items: list[dict], filename: str = "items.json"):
    """Save raw items data to JSON file."""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"\nRaw data saved to {filename}")


def main():
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "balenciaga 3xl"

    print(f'Searching Vinted.pl for: "{query}"\n')

    session = create_session()
    items = search_items(session, query)
    print_items(items)
    save_json(items)


if __name__ == "__main__":
    main()
