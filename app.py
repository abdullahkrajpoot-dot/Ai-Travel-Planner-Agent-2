import hashlib
import os
import re
import tempfile
import time
import unicodedata
from datetime import date, datetime, timedelta
from io import BytesIO
from urllib.parse import quote

import requests
import streamlit as st
from fpdf import FPDF
from PIL import Image, UnidentifiedImageError


st.set_page_config(page_title="Ai Travel Plan", layout="wide")

st.markdown(
    """
    <style>
    #MainMenu, footer, header, [data-testid="stToolbar"], [data-testid="stDecoration"], .stDeployButton { display: none !important; }
    .main-header {
        color: #f8fafc;
        font-family: Georgia, Cambria, "Times New Roman", serif;
        font-weight: 700;
        font-size: 2.85rem;
        line-height: 1.08;
        letter-spacing: 0;
        margin-bottom: 1.5rem;
        text-shadow: 0 2px 14px rgba(96, 165, 250, 0.18);
    }
    .main-header span {
        color: #93c5fd;
        font-style: italic;
    }
    .stButton>button, .stDownloadButton>button {
        background: #2563eb;
        color: white;
        border-radius: 10px;
        border: none;
        font-weight: 700;
        width: 100%;
    }
    .stProgress > div > div > div > div { background-color: #22c55e; }
    </style>
    """,
    unsafe_allow_html=True,
)


def safe_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return normalized.encode("latin-1", "ignore").decode("latin-1").strip()


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value or "")
    return cleaned.strip("_").lower() or "travel_plan"


def format_day_label(day_date: date) -> str:
    return day_date.strftime("%B %d - %A")


def compact_date(day_date: date) -> str:
    return day_date.strftime("%b %d, %Y")


KNOWN_DESTINATION_PLACES = {
    "belgium": [
        ("Brussels, Belgium", ["Grand Place", "Galeries Royales Saint-Hubert", "Manneken Pis", "Royal Palace of Brussels"]),
        ("Bruges, Belgium", ["Bruges Markt", "Belfry of Bruges", "Rozenhoedkaai", "Basilica of the Holy Blood"]),
        ("Ghent, Belgium", ["Gravensteen Castle", "Saint Bavo's Cathedral", "Graslei", "Korenlei"]),
        ("Antwerp, Belgium", ["Antwerp Cathedral", "Grote Markt Antwerp", "MAS Museum", "Antwerp Central Station"]),
    ],
    "lahore": [
        ("Lahore, Pakistan", ["Badshahi Mosque", "Lahore Fort", "Minar-e-Pakistan", "Food Street Lahore"]),
        ("Old Lahore, Pakistan", ["Walled City of Lahore", "Delhi Gate Lahore", "Shahi Hammam", "Wazir Khan Mosque"]),
        ("Lahore, Pakistan", ["Shalimar Gardens Lahore", "Lahore Museum", "Anarkali Bazaar", "MM Alam Road Lahore"]),
        ("Lahore, Pakistan", ["Data Darbar", "Emporium Mall Lahore", "Packages Mall Lahore", "MM Alam Road Lahore"]),
        ("Lahore, Pakistan", ["Wagah Border", "Greater Iqbal Park", "Lahore Zoo", "Mall Road Lahore"]),
        ("Lahore, Pakistan", ["Allama Iqbal International Airport", "Lahore Cantonment", "Fortress Stadium Lahore", "Gulberg Lahore"]),
    ],
}


def normalize_places(value):
    if isinstance(value, list):
        return [safe_text(item) for item in value if safe_text(item)]
    if not value:
        return []
    return [safe_text(item) for item in re.split(r",|;|\|", str(value)) if safe_text(item)]


DESTINATION_PLACE_CACHE: dict[str, list[str]] = {}


WIKIPEDIA_SKIP_TERMS = (
    "list of", "lists of", "tourism in", "tourist attraction", "tourist attractions",
    "landmarks in", "history of", "geography of", "economy of", "demographics of",
    "transport in", "outline of", "culture of", "politics of", "flag of",
    "coat of arms", "disambiguation", "timeline of", "covid", "syndrome",
    "visa policy", "foreign relations", "index of", "bibliography of", "museums in",
    "state park", "station",
)


PLACE_INDICATOR_TERMS = (
    "aquarium", "arch", "archaeological", "architecture", "attraction", "bazaar",
    "basilica", "beach", "bridge", "castle", "cathedral", "center", "centre",
    "church", "city walk", "complex", "district", "fort", "fortress", "gallery",
    "garden", "gate", "historic", "island", "landmark", "mall", "market",
    "monument", "mosque", "museum", "old city", "old town", "opera", "palace",
    "park", "plaza", "quarter", "retail", "shrine", "souk", "square", "street",
    "temple", "theatre", "tower", "walk", "wheel", "zoo",
)


NON_PLACE_TERMS = (
    "actor", "album", "anime", "automobile", "car", "character", "doctor", "film",
    "football", "footballer", "manga", "motorcycle", "novel", "painting", "physician",
    "politician", "rapper", "record", "singer", "song", "television series",
    "video game", "writer",
)


def known_destination_contexts(destination: str):
    key = safe_text(destination).lower()
    for known_key, options in KNOWN_DESTINATION_PLACES.items():
        if known_key in key:
            return options
    return []


def clean_wikipedia_title(title: str):
    title = safe_text(title).replace("_", " ")
    title = re.sub(r"\s+\([^)]*\)$", "", title).strip()
    return title


def is_place_like_title(title: str, destination: str):
    clean_title = clean_wikipedia_title(title)
    lowered = clean_title.lower()
    destination_lower = safe_text(destination).lower()
    if not clean_title or len(clean_title) < 3 or len(clean_title) > 70:
        return False
    if ":" in clean_title or lowered == destination_lower:
        return False
    return not any(term in lowered for term in WIKIPEDIA_SKIP_TERMS)


def has_place_indicator(value: str):
    lowered = value.lower()
    extra_title_terms = ("champs", "elysees", "rue", "avenue", "archives", "disneyland")
    return any(term in lowered for term in PLACE_INDICATOR_TERMS + extra_title_terms)


def looks_like_physical_place(title: str, snippet: str, destination: str):
    title_lower = title.lower()
    haystack = f"{title} {snippet}".lower()
    if any(term in haystack for term in NON_PLACE_TERMS):
        return False
    if not any(term in haystack for term in PLACE_INDICATOR_TERMS):
        return False
    title_mentions_destination = any(word in slugify(title).split("_") for word in destination_words(destination))
    return has_place_indicator(title) or title_mentions_destination


def destination_words(destination: str):
    words = [word for word in slugify(destination).split("_") if len(word) >= 3]
    demonyms = {
        "america": "american",
        "belgium": "belgian",
        "china": "chinese",
        "england": "english",
        "emirates": "emirati",
        "france": "french",
        "germany": "german",
        "india": "indian",
        "italy": "italian",
        "japan": "japanese",
        "korea": "korean",
        "pakistan": "pakistani",
        "spain": "spanish",
        "turkey": "turkish",
        "kingdom": "british",
        "uae": "emirati",
        "usa": "american",
    }
    for word in list(words):
        if word in demonyms:
            words.append(demonyms[word])
    return list(dict.fromkeys(words))


def mentions_destination(title: str, snippet: str, destination: str):
    haystack_text = f"{title} {snippet}".lower()
    destination_clean = safe_text(destination).lower()
    if destination_clean and destination_clean in haystack_text:
        return True
    haystack = slugify(haystack_text).split("_")
    haystack_words = set(haystack)
    words = destination_words(destination)
    if not words:
        return True
    return any(word in haystack_words for word in words)


def wikipedia_search_results(query: str, limit: int = 10):
    try:
        response = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit,
                "srprop": "snippet",
                "format": "json",
            },
            headers={"User-Agent": "AiTravelPlan/1.0"},
            timeout=15,
        )
        response.raise_for_status()
        results = []
        for item in response.json().get("query", {}).get("search", []):
            snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
            results.append({"title": item.get("title", ""), "snippet": snippet})
        return results
    except Exception:
        return []


def discover_destination_places(destination: str):
    destination_clean = safe_text(destination) or "Destination"
    cache_key = destination_clean.lower()
    if cache_key in DESTINATION_PLACE_CACHE:
        return DESTINATION_PLACE_CACHE[cache_key]

    queries = [
        f"tourist attractions in {destination_clean}",
        f"landmarks in {destination_clean}",
        f"historic sites in {destination_clean}",
        f"museums in {destination_clean}",
        f"parks in {destination_clean}",
        f"architecture in {destination_clean}",
        f"old city {destination_clean}",
    ]

    places = []
    seen = set()
    for query in queries:
        for result in wikipedia_search_results(query, limit=12):
            title = result.get("title", "")
            snippet = result.get("snippet", "")
            place = clean_wikipedia_title(title)
            key = place.lower()
            if key in seen or not is_place_like_title(place, destination_clean):
                continue
            if not mentions_destination(place, snippet, destination_clean):
                continue
            if not looks_like_physical_place(place, snippet, destination_clean):
                continue
            places.append(place)
            seen.add(key)
            if len(places) >= 36:
                DESTINATION_PLACE_CACHE[cache_key] = places
                return places

    DESTINATION_PLACE_CACHE[cache_key] = places
    return places


def group_discovered_places(destination: str, total_days: int):
    destination_clean = safe_text(destination) or "Destination"
    known_contexts = known_destination_contexts(destination_clean)
    if known_contexts:
        return [known_contexts[idx % len(known_contexts)] for idx in range(total_days)]

    discovered = discover_destination_places(destination_clean)
    if not discovered:
        discovered = [
            f"{destination_clean} City Center",
            f"Historic Landmark in {destination_clean}",
            f"Main Museum in {destination_clean}",
            f"Local Market in {destination_clean}",
            f"Scenic Viewpoint in {destination_clean}",
            f"Old Town in {destination_clean}",
        ]

    contexts = []
    for idx in range(total_days):
        start = (idx * 4) % len(discovered)
        places = []
        offset = 0
        while len(places) < 4 and offset < len(discovered) + 4:
            place = discovered[(start + offset) % len(discovered)]
            if place not in places:
                places.append(place)
            offset += 1
        while len(places) < 4:
            places.append(f"{destination_clean} Highlight {len(places) + 1}")
        contexts.append((destination_clean, places))
    return contexts


def fallback_day_title(destination: str, location: str, places: list[str], day_index: int, total_days: int):
    destination_clean = safe_text(destination) or "Destination"
    city = safe_text(location).split(",", 1)[0] or destination_clean
    city_key = city.lower()
    places_key = " ".join(places).lower()

    if day_index == 0 and "badshahi" in places_key:
        return "Badshahi Mosque & Lahore Fort"
    if day_index == 0:
        return f"Arrival in {city}"
    if day_index == total_days - 1:
        return f"Departure from {destination_clean}"

    place_titles = [
        ("walled city", "Walled City Heritage Walk"),
        ("shalimar", "Gardens, Museums & Bazaars"),
        ("emporium", "Modern Lahore Shopping & Shrines"),
        ("wagah", "Wagah Border & Lahore Parks"),
        ("airport", f"Departure from {destination_clean}"),
    ]
    for key, title in place_titles:
        if key in places_key:
            return title

    known_titles = {
        "brussels": "The Heart of Brussels",
        "bruges": "Fairytale Day Trip to Bruges",
        "ghent": "Day Trip to Ghent",
        "antwerp": "Antwerp Art & Architecture",
        "old lahore": "Walled City Heritage Walk",
        "lahore": "Lahore Landmarks & Local Culture",
    }
    for key, title in known_titles.items():
        if key in city_key:
            return title

    if len(places) >= 2:
        return f"{places[0]} & {places[1]}"
    if places:
        return f"{city}: {places[0]}"
    return f"{city} Highlights"


def fallback_plan(destination: str, start_date: date, end_date: date):
    total_days = max((end_date - start_date).days + 1, 1)
    contexts = group_discovered_places(destination, total_days)
    days = []
    for idx in range(total_days):
        current_date = start_date + timedelta(days=idx)
        location, places = contexts[idx]
        title = fallback_day_title(destination, location, places, idx, total_days)

        days.append(
            {
                "date": current_date,
                "title": title,
                "location": location,
                "places": places,
                "morning": f"Start with {places[0]} and {places[1]} in {location}, focusing on the destination's most recognizable culture, history, and photo stops.",
                "afternoon": f"Continue toward {places[2]} for local character, museums, food, shopping, or neighborhood exploration connected to {location}.",
                "evening": f"End near {places[3]} with a scenic walk, dinner, and an easy transfer back to the hotel.",
            }
        )
    return days


def parse_ai_plan(raw_text: str, destination: str, start_date: date, end_date: date):
    total_days = max((end_date - start_date).days + 1, 1)
    expected_dates = [start_date + timedelta(days=i) for i in range(total_days)]
    days = []
    current = None

    for line in [item.strip() for item in raw_text.splitlines() if item.strip()]:
        lowered = line.lower()
        if lowered.startswith("day "):
            if current:
                days.append(current)
            current = {"title": "", "location": "", "places": "", "morning": "", "afternoon": "", "evening": ""}
        elif lowered.startswith("title:"):
            current = current or {"title": "", "location": "", "places": "", "morning": "", "afternoon": "", "evening": ""}
            current["title"] = line.split(":", 1)[1].strip()
        elif lowered.startswith("location:"):
            current = current or {"title": "", "location": "", "places": "", "morning": "", "afternoon": "", "evening": ""}
            current["location"] = line.split(":", 1)[1].strip()
        elif lowered.startswith("places:"):
            current = current or {"title": "", "location": "", "places": "", "morning": "", "afternoon": "", "evening": ""}
            current["places"] = line.split(":", 1)[1].strip()
        elif lowered.startswith("morning:"):
            current = current or {"title": "", "location": "", "places": "", "morning": "", "afternoon": "", "evening": ""}
            current["morning"] = line.split(":", 1)[1].strip()
        elif lowered.startswith("afternoon:"):
            current = current or {"title": "", "location": "", "places": "", "morning": "", "afternoon": "", "evening": ""}
            current["afternoon"] = line.split(":", 1)[1].strip()
        elif lowered.startswith("evening:"):
            current = current or {"title": "", "location": "", "places": "", "morning": "", "afternoon": "", "evening": ""}
            current["evening"] = line.split(":", 1)[1].strip()
        elif current:
            if current["evening"]:
                current["evening"] = f"{current['evening']} {line}".strip()
            elif current["afternoon"]:
                current["afternoon"] = f"{current['afternoon']} {line}".strip()
            else:
                current["morning"] = f"{current['morning']} {line}".strip()

    if current:
        days.append(current)

    if not days:
        return fallback_plan(destination, start_date, end_date)

    normalized = []
    fallback_days = fallback_plan(destination, start_date, end_date)
    for idx, item in enumerate(days[:total_days]):
        fallback_item = fallback_days[idx]
        location = safe_text(item.get("location")) or fallback_item["location"]
        places = normalize_places(item.get("places")) or fallback_item["places"]
        normalized.append(
            {
                "date": expected_dates[idx],
                "title": item.get("title") or f"Day {idx + 1} Highlights",
                "location": location,
                "places": places,
                "morning": item.get("morning", "") or fallback_item["morning"],
                "afternoon": item.get("afternoon", "") or fallback_item["afternoon"],
                "evening": item.get("evening", "") or fallback_item["evening"],
            }
        )

    while len(normalized) < total_days:
        normalized.append(fallback_days[len(normalized)])

    return normalized

def get_ai_plan(client: str, destination: str, start_date: date, end_date: date):
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return fallback_plan(destination, start_date, end_date)

    prompt = f"""
Create a professional travel plan.

Client: {client}
Destination: {destination}
Dates: {start_date} to {end_date}

Return plain text only using this exact format:

Day 1
Title: ...
Location: specific city/neighborhood, country
Places: exact place 1, exact place 2, exact place 3, exact place 4
Morning: mention the exact place names for the morning plan.
Afternoon: mention the exact place names for the afternoon plan.
Evening: mention the exact place names for the evening plan.

Day 2
Title: ...
Location: specific city/neighborhood, country
Places: exact place 1, exact place 2, exact place 3, exact place 4
Morning: mention the exact place names for the morning plan.
Afternoon: mention the exact place names for the afternoon plan.
Evening: mention the exact place names for the evening plan.

Use real, photo-friendly locations and landmarks. Keep each day practical and realistic.
"""

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "anthropic/claude-3-haiku",
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        raw_text = data["choices"][0]["message"]["content"]
        return parse_ai_plan(raw_text, destination, start_date, end_date)
    except Exception:
        return fallback_plan(destination, start_date, end_date)


def image_fingerprint(image_bytes: bytes) -> str | None:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image = image.convert("L").resize((8, 8))
            pixels = list(image.getdata())
            average = sum(pixels) / len(pixels)
            return "".join("1" if pixel >= average else "0" for pixel in pixels)
    except (UnidentifiedImageError, OSError):
        return None


def download_image(url: str, used_hashes: set[str] | None = None):
    for attempt in range(2):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "AiTravelPlan/1.0"},
                timeout=25,
            )
            if response.status_code == 429 and attempt == 0:
                time.sleep(1.2)
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "image" not in content_type:
                return None

            image_bytes = response.content
            image_hash = hashlib.md5(image_bytes).hexdigest()
            if used_hashes is not None and image_hash in used_hashes:
                return None

            if used_hashes is not None:
                used_hashes.add(image_hash)

            suffix = ".png" if "png" in content_type else ".jpg"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                temp_file.write(image_bytes)
                return temp_file.name
        except Exception:
            if attempt == 0:
                time.sleep(0.4)
                continue
            return None
    return None


def get_cover_image(destination: str, used_hashes: set[str] | None = None):
    seed = slugify(destination)
    for url in [
        f"https://loremflickr.com/1200/700/{seed},city?lock={image_lock(seed + '_cover')}",
        f"https://picsum.photos/seed/{seed}_cover/1200/700",
    ]:
        path = download_image(url, used_hashes)
        if path:
            return path
    return None


def image_lock(value: str) -> int:
    digest = hashlib.md5(value.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100000


CURATED_PLACE_IMAGE_FILES = {
    "badshahi mosque": ["Badshahi Mosque, Lahore I.jpg", "Badshahi Mosque (Lahore).jpg"],
    "lahore fort": ["Lahore Fort view from Baradari.jpg", "Alamgiri Gate, Lahore Fort.jpg"],
    "minar e pakistan": ["Minar-e-Pakistan(Lahore).jpg", "Minar-e-Pakistan Lahore,Pakistan.jpg"],
    "food street lahore": ["Food Street, Lahore - panoramio.jpg", "Food street lahore by kamran.jpg"],
    "walled city of lahore": ["Old Lahore.jpg", "Walled city lahore and its colors.jpg"],
    "delhi gate lahore": ["Delhi Gate, Lahore.jpg", "Delhi Gate - Lahore - Pakistan.jpg"],
    "shahi hammam": ["Outside Shahi Hammam (Wazir Khan's hammam).jpg", "Shahi Hammam Arches.jpg"],
    "wazir khan mosque": ["Courtyard of Wazir Khan Mosque, Lahore.jpg", "Masjid Wazir Khan of Lahore.jpg"],
    "shalimar gardens lahore": ["Shalimar Gardens (Lahore).jpg", "Shalimar Gardens (Lahore) 1.jpg"],
    "lahore museum": ["Lahore Museum, Lahore.jpg", "Front View of Lahore Museum.jpg"],
    "anarkali bazaar": ["Inside view of anarkali bazar.jpg", "Street view anarkali lahore.jpg"],
    "liberty market lahore": ["MM Alam Road 1.jpg", "MM Alam Road 2.jpg"],
    "data darbar": ["Data Darbar 2.jpg", "Data Darbar Shrine @ Lahore (15285413030).jpg"],
    "emporium mall lahore": ["Emporium Mall.jpg", "Emporium Mall 2.jpg", "The Boulevard, Emporium Mall, Lahore.jpg"],
    "packages mall lahore": ["Packages Mall, Lahore 10.jpg", "Packages Mall, Lahore 11.jpg"],
    "mm alam road lahore": ["MM Alam Road 1.jpg", "MM Alam Road 2.jpg", "Freddy's Cafe on M.M Alam road Lahore.jpg"],
    "wagah border": ["Wagah Border 2023.jpg", "Wagah border ceremony2.jpg"],
    "lahore zoo": ["Lahore Zoo Entrance.jpg"],
}


def canonical_place_key(place: str):
    key = slugify(place).replace("_", " ")
    key = key.replace("minar e pakistan", "minar e pakistan")
    return key.strip()


def curated_place_urls(place: str):
    files = CURATED_PLACE_IMAGE_FILES.get(canonical_place_key(place), [])
    return [
        f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(file_name)}?width=900"
        for file_name in files
    ]


IMAGE_KEYWORD_STOP_WORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "the", "to",
    "pakistan", "lahore", "belgium", "brussels", "photo", "image", "view",
}


def image_keywords(value: str):
    words = [word for word in slugify(value).split("_") if word]
    keywords = [word for word in words if word not in IMAGE_KEYWORD_STOP_WORDS and len(word) >= 2]
    return keywords or [word for word in words if word not in {"a", "an", "and", "of", "the"}]


def title_matches_place(title: str, place: str):
    title_words = set(slugify(title).split("_"))
    keywords = image_keywords(place)
    if not keywords:
        return True
    matches = sum(1 for keyword in keywords if keyword in title_words)
    required = 1 if len(keywords) <= 2 else 2
    return matches >= required


def get_wikimedia_place_image_urls(place: str, destination: str):
    search_terms = [f"{place} {destination}", place]
    urls = []
    seen_urls = set()

    for url in curated_place_urls(place):
        urls.append(url)
        seen_urls.add(url)

    for term in search_terms:
        try:
            response = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": term,
                    "gsrnamespace": 6,
                    "gsrlimit": 14,
                    "prop": "imageinfo",
                    "iiprop": "url|mime",
                    "iiurlwidth": 900,
                    "format": "json",
                },
                headers={"User-Agent": "AiTravelPlan/1.0"},
                timeout=18,
            )
            response.raise_for_status()
            pages = list(response.json().get("query", {}).get("pages", {}).values())
            for page in pages:
                title = page.get("title", "")
                info = (page.get("imageinfo") or [{}])[0]
                mime = info.get("mime", "")
                url = info.get("thumburl") or info.get("url")
                if not mime.startswith("image/") or not url or url in seen_urls:
                    continue
                if title_matches_place(title, place):
                    urls.append(url)
                    seen_urls.add(url)
        except Exception:
            continue

    return urls


def create_place_card(place: str, location: str):
    try:
        image = Image.new("RGB", (900, 600), "#e8eef5")
        # Keep this fallback honest: a labeled card is better than a wrong beach/Eiffel photo.
        from PIL import ImageDraw
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 900, 600), fill="#e8eef5")
        draw.rectangle((0, 0, 900, 115), fill="#1f4e79")
        draw.text((38, 36), safe_text(place)[:55], fill="white")
        draw.text((38, 150), safe_text(location)[:70], fill="#1f2937")
        draw.text((38, 215), "Photo not available from Wikimedia Commons", fill="#475569")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
            image.save(temp_file.name, format="JPEG", quality=92)
            return temp_file.name
    except Exception:
        return None


def get_day_images(destination: str, title: str, location: str, places: list[str], day_index: int, used_hashes: set[str]):
    paths = []
    normalized_places = normalize_places(places)
    place_urls = [(place, get_wikimedia_place_image_urls(place, destination)) for place in normalized_places[:4]]

    for _, urls in place_urls:
        for url in urls:
            place_path = download_image(url, used_hashes)
            if place_path:
                paths.append(place_path)
                break
        if len(paths) >= 3:
            return paths[:3]

    for _, urls in place_urls:
        for url in urls:
            place_path = download_image(url, used_hashes)
            if place_path:
                paths.append(place_path)
            if len(paths) >= 3:
                return paths[:3]

    for fallback_place in [location, destination]:
        for url in get_wikimedia_place_image_urls(fallback_place, destination):
            place_path = download_image(url, used_hashes)
            if place_path:
                paths.append(place_path)
            if len(paths) >= 3:
                return paths[:3]

    return paths[:3]


class TravelPlanPDF(FPDF):
    def header(self):
        if self.page_no() >= 3:
            self.set_font("Helvetica", "B", 8)
            self.set_text_color(0, 0, 0)
            self.cell(0, 6, f"Page {self.page_no()} of {{nb}}", align="R")
            self.ln(3)


def draw_cover_page(pdf: TravelPlanPDF, client: str, title: str, start_date: date, end_date: date, cover_path: str | None):
    pdf.add_page()
    pdf.set_fill_color(255, 255, 255)
    pdf.rect(0, 0, 210, 297, "F")

    pdf.set_xy(0, 18)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(210, 10, safe_text(client.upper()), align="C")

    if cover_path:
        pdf.image(cover_path, x=10, y=38, w=190, h=86)

    pdf.set_xy(0, 132)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(14, 44, 85)
    pdf.cell(210, 10, safe_text(title), align="C")

    pdf.set_xy(0, 146)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(53, 90, 129)
    pdf.cell(210, 8, safe_text(f"{compact_date(start_date)} - {compact_date(end_date)}"), align="C")


def draw_summary_page(pdf: TravelPlanPDF, days: list[dict]):
    pdf.add_page()
    pdf.set_xy(14, 18)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(58, 83, 101)
    pdf.cell(0, 10, "Trip Summary")

    pdf.set_draw_color(210, 220, 230)
    pdf.line(14, 34, 196, 34)
    pdf.set_y(46)

    for item in days:
        if pdf.get_y() > 260:
            pdf.add_page()
            pdf.set_y(18)

        y = pdf.get_y()
        location = safe_text(item.get("location", ""))
        places = normalize_places(item.get("places", []))[:3]
        detail_parts = []
        if location:
            detail_parts.append(location)
        if places:
            detail_parts.append(", ".join(places))
        detail = " - ".join(detail_parts)
        if len(detail) > 120:
            detail = f"{detail[:117]}..."

        pdf.set_draw_color(210, 220, 230)
        pdf.line(14, y + 3, 34, y + 3)

        pdf.set_xy(40, y)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(58, 83, 101)
        pdf.cell(0, 6, safe_text(format_day_label(item["date"])))

        pdf.set_xy(40, y + 7)
        pdf.set_font("Helvetica", "", 10.5)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 5, safe_text(item["title"]))

        if detail:
            pdf.set_xy(40, y + 13)
            pdf.set_font("Helvetica", "", 8.5)
            pdf.set_text_color(82, 92, 105)
            pdf.multi_cell(145, 4, safe_text(detail))

        pdf.set_y(y + 23)


def draw_calendar_marker(pdf: TravelPlanPDF, x: float, y: float):
    pdf.set_draw_color(45, 62, 78)
    pdf.set_fill_color(255, 255, 255)
    pdf.ellipse(x - 4.5, y - 1, 9, 9, "DF")

    icon_x = x - 2.4
    icon_y = y + 1.2
    pdf.set_draw_color(45, 62, 78)
    pdf.rect(icon_x, icon_y, 4.8, 4.2)
    pdf.line(icon_x, icon_y + 1.3, icon_x + 4.8, icon_y + 1.3)
    pdf.set_line_width(0.35)
    pdf.line(icon_x + 1.2, icon_y - 0.5, icon_x + 1.2, icon_y + 0.8)
    pdf.line(icon_x + 3.6, icon_y - 0.5, icon_x + 3.6, icon_y + 0.8)
    pdf.set_line_width(0.2)


def draw_image_grid(pdf: TravelPlanPDF, image_paths: list[str], x: float, y: float):
    if not image_paths:
        return y

    positions = [
        (x, y, 78, 52),
        (x + 80, y, 39, 25),
        (x + 80, y + 27, 39, 25),
    ]

    for idx, image_path in enumerate(image_paths[:3]):
        px, py, pw, ph = positions[idx]
        try:
            pdf.image(image_path, x=px, y=py, w=pw, h=ph)
        except Exception:
            continue

    return y + 56


def draw_bullet_line(pdf: TravelPlanPDF, label: str, content: str, x: float):
    clean_content = safe_text(content)
    if not clean_content:
        return

    bullet_x = x
    text_x = x + 4
    y = pdf.get_y()
    right_edge = 194

    pdf.set_xy(bullet_x, y + 1.5)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(3, 3, chr(149))

    pdf.set_xy(text_x, y)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.write(4.5, f"{label}: ")
    content_x = pdf.get_x()
    content_width = max(80, right_edge - content_x)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.multi_cell(content_width, 4.5, clean_content)


def draw_day_block(pdf: TravelPlanPDF, item: dict, image_paths: list[str]):
    if pdf.get_y() > 222:
        pdf.add_page()

    left_margin = 24
    marker_x = 28
    content_x = 43

    pdf.set_draw_color(196, 208, 220)
    pdf.line(left_margin, pdf.get_y(), 196, pdf.get_y())
    pdf.ln(4)

    pdf.set_x(left_margin)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(52, 86, 129)
    pdf.cell(0, 7, safe_text(format_day_label(item["date"])))
    pdf.ln(8)

    block_start_y = pdf.get_y()
    draw_calendar_marker(pdf, marker_x, block_start_y)

    pdf.set_xy(content_x, block_start_y - 1)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 5, safe_text(item["title"]))
    pdf.ln(5)

    places_text = ", ".join(normalize_places(item.get("places", [])))
    draw_bullet_line(pdf, "Location", item.get("location", ""), content_x + 1)
    draw_bullet_line(pdf, "Places", places_text, content_x + 1)

    for label, content in [
        ("Morning", item["morning"]),
        ("Afternoon", item["afternoon"]),
        ("Evening", item["evening"]),
    ]:
        draw_bullet_line(pdf, label, content, content_x + 1)

    if image_paths:
        image_y = pdf.get_y() + 3
        last_y = draw_image_grid(pdf, image_paths, content_x, image_y)
        pdf.set_y(last_y + 8)
    else:
        pdf.ln(8)

    block_end_y = pdf.get_y()
    pdf.set_draw_color(210, 220, 230)
    pdf.line(marker_x, block_start_y + 8, marker_x, block_end_y - 2)

def build_pdf(client: str, title: str, destination: str, start_date: date, end_date: date, days: list[dict]) -> bytes:
    used_image_hashes: set[str] = set()
    cover_path = get_cover_image(destination, used_image_hashes)
    day_images = [
        get_day_images(
            destination,
            item["title"],
            item.get("location", destination),
            item.get("places", []),
            idx + 1,
            used_image_hashes,
        )
        for idx, item in enumerate(days)
    ]

    pdf = TravelPlanPDF("P", "mm", "A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=16)

    draw_cover_page(pdf, client, title, start_date, end_date, cover_path)
    draw_summary_page(pdf, days)

    pdf.add_page()
    pdf.set_y(12)
    for idx, item in enumerate(days):
        draw_day_block(pdf, item, day_images[idx])

    output = pdf.output(dest="S")
    if isinstance(output, str):
        return output.encode("latin-1", errors="ignore")
    if isinstance(output, bytearray):
        return bytes(output)
    return output


st.markdown("<div class='main-header'>Ai <span>Travel</span> Plan</div>", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### Trip Details")
    client_name = st.text_input("Client Name", "")
    destination = st.text_input("Destination", "")
    plan_title = st.text_input("Plan Title", "Travel Plan")
    start_date = st.date_input("Departure", datetime.now().date())
    end_date = st.date_input("Return", datetime.now().date() + timedelta(days=5))
    generate = st.button("Create Travel Plan")

if end_date < start_date:
    st.error("Return date cannot be before departure date.")
    st.stop()

if generate:
    if not destination.strip():
        st.error("Please enter a destination before creating the travel plan.")
        st.stop()

    progress = st.progress(0)
    status = st.empty()

    status.write("Preparing travel plan...")
    progress.progress(15)
    itinerary_days = get_ai_plan(client_name, destination, start_date, end_date)

    status.write("Creating PDF and collecting photos...")
    progress.progress(55)
    pdf_bytes = build_pdf(
        client=client_name,
        title=plan_title,
        destination=destination,
        start_date=start_date,
        end_date=end_date,
        days=itinerary_days,
    )

    progress.progress(100)
    status.empty()
    progress.empty()

    st.download_button(
        label="Download PDF",
        data=BytesIO(pdf_bytes),
        file_name=f"{slugify(destination)}_travel_plan.pdf",
        mime="application/pdf",
    )
