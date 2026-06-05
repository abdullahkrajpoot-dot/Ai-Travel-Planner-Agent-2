import hashlib
import logging
import os
import re
import tempfile
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from io import BytesIO
from urllib.parse import quote, unquote

import requests
import streamlit as st
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError


logging.basicConfig(
    filename="image-generation.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("ai_travel_images")
IMAGE_REQUEST_TIMEOUT = 12
IMAGE_CACHE: dict[str, tuple[str, str] | None] = {}
IMAGE_CACHE_LOCK = threading.Lock()
USED_IMAGE_URLS: set[str] = set()
USED_IMAGE_URLS_LOCK = threading.Lock()


def log_image_event(message: str):
    print(message)
    LOGGER.info(message)


st.set_page_config(page_title="Ai Travel Plan", layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
    #MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"], .stDeployButton { display: none !important; }
    header {
        background: #0b0f16 !important;
        color: #ffffff !important;
    }
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
    [data-testid="stSidebarCollapseButton"],
    [data-testid="stSidebarCollapsedControl"] {
        color: #ffffff !important;
    }
    [data-testid="stSidebarCollapseButton"] svg,
    [data-testid="stSidebarCollapsedControl"] svg {
        color: #ffffff !important;
        fill: #ffffff !important;
        stroke: #ffffff !important;
    }
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
    "dallas": [
        ("Dallas, Texas", ["Dallas Arboretum", "Reunion Tower", "The Sixth Floor Museum at Dealey Plaza", "Dallas Arts District"]),
        ("Dallas, Texas", ["Klyde Warren Park", "Dallas Museum of Art", "Dealey Plaza", "Perot Museum of Nature and Science"]),
        ("Dallas, Texas", ["Bishop Arts District", "Pioneer Plaza", "Nasher Sculpture Center", "White Rock Lake"]),
    ],
    "florida": [
        ("Orlando, Florida", ["Walt Disney World", "Universal Studios Florida", "SeaWorld Orlando", "Lake Eola Park"]),
        ("Merritt Island, Florida", ["Kennedy Space Center Visitor Complex", "Cape Canaveral", "Cocoa Beach", "Merritt Island National Wildlife Refuge"]),
        ("Miami, Florida", ["South Beach Miami", "Vizcaya Museum and Gardens", "Wynwood Walls", "Bayside Marketplace"]),
        ("Everglades, Florida", ["Everglades National Park", "Shark Valley", "Anhinga Trail", "Big Cypress National Preserve"]),
        ("Key West, Florida", ["Duval Street Key West", "Southernmost Point Buoy", "Mallory Square", "Ernest Hemingway House"]),
        ("Homestead, Florida", ["Coral Castle", "Florida Citrus Tower", "Florida Museum of Natural History", "Bok Tower Gardens"]),
    ],
    "new york": [
        ("New York, USA", ["Times Square", "Statue of Liberty", "Central Park", "Empire State Building"]),
        ("New York, USA", ["Metropolitan Museum of Art", "Brooklyn Bridge", "Grand Central Terminal", "Rockefeller Center"]),
        ("New York, USA", ["One World Trade Center", "High Line", "Museum of Modern Art", "Fifth Avenue"]),
    ],
    "texas": [
        ("Austin, Texas", ["Texas State Capitol", "Zilker Park", "Bullock Texas State History Museum", "South Congress Avenue"]),
        ("San Antonio, Texas", ["The Alamo", "River Walk San Antonio", "San Antonio Missions National Historical Park", "Historic Market Square"]),
        ("Houston, Texas", ["Space Center Houston", "Houston Museum of Natural Science", "Museum of Fine Arts Houston", "Houston Theater District"]),
        ("Dallas, Texas", ["Dallas Arboretum", "Reunion Tower", "The Sixth Floor Museum at Dealey Plaza", "Dallas Arts District"]),
        ("Fort Worth, Texas", ["Fort Worth Stockyards", "Kimbell Art Museum", "Sundance Square", "Fort Worth Water Gardens"]),
    ],
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


US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana",
    "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts",
    "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska",
    "nevada", "new hampshire", "new jersey", "new mexico", "new york state",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "washington state", "west virginia", "wisconsin", "wyoming",
}


COUNTRY_HINTS = {
    "belgium", "france", "germany", "italy", "spain", "pakistan", "india", "japan",
    "china", "turkey", "united kingdom", "uae", "united arab emirates", "canada",
    "mexico", "brazil", "australia", "thailand", "egypt", "morocco",
}


PLACE_IMAGE_CONTEXTS = {
    "times square": "New York",
    "statue of liberty": "New York",
    "central park": "New York",
    "empire state building": "New York",
    "metropolitan museum of art": "New York",
    "brooklyn bridge": "New York",
    "grand central terminal": "New York",
    "rockefeller center": "New York",
    "one world trade center": "New York",
    "high line": "New York",
    "museum of modern art": "New York",
    "fifth avenue": "New York",
    "texas state capitol": "Austin",
    "zilker park": "Austin",
    "bullock texas state history museum": "Austin",
    "south congress avenue": "Austin",
    "the alamo": "San Antonio",
    "river walk san antonio": "San Antonio",
    "san antonio missions national historical park": "San Antonio",
    "historic market square": "San Antonio",
    "space center houston": "Houston",
    "houston museum of natural science": "Houston",
    "museum of fine arts houston": "Houston",
    "houston theater district": "Houston",
    "dallas arboretum": "Dallas",
    "reunion tower": "Dallas",
    "the sixth floor museum at dealey plaza": "Dallas",
    "dallas arts district": "Dallas",
    "klyde warren park": "Dallas",
    "dallas museum of art": "Dallas",
    "dealey plaza": "Dallas",
    "perot museum of nature and science": "Dallas",
    "bishop arts district": "Dallas",
    "pioneer plaza": "Dallas",
    "nasher sculpture center": "Dallas",
    "white rock lake": "Dallas",
    "fort worth stockyards": "Fort Worth",
    "kimbell art museum": "Fort Worth",
    "sundance square": "Fort Worth",
    "fort worth water gardens": "Fort Worth",
    "walt disney world": "Orlando",
    "universal studios florida": "Orlando",
    "seaworld orlando": "Orlando",
    "lake eola park": "Orlando",
    "kennedy space center visitor complex": "Merritt Island",
    "cape canaveral": "Florida",
    "cocoa beach": "Florida",
    "merritt island national wildlife refuge": "Florida",
    "south beach miami": "Miami",
    "vizcaya museum and gardens": "Miami",
    "wynwood walls": "Miami",
    "bayside marketplace": "Miami",
    "everglades national park": "Florida",
    "shark valley": "Everglades",
    "anhinga trail": "Everglades",
    "big cypress national preserve": "Florida",
    "duval street key west": "Key West",
    "southernmost point buoy": "Key West",
    "mallory square": "Key West",
    "ernest hemingway house": "Key West",
    "coral castle": "Florida",
    "florida citrus tower": "Florida",
    "florida museum of natural history": "Gainesville",
    "bok tower gardens": "Florida",
}


def normalize_places(value):
    if isinstance(value, list):
        return [safe_text(item) for item in value if safe_text(item)]
    if not value:
        return []
    return [safe_text(item) for item in re.split(r",|;|\|", str(value)) if safe_text(item)]


DESTINATION_PLACE_CACHE: dict[str, list[str]] = {}
PLACE_DESTINATION_VALIDATION_CACHE: dict[str, bool] = {}


WIKIPEDIA_SKIP_TERMS = (
    "list of", "lists of", "tourism in", "tourist attraction", "tourist attractions",
    "landmarks in", "history of", "geography of", "economy of", "demographics of",
    "transport in", "outline of", "culture of", "politics of", "flag of",
    "coat of arms", "disambiguation", "timeline of", "covid", "syndrome",
    "visa policy", "foreign relations", "index of", "bibliography of", "museums in",
    "state park", "station", "bombing", "attack", "shooting", "massacre", "disaster",
    "accident", "incident", "protest", "riot", "war", "battle",
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
    "video game", "writer", "bombing", "attack", "shooting", "massacre", "disaster",
    "accident", "incident", "protest", "riot", "war", "battle",
)


GENERIC_PLACE_TERMS = (
    "city center", "city centre", "downtown", "highlight", "landmark in", "main museum",
    "local market", "scenic viewpoint", "old town", "old city", "gulf coast",
    "lake landmark", "historic landmark", "tourist spot", "attraction in",
)


def destination_profile(destination: str):
    clean = safe_text(destination)
    key = clean.lower()
    if key == "new york":
        profile_type = "city"
    elif key in US_STATE_NAMES or f"{key} state" in US_STATE_NAMES:
        profile_type = "state"
    elif key in COUNTRY_HINTS:
        profile_type = "country"
    elif "," in clean:
        profile_type = "city"
    else:
        profile_type = "city"
    log_image_event(f"destination profile: {clean} -> {profile_type}")
    return {"name": clean, "type": profile_type}


def is_generic_or_placeholder_place(place: str):
    lowered = safe_text(place).lower()
    if not lowered:
        return True
    if re.search(r"\b(highlight|place|spot|site)\s*\d+\b", lowered):
        return True
    if re.search(r"\b(19|20)\d{2}\b", lowered):
        return True
    return any(term in lowered for term in GENERIC_PLACE_TERMS)


def known_destination_contexts(destination: str):
    key = safe_text(destination).lower()
    for known_key, options in KNOWN_DESTINATION_PLACES.items():
        if known_key == key or known_key in key:
            return options
    return []


def known_destination_places(destination: str):
    return [
        place
        for _, places in known_destination_contexts(destination)
        for place in places
    ]


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


def parse_destinations(destination_input: str):
    destinations = []
    seen = set()
    for value in safe_text(destination_input).split(","):
        destination = value.strip()
        key = destination.lower()
        if destination and key not in seen:
            destinations.append(destination)
            seen.add(key)
    log_image_event(f"parsed destinations: {destinations}")
    return destinations


def allocate_destination_dates(destinations: list[str], start_date: date, end_date: date):
    total_days = max((end_date - start_date).days + 1, 1)
    destination_count = max(len(destinations), 1)
    base_days = max(total_days // destination_count, 1)
    extra_days = max(total_days - (base_days * destination_count), 0)
    ranges = []
    current_start = start_date
    for idx, destination in enumerate(destinations):
        days_for_destination = base_days + (1 if idx < extra_days else 0)
        if idx == destination_count - 1:
            current_end = end_date
        else:
            current_end = min(current_start + timedelta(days=days_for_destination - 1), end_date)
        ranges.append((destination, current_start, current_end))
        current_start = current_end + timedelta(days=1)
    return ranges


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

    known_places = known_destination_places(destination_clean)
    if known_places:
        DESTINATION_PLACE_CACHE[cache_key] = known_places
        return known_places

    profile = destination_profile(destination_clean)
    if profile["type"] == "state":
        queries = [
            f"top tourist attractions in {destination_clean}",
            f"famous landmarks in {destination_clean}",
            f"most visited museums in {destination_clean}",
            f"historic sites in {destination_clean}",
        ]
    elif profile["type"] == "country":
        queries = [
            f"top tourist attractions in {destination_clean}",
            f"famous landmarks in {destination_clean}",
            f"UNESCO sites in {destination_clean}",
            f"most visited museums in {destination_clean}",
        ]
    else:
        queries = [
            f"top tourist attractions in {destination_clean}",
            f"famous landmarks in {destination_clean}",
            f"historic sites in {destination_clean}",
            f"museums in {destination_clean}",
            f"parks in {destination_clean}",
            f"architecture in {destination_clean}",
        ]

    places = []
    seen = set()
    for query in queries:
        for result in wikipedia_search_results(query, limit=12):
            title = result.get("title", "")
            snippet = result.get("snippet", "")
            place = clean_wikipedia_title(title)
            key = place.lower()
            if is_generic_or_placeholder_place(place):
                continue
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


def place_belongs_to_destination(place: str, destination: str):
    clean_place = clean_image_subject(place)
    clean_destination = safe_text(destination) or "Destination"
    if not clean_place or is_generic_or_placeholder_place(clean_place):
        return False

    cache_key = f"{clean_place.lower()}|{clean_destination.lower()}"
    if cache_key in PLACE_DESTINATION_VALIDATION_CACHE:
        return PLACE_DESTINATION_VALIDATION_CACHE[cache_key]

    destination_key = slugify(clean_destination)
    place_key = slugify(clean_place)
    known_places = {slugify(place_name) for place_name in known_destination_places(clean_destination)}
    discovered_places = {slugify(item) for item in discover_destination_places(clean_destination)}
    if place_key in known_places or place_key in discovered_places:
        PLACE_DESTINATION_VALIDATION_CACHE[cache_key] = True
        return True
    if known_places:
        PLACE_DESTINATION_VALIDATION_CACHE[cache_key] = False
        log_image_event(f"place validation failed: '{clean_place}' is not in the curated attraction set for '{clean_destination}'")
        return False

    for result in wikipedia_search_results(f"{clean_place} {clean_destination}", limit=5):
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        haystack = f"{title} {snippet}".lower()
        popularity_hint = any(term in haystack for term in ("landmark", "museum", "historic", "tourist", "attraction", "visited", "famous", "district", "park", "square", "palace", "tower"))
        if title_matches_place(title, clean_place) and mentions_destination(title, snippet, clean_destination) and popularity_hint:
            PLACE_DESTINATION_VALIDATION_CACHE[cache_key] = True
            return True

    PLACE_DESTINATION_VALIDATION_CACHE[cache_key] = False
    log_image_event(f"place validation failed: '{clean_place}' does not belong to '{clean_destination}'")
    return False


def validated_destination_places(places: list[str], destination: str, minimum: int = 4):
    destination_clean = safe_text(destination) or "Destination"
    discovered = discover_destination_places(destination_clean)
    valid_places = []
    seen = set()

    for place in normalize_places(places):
        key = slugify(place)
        if key in seen:
            continue
        if place_belongs_to_destination(place, destination_clean):
            valid_places.append(place)
            seen.add(key)

    for place in discovered:
        key = slugify(place)
        if len(valid_places) >= minimum:
            break
        if key not in seen and place_belongs_to_destination(place, destination_clean):
            valid_places.append(place)
            seen.add(key)

    log_image_event(f"places selected for {destination_clean}: {valid_places}")
    return valid_places


def destination_place_pool(destination: str):
    destination_clean = safe_text(destination) or "Destination"
    pool = []
    seen = set()

    for location, places in known_destination_contexts(destination_clean):
        for place in places:
            key = canonical_place_key(place)
            if key not in seen and place_belongs_to_destination(place, destination_clean):
                pool.append(place)
                seen.add(key)

    for place in discover_destination_places(destination_clean):
        key = canonical_place_key(place)
        if key not in seen and place_belongs_to_destination(place, destination_clean):
            pool.append(place)
            seen.add(key)

    return pool


def enforce_unique_day_places(days: list[dict], destination: str):
    destination_clean = safe_text(destination) or "Destination"
    pool = destination_place_pool(destination_clean)
    used_place_keys = set()
    used_title_keys = set()
    cleaned_days = []

    for idx, day in enumerate(days):
        item = dict(day)
        if is_departure_day(item, day_index=idx + 1, total_days=len(days)):
            cleaned_days.append(item)
            continue

        unique_places = []
        local_seen = set()
        for place in validated_destination_places(item.get("places", []), destination_clean, minimum=0):
            key = canonical_place_key(place)
            if key in used_place_keys or key in local_seen:
                log_image_event(f"repeated place removed: {place}")
                continue
            unique_places.append(place)
            local_seen.add(key)

        for place in pool:
            if len(unique_places) >= 4:
                break
            key = canonical_place_key(place)
            if key not in used_place_keys and key not in local_seen:
                unique_places.append(place)
                local_seen.add(key)

        for place in unique_places:
            used_place_keys.add(canonical_place_key(place))

        if unique_places:
            item["places"] = unique_places
            item["morning"], item["afternoon"], item["evening"] = premium_day_copy(
                item.get("location", destination_clean),
                unique_places,
            )
            title = fallback_day_title(
                destination_clean,
                item.get("location", destination_clean),
                unique_places,
                idx,
                len(days),
            )
            title_key = canonical_place_key(title)
            if title_key in used_title_keys:
                title = f"{safe_text(item.get('location', destination_clean)).split(',', 1)[0]}: {unique_places[0]}"
                title_key = canonical_place_key(title)
            item["title"] = title
            used_title_keys.add(title_key)
        else:
            item["places"] = []
            item["title"] = f"{safe_text(item.get('location', destination_clean)).split(',', 1)[0]} Free Time"
            item["morning"], item["afternoon"], item["evening"] = premium_day_copy(
                item.get("location", destination_clean),
                [],
            )

        cleaned_days.append(item)

    return cleaned_days


def group_discovered_places(destination: str, total_days: int):
    destination_clean = safe_text(destination) or "Destination"
    known_contexts = known_destination_contexts(destination_clean)
    if known_contexts:
        contexts = known_contexts[:total_days]
        while len(contexts) < total_days:
            contexts.append((destination_clean, []))
        return contexts

    discovered = discover_destination_places(destination_clean)
    if not discovered:
        log_image_event(f"no verified places discovered for {destination_clean}; generic fallback places disabled")
        return [(destination_clean, []) for _ in range(total_days)]

    contexts = []
    for idx in range(total_days):
        start = idx * 4
        places = discovered[start:start + 4]
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
        if destination_profile(destination_clean)["type"] == "state":
            return f"Arrival in {destination_clean}: {city}"
        return f"Arrival in {city}"
    place_titles = [
        ("walled city", "Walled City Heritage Walk"),
        ("shalimar", "Gardens, Museums & Bazaars"),
        ("emporium", "Modern Lahore Shopping & Shrines"),
        ("wagah", "Wagah Border & Lahore Parks"),
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
        selected = places[:4]
        if selected:
            first_places = " and ".join(selected[:2])
            afternoon_place = selected[2] if len(selected) > 2 else selected[0]
            evening_place = selected[3] if len(selected) > 3 else selected[-1]
            morning = f"Explore {first_places} in {location}, with time for photos and the main visitor areas."
            afternoon = f"Visit {afternoon_place}, then enjoy nearby cafes, galleries, or shopping streets at an easy pace."
            evening = f"End the day around {evening_place} with dinner and a relaxed walk before returning to the hotel."
        else:
            morning = f"Begin with a guided orientation walk in {location}."
            afternoon = f"Use this block for reservations, transfers, or hotel check-in time in {location}."
            evening = f"Plan dinner in a central neighborhood of {location}."

        days.append(
            {
                "date": current_date,
                "title": title,
                "location": location,
                "places": selected,
                "morning": morning,
                "afternoon": afternoon,
                "evening": evening,
            }
        )
    return days


def has_weak_generated_wording(*values: str):
    banned = (
        "connected to this destination", "verified places", "local character",
        "no unverified fallback", "exact places", "research verified",
    )
    text = " ".join(safe_text(value).lower() for value in values)
    return any(term in text for term in banned)


def premium_day_copy(location: str, places: list[str]):
    selected = normalize_places(places)
    if not selected:
        return (
            f"Settle into {location} with a comfortable orientation walk.",
            f"Keep the afternoon open for hotel check-in, reservations, or a guided overview of {location}.",
            f"Enjoy dinner in a central neighborhood and prepare for the next sightseeing day.",
        )

    first_places = " and ".join(selected[:2])
    afternoon_place = selected[2] if len(selected) > 2 else selected[0]
    evening_place = selected[3] if len(selected) > 3 else selected[-1]
    return (
        f"Explore {first_places} in {location}, leaving time for the main viewpoints and visitor areas.",
        f"Visit {afternoon_place}, then pause for lunch, shopping, or a nearby gallery at an easy pace.",
        f"End near {evening_place} with dinner and a relaxed walk before returning to the hotel.",
    )


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
        raw_places = normalize_places(item.get("places"))
        places = validated_destination_places(raw_places, destination) or fallback_item["places"]
        places_changed = [slugify(place) for place in places] != [slugify(place) for place in raw_places[:len(places)]]
        morning = item.get("morning", "") or fallback_item["morning"]
        afternoon = item.get("afternoon", "") or fallback_item["afternoon"]
        evening = item.get("evening", "") or fallback_item["evening"]
        if places_changed:
            fallback_context = fallback_plan(destination, expected_dates[idx], expected_dates[idx])[0]
            fallback_context["places"] = places
            if places:
                morning, afternoon, evening = premium_day_copy(location, places)
            else:
                morning = fallback_context["morning"]
                afternoon = fallback_context["afternoon"]
                evening = fallback_context["evening"]
        elif has_weak_generated_wording(morning, afternoon, evening):
            morning, afternoon, evening = premium_day_copy(location, places)
        normalized.append(
            {
                "date": expected_dates[idx],
                "title": item.get("title") or f"Day {idx + 1} Highlights",
                "location": location,
                "places": places,
                "morning": morning,
                "afternoon": afternoon,
                "evening": evening,
            }
        )

    while len(normalized) < total_days:
        normalized.append(fallback_days[len(normalized)])

    return enforce_unique_day_places(normalized, destination)

def get_ai_plan(client: str, destination: str, start_date: date, end_date: date):
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return enforce_unique_day_places(fallback_plan(destination, start_date, end_date), destination)

    profile = destination_profile(destination)
    known_contexts = known_destination_contexts(destination)
    if profile["type"] == "state" and known_contexts:
        context_text = "\n".join(
            f"- {location}: {', '.join(places)}"
            for location, places in known_contexts
        )
        destination_instruction = f"""
Destination type: state
Build the itinerary across famous real cities inside {destination}. Use these city/place anchors first:
{context_text}
"""
    elif profile["type"] == "country" and known_contexts:
        context_text = "\n".join(
            f"- {location}: {', '.join(places)}"
            for location, places in known_contexts
        )
        destination_instruction = f"""
Destination type: country
Build the itinerary across famous real cities or regions inside {destination}. Use these anchors first:
{context_text}
"""
    else:
        destination_instruction = f"""
Destination type: {profile["type"]}
Keep every place inside {destination}.
"""

    prompt = f"""
Create a professional travel plan.

Client: {client}
Destination: {destination}
Dates: {start_date} to {end_date}
{destination_instruction}

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

Use only famous, real, photo-friendly places that are inside or directly identified with {destination}.
Do not include places from other cities, states, or countries.
Do not invent generic fallback places.
Do not make any itinerary day a departure day; departure/transfer sections are added separately.
Write in a natural premium travel-guide style.
Keep each day practical and realistic.
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
        return enforce_unique_day_places(fallback_plan(destination, start_date, end_date), destination)


def image_fingerprint(image_bytes: bytes) -> str | None:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image = image.convert("L").resize((8, 8))
            pixels = list(image.getdata())
            average = sum(pixels) / len(pixels)
            return "".join("1" if pixel >= average else "0" for pixel in pixels)
    except (UnidentifiedImageError, OSError):
        return None


def image_path_fingerprint(image_path: str | None) -> str | None:
    if not valid_image_path(image_path):
        return None
    try:
        with open(image_path, "rb") as image_file:
            return image_fingerprint(image_file.read())
    except OSError:
        return None


def valid_image_path(image_path: str | None):
    if not image_path or not os.path.exists(image_path):
        return False
    try:
        with Image.open(image_path) as image:
            image.verify()
        with Image.open(image_path) as image:
            return image.width > 0 and image.height > 0
    except Exception as exc:
        log_image_event(f"failed image path skipped: {image_path} ({exc})")
        return False


def download_image(url: str, used_hashes: set[str] | None = None):
    for attempt in range(2):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "AiTravelPlan/1.0"},
                timeout=IMAGE_REQUEST_TIMEOUT,
            )
            if response.status_code == 429 and attempt == 0:
                time.sleep(1.2)
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "image" not in content_type:
                log_image_event(f"failed image URL skipped: {url} ({content_type or 'no content type'})")
                return None

            image_bytes = response.content
            image_hash = image_fingerprint(image_bytes) or hashlib.md5(image_bytes).hexdigest()
            if used_hashes is not None and image_hash in used_hashes:
                log_image_event(f"failed image URL skipped: {url} (duplicate)")
                return None

            if used_hashes is not None:
                used_hashes.add(image_hash)

            with Image.open(BytesIO(image_bytes)) as image:
                image = image.convert("RGB")
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
                    image.save(temp_file.name, format="JPEG", quality=90)
                    if valid_image_path(temp_file.name):
                        log_image_event(f"image URL selected: {url}")
                        return temp_file.name
                    log_image_event(f"failed image URL skipped: {url} (saved file failed validation)")
                    return None
        except (UnidentifiedImageError, OSError):
            log_image_event(f"failed image URL skipped: {url} (image could not be opened)")
            return None
        except Exception as exc:
            if attempt == 0:
                time.sleep(0.4)
                continue
            log_image_event(f"failed image URL skipped: {url} ({exc})")
            return None
    return None


def download_valid_image(urls: list[str], keyword: str, used_hashes: set[str] | None = None):
    log_image_event(f"keyword used: {keyword}")
    for url in urls:
        if should_skip_image_source(url):
            log_image_event(f"failed image URL skipped: {url} (icon/map/logo source)")
            continue
        with USED_IMAGE_URLS_LOCK:
            if url in USED_IMAGE_URLS:
                log_image_event(f"failed image URL skipped: {url} (already used)")
                continue
        image_path = download_image(url, used_hashes)
        if valid_image_path(image_path):
            with USED_IMAGE_URLS_LOCK:
                if url in USED_IMAGE_URLS:
                    log_image_event(f"failed image URL skipped: {url} (already used)")
                    continue
                USED_IMAGE_URLS.add(url)
            log_image_event(f"selected image for keyword '{keyword}': {url}")
            return image_path, url
        log_image_event(f"failed image URL skipped: {url}")
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
    "walt disney world": ["Cinderella Castle April 2013.jpg", "Magic Kingdom castle.jpg"],
    "universal studios florida": ["USF Entrance.jpg", "Universal Studios Florida entrance.jpg", "K022013.jpg"],
    "seaworld orlando": ["SeaWorld Orlando entrance.jpg"],
    "lake eola park": ["Lake Eola Park in Orlando 01.jpg"],
    "kennedy space center visitor complex": [
        "Kennedy Space Center Visitor Complex, Florida.jpg",
        "Entrance Kennedy Space Center Visitor Complex.jpg",
    ],
    "cape canaveral": ["Cape Canaveral Air Force Station.jpg"],
    "cocoa beach": ["Cocoa Beach Pier 2015.jpg"],
    "merritt island national wildlife refuge": ["Merritt Island National Wildlife Refuge.jpg"],
    "south beach miami": ["South Beach, Miami.jpg"],
    "vizcaya museum and gardens": ["Vizcaya Museum and Gardens, Miami.jpg"],
    "wynwood walls": ["Wynwood Walls, Miami.jpg"],
    "everglades national park": [
        "A white ibis flying over the River of Grass, tree islands in background.jpg",
        "Shark Valley.jpg",
    ],
    "shark valley": ["Shark Valley.jpg"],
    "anhinga trail": ["American Purple Gallinule at Anhinga Trail, Royal Palm.jpg"],
    "big cypress national preserve": ["Big Cypress National Preserve.jpg"],
    "duval street key west": ["Duval Street Key West.jpg"],
    "southernmost point buoy": ["Southernmost Point Buoy.jpg"],
    "mallory square": ["Mallory Square Key West.jpg"],
    "ernest hemingway house": ["Ernest Hemingway House Key West.jpg"],
    "coral castle": ["Coral Castle 1.jpg"],
    "florida citrus tower": ["Citrus Tower in June 2024.jpg"],
    "florida museum of natural history": ["Florida Museum of Natural History Gainesville.jpg"],
    "bok tower gardens": ["Bok Tower Gardens tower.jpg"],
    "defence housing authority karachi": ["Sea view karachi.jpg", "DHA Karachi Sunset.jpg"],
    "hill park karachi": ["Hill Park, Karachi.jpg"],
    "holy trinity cathedral karachi": ["Holy Trinity Cathedral Karachi.jpg"],
    "lucky one mall": ["Lucky One Mall Karachi.jpg"],
    "clifton beach karachi": ["Clifton Beach, Karachi.jpg", "Sea view karachi.jpg"],
    "manora fort karachi": ["Manora Fort Karachi.jpg", "Manora Karachi.jpg"],
    "clifton karachi": ["Clifton Beach, Karachi.jpg", "Sea view karachi.jpg"],
    "hindu gymkhana karachi": ["Hindu Gymkhana Karachi.jpg"],
}


TITLE_PLACE_OVERRIDES = {
    "gardens museums bazaars": [
        "Shalimar Gardens Lahore",
        "Lahore Museum",
        "Anarkali Bazaar",
        "MM Alam Road Lahore",
    ],
    "modern lahore shopping shrines": [
        "Data Darbar Lahore",
        "Emporium Mall Lahore",
        "Packages Mall Lahore",
        "MM Alam Road Lahore",
    ],
}


LOCAL_PLACE_FALLBACKS = {
    "badshahi mosque": ["fallbacks/lahore/badshahi-mosque.jpg", "public/fallbacks/lahore/badshahi-mosque.jpg"],
    "lahore fort": ["fallbacks/lahore/lahore-fort.jpg", "public/fallbacks/lahore/lahore-fort.jpg"],
    "minar e pakistan": ["fallbacks/lahore/minar-e-pakistan.jpg", "public/fallbacks/lahore/minar-e-pakistan.jpg"],
    "allama iqbal international airport lahore": [
        "fallbacks/lahore/lahore-airport.jpg",
        "public/fallbacks/lahore/lahore-airport.jpg",
    ],
}


def canonical_place_key(place: str):
    key = slugify(place).replace("_", " ")
    key = re.sub(r"\s+", " ", key)
    aliases = {
        "badshahi mosque lahore": "badshahi mosque",
        "badshahi mosque pakistan": "badshahi mosque",
        "minar e pakistan lahore": "minar e pakistan",
        "data darbar lahore": "data darbar",
        "anarkali bazaar lahore": "anarkali bazaar",
        "allama iqbal international airport": "allama iqbal international airport lahore",
        "lahore airport terminal": "allama iqbal international airport lahore",
        "lahore airport exterior": "allama iqbal international airport lahore",
        "river walk": "river walk san antonio",
        "san antonio river walk": "river walk san antonio",
        "dallas arboretum and botanical garden": "dallas arboretum",
        "walt disney world resort": "walt disney world",
        "disney world": "walt disney world",
        "universal studios orlando": "universal studios florida",
        "kennedy space center": "kennedy space center visitor complex",
        "south beach": "south beach miami",
        "vizcaya": "vizcaya museum and gardens",
        "southernmost point": "southernmost point buoy",
        "hemingway house": "ernest hemingway house",
        "florida natural history museum": "florida museum of natural history",
        "defence housing authority karachi": "defence housing authority karachi",
        "dha karachi": "defence housing authority karachi",
        "hill park karachi": "hill park karachi",
        "holy trinity cathedral karachi": "holy trinity cathedral karachi",
        "lucky one mall karachi": "lucky one mall",
        "clifton beach karachi": "clifton beach karachi",
        "manora fort karachi": "manora fort karachi",
        "clifton karachi": "clifton karachi",
        "hindu gymkhana karachi": "hindu gymkhana karachi",
    }
    return aliases.get(key.strip(), key.strip())


def wikimedia_file_url(file_name: str):
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(file_name)}"


def curated_place_urls(place: str):
    files = CURATED_PLACE_IMAGE_FILES.get(canonical_place_key(place), [])
    if not files:
        return []
    direct_urls = [wikimedia_file_url(file_name) for file_name in files]
    try:
        response = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "titles": "|".join(f"File:{file_name}" for file_name in files),
                "prop": "imageinfo",
                "iiprop": "url|mime",
                "iiurlwidth": 900,
                "format": "json",
            },
            headers={"User-Agent": "AiTravelPlan/1.0"},
            timeout=IMAGE_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        urls = []
        for page in response.json().get("query", {}).get("pages", {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            url = info.get("thumburl") or info.get("url")
            mime = info.get("mime", "")
            if url and mime.startswith("image/"):
                urls.append(url)
        return direct_urls + urls
    except Exception as exc:
        LOGGER.warning("Curated Commons image lookup failed for '%s': %s", place, exc)
        return direct_urls


def local_place_fallback_paths(place: str):
    paths = []
    for relative_path in LOCAL_PLACE_FALLBACKS.get(canonical_place_key(place), []):
        absolute_path = os.path.join(os.getcwd(), relative_path)
        if valid_image_path(absolute_path):
            paths.append(absolute_path)
        else:
            log_image_event(f"failed local fallback skipped: {relative_path}")
    return paths


WIKIPEDIA_PAGE_TITLES = {
    "times square": ["Times Square"],
    "statue of liberty": ["Statue of Liberty"],
    "central park": ["Central Park"],
    "empire state building": ["Empire State Building"],
    "metropolitan museum of art": ["Metropolitan Museum of Art"],
    "brooklyn bridge": ["Brooklyn Bridge"],
    "grand central terminal": ["Grand Central Terminal"],
    "rockefeller center": ["Rockefeller Center"],
    "one world trade center": ["One World Trade Center"],
    "high line": ["High Line"],
    "museum of modern art": ["Museum of Modern Art"],
    "fifth avenue": ["Fifth Avenue"],
    "texas state capitol": ["Texas State Capitol"],
    "zilker park": ["Zilker Park"],
    "bullock texas state history museum": ["Bullock Texas State History Museum"],
    "south congress avenue": ["South Congress"],
    "the alamo": ["Alamo Mission"],
    "river walk san antonio": ["San Antonio River Walk"],
    "san antonio missions national historical park": ["San Antonio Missions National Historical Park"],
    "historic market square": ["Market Square, San Antonio"],
    "space center houston": ["Space Center Houston"],
    "houston museum of natural science": ["Houston Museum of Natural Science"],
    "museum of fine arts houston": ["Museum of Fine Arts, Houston"],
    "houston theater district": ["Houston Theater District"],
    "dallas arboretum": ["Dallas Arboretum and Botanical Garden"],
    "reunion tower": ["Reunion Tower"],
    "the sixth floor museum at dealey plaza": ["The Sixth Floor Museum at Dealey Plaza"],
    "dallas arts district": ["Dallas Arts District"],
    "fort worth stockyards": ["Fort Worth Stockyards"],
    "kimbell art museum": ["Kimbell Art Museum"],
    "sundance square": ["Sundance Square"],
    "fort worth water gardens": ["Fort Worth Water Gardens"],
    "walt disney world": ["Walt Disney World"],
    "universal studios florida": ["Universal Studios Florida"],
    "seaworld orlando": ["SeaWorld Orlando"],
    "lake eola park": ["Lake Eola Park"],
    "kennedy space center visitor complex": ["Kennedy Space Center Visitor Complex"],
    "cape canaveral": ["Cape Canaveral"],
    "cocoa beach": ["Cocoa Beach, Florida"],
    "merritt island national wildlife refuge": ["Merritt Island National Wildlife Refuge"],
    "south beach miami": ["South Beach, Miami"],
    "vizcaya museum and gardens": ["Vizcaya Museum and Gardens"],
    "wynwood walls": ["Wynwood Walls"],
    "everglades national park": ["Everglades National Park"],
    "shark valley": ["Shark Valley"],
    "anhinga trail": ["Anhinga Trail"],
    "big cypress national preserve": ["Big Cypress National Preserve"],
    "duval street key west": ["Duval Street"],
    "southernmost point buoy": ["Southernmost Point Buoy"],
    "mallory square": ["Mallory Square"],
    "ernest hemingway house": ["Ernest Hemingway House"],
    "coral castle": ["Coral Castle"],
    "florida citrus tower": ["Florida Citrus Tower"],
    "florida museum of natural history": ["Florida Museum of Natural History"],
    "bok tower gardens": ["Bok Tower Gardens"],
    "badshahi mosque": ["Badshahi Mosque"],
    "lahore fort": ["Lahore Fort"],
    "minar e pakistan": ["Minar-e-Pakistan"],
    "food street lahore": ["Food Street, Lahore"],
    "shalimar gardens lahore": ["Shalimar Gardens, Lahore"],
    "lahore museum": ["Lahore Museum"],
    "anarkali bazaar": ["Anarkali Bazaar"],
    "data darbar": ["Data Darbar"],
    "emporium mall lahore": ["Emporium Mall"],
    "packages mall lahore": ["Packages Mall"],
    "mm alam road lahore": ["M. M. Alam Road"],
    "allama iqbal international airport lahore": ["Allama Iqbal International Airport"],
    "defence housing authority karachi": ["Defence Housing Authority, Karachi", "Clifton, Karachi"],
    "hill park karachi": ["Hill Park, Karachi"],
    "holy trinity cathedral karachi": ["Holy Trinity Cathedral, Karachi"],
    "lucky one mall": ["Lucky One Mall"],
    "clifton beach karachi": ["Clifton Beach, Karachi"],
    "manora fort karachi": ["Manora, Karachi", "Manora Fort"],
    "clifton karachi": ["Clifton, Karachi"],
    "hindu gymkhana karachi": ["Hindu Gymkhana, Karachi"],
}


IMAGE_KEYWORD_STOP_WORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "the", "to",
    "pakistan", "lahore", "belgium", "brussels", "photo", "image", "view",
}


def image_keywords(value: str):
    words = [word for word in slugify(value).split("_") if word]
    keywords = [word for word in words if word not in IMAGE_KEYWORD_STOP_WORDS and len(word) >= 2]
    return keywords or [word for word in words if word not in {"a", "an", "and", "of", "the"}]


def clean_image_subject(value: str):
    cleaned = re.sub(r"\s+", " ", safe_text(value).replace("&", " ")).strip(" ,.-")
    return cleaned


def place_image_keyword(place: str, destination: str, location: str = ""):
    subject = clean_image_subject(place.replace(",", " "))
    destination_clean = clean_image_subject(destination.replace(",", " "))
    location_city = clean_image_subject(safe_text(location).split(",", 1)[0]) if location else ""
    known_context = PLACE_IMAGE_CONTEXTS.get(canonical_place_key(subject))
    context = known_context or location_city or destination_clean
    if not subject:
        return ""
    if context and context.lower() not in subject.lower():
        return f"{subject} {context}"
    return subject


def image_subjects(destination: str, title: str, location: str, places: list[str]):
    subjects = []
    for place in normalize_places(places):
        subjects.append(clean_image_subject(place.replace(",", " ")))

    for title_part in re.split(r"&|/|\+|\band\b", safe_text(title), flags=re.IGNORECASE):
        subjects.append(clean_image_subject(title_part))

    subjects.extend(
        [
            clean_image_subject(location.replace(",", " ")),
            clean_image_subject(destination),
        ]
    )

    deduped = []
    seen = set()
    for subject in subjects:
        key = slugify(subject)
        if not subject or key in seen:
            continue
        deduped.append(subject)
        seen.add(key)
    return deduped


def city_subjects(destination: str, location: str, title: str):
    raw = []
    for value in [location, destination, title]:
        for part in re.split(r",|&|/|\+|\band\b", safe_text(value), flags=re.IGNORECASE):
            subject = clean_image_subject(part)
            if subject:
                raw.append(subject)
    deduped = []
    seen = set()
    for subject in raw:
        key = slugify(subject)
        if key not in seen:
            deduped.append(subject)
            seen.add(key)
    return deduped


def is_generic_image_subject(subject: str):
    subject_key = slugify(subject).replace("_", " ")
    generic_terms = (
        "free time", "arrival", "leisure", "orientation", "overview",
        "destination", "city", "travel",
    )
    return not subject_key or any(term in subject_key for term in generic_terms)


def is_generic_day_title(title: str):
    title_key = slugify(title).replace("_", " ")
    return any(term in title_key for term in ("free time", "leisure", "arrival", "orientation"))


def is_departure_day(item: dict | None = None, title: str = "", day_index: int | None = None, total_days: int | None = None):
    day_title = safe_text((item or {}).get("title", title)).lower()
    return "departure" in day_title or (day_index is not None and total_days is not None and day_index == total_days)


def departure_image_subjects(destination: str, location: str):
    if "lahore" in f"{destination} {location}".lower():
        return [
            "Allama Iqbal International Airport Lahore",
            "Lahore airport terminal",
            "Lahore airport exterior",
        ]
    city = clean_image_subject(location or destination).split(",", 1)[0].strip() or clean_image_subject(destination)
    return [
        f"{city} airport terminal",
        f"{city} airport exterior",
        f"{city} departure airport",
    ]


def title_place_override(title: str):
    title_key = slugify(title).replace("_", " ")
    for key, places in TITLE_PLACE_OVERRIDES.items():
        if key in title_key:
            return places
    return []


def exact_place_subjects(title: str, places: list[str]):
    override_places = title_place_override(title)
    raw_places = override_places or normalize_places(places)
    subjects = []
    seen = set()
    for place in raw_places:
        subject = clean_image_subject(place.replace(",", " "))
        key = slugify(subject)
        if subject and key not in seen:
            subjects.append(subject)
            seen.add(key)
    return subjects


def ordered_day_image_subjects(destination: str, title: str, location: str, places: list[str]):
    if is_departure_day(title=title):
        return departure_image_subjects(destination, location)

    place_subjects = exact_place_subjects(title, places)
    if is_generic_day_title(title) and not place_subjects:
        return city_subjects(destination, location, title)
    if "lahore" in f"{destination} {location} {' '.join(place_subjects)}".lower():
        lahore_priority = ["Badshahi Mosque Lahore", "Lahore Fort", "Minar-e-Pakistan", "Food Street Lahore"]
        prioritized = []
        place_keys = {canonical_place_key(place): place for place in place_subjects}
        for subject in lahore_priority:
            key = canonical_place_key(subject)
            if key in place_keys:
                prioritized.append(subject)
        for place in place_subjects:
            if canonical_place_key(place) not in {canonical_place_key(item) for item in prioritized}:
                prioritized.append(place)
        return prioritized
    return place_subjects or image_subjects(destination, title, location, places)


def title_matches_place(title: str, place: str):
    title_words = image_word_set(title)
    keywords = list(image_word_set(" ".join(image_keywords(place))))
    if not keywords:
        return True
    matches = sum(1 for keyword in keywords if keyword in title_words)
    required = len(keywords) if len(keywords) <= 2 else 2
    return matches >= required


def image_word_set(value: str):
    aliases = {
        "bazar": "bazaar",
        "bazaar": "bazaar",
        "durbar": "darbar",
        "darbar": "darbar",
        "defence": "dha",
        "defense": "dha",
        "housing": "dha",
        "authority": "dha",
        "gymkhana": "gymkhana",
        "gym": "gymkhana",
        "manora": "manora",
        "manoro": "manora",
        "m": "mm",
    }
    return {aliases.get(word, word) for word in slugify(unquote(value)).split("_") if word}


BAD_IMAGE_SOURCE_TERMS = {
    "badge", "coat", "coat_of_arms", "crest", "diagram", "emblem", "flag",
    "icon", "locator", "logo", "map", "seal", "sign", "symbol",
}


def should_skip_image_source(source: str):
    words = image_word_set(source)
    return any(term in words or term.replace("_", " ") in safe_text(source).lower() for term in BAD_IMAGE_SOURCE_TERMS)


def source_matches_place(source: str, place: str, destination: str):
    if should_skip_image_source(source):
        return False
    source_words = image_word_set(source)
    place_words = image_word_set(" ".join(image_keywords(place)))
    city_words = image_word_set(" ".join(image_keywords(destination)))
    if not place_words:
        return False
    if canonical_place_key(place) == "manora fort karachi":
        return "manora" in source_words and "karachi" in source_words

    place_matches = sum(1 for word in place_words if word in source_words)
    city_matches = sum(1 for word in city_words if word in source_words)
    if len(place_words) <= 2:
        return place_matches == len(place_words) or (place_matches >= 1 and city_matches >= 1)
    return place_matches >= 2 or (place_matches >= 1 and city_matches >= 1)


def wikipedia_page_image_urls(place: str, destination: str):
    urls = []
    page_titles = WIKIPEDIA_PAGE_TITLES.get(canonical_place_key(place), [])
    page_titles.append(clean_image_subject(place))
    for result in wikipedia_search_results(f"{clean_image_subject(place)} {destination}", limit=4):
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        if title_matches_place(title, place) and mentions_destination(title, snippet, destination):
            page_titles.append(title)

    seen_titles = set()
    for page_title in page_titles:
        title_key = slugify(page_title)
        if not page_title or title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        try:
            response = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "titles": page_title,
                    "prop": "pageimages",
                    "pithumbsize": 900,
                    "format": "json",
                },
                headers={"User-Agent": "AiTravelPlan/1.0"},
                timeout=IMAGE_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            pages = response.json().get("query", {}).get("pages", {}).values()
            for page in pages:
                source = (page.get("thumbnail") or {}).get("source")
                title = page.get("title", page_title)
                if source and should_skip_image_source(f"{title} {source}"):
                    log_image_event(f"failed image URL skipped: {source} (icon/map/logo source)")
                elif source and source_matches_place(f"{title} {source}", place, destination):
                    urls.append(source)
                elif source:
                    log_image_event(f"failed image URL skipped: {source} (page title/source does not match {place})")
        except Exception as exc:
            LOGGER.warning("Wikipedia page image lookup failed for '%s': %s", page_title, exc)
    return urls


def get_wikimedia_place_image_urls(place: str, destination: str):
    clean_place = clean_image_subject(place)
    search_terms = [
        clean_place,
        f"{clean_place} {destination}",
    ]
    urls = []
    seen_urls = set()

    for url in wikipedia_page_image_urls(place, destination):
        urls.append(url)
        seen_urls.add(url)

    for url in curated_place_urls(place):
        if should_skip_image_source(url):
            log_image_event(f"failed image URL skipped: {url} (icon/map/logo source)")
        elif source_matches_place(url, place, destination):
            urls.append(url)
            seen_urls.add(url)
        else:
            log_image_event(f"failed image URL skipped: {url} (source does not match {place})")

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
                timeout=IMAGE_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            pages = list(response.json().get("query", {}).get("pages", {}).values())
            for page in pages:
                title = page.get("title", "")
                info = (page.get("imageinfo") or [{}])[0]
                mime = info.get("mime", "")
                url = info.get("thumburl") or info.get("url")
                if not mime.startswith("image/") or not url or url in seen_urls:
                    if not url:
                        LOGGER.warning("Missing image URL for Wikimedia result '%s'", title)
                    continue
                if should_skip_image_source(f"{title} {url}"):
                    log_image_event(f"failed image URL skipped: {url} (icon/map/logo source)")
                elif title_matches_place(title, place) and source_matches_place(f"{title} {url}", place, destination):
                    urls.append(url)
                    seen_urls.add(url)
                else:
                    log_image_event(f"failed image URL skipped: {url} (title/source does not match {place})")
        except Exception as exc:
            LOGGER.warning("Wikimedia image search failed for '%s': %s", term, exc)
            continue

    return urls


def exact_remote_fallback_urls(subject: str, destination: str, location: str):
    query = clean_image_subject(subject)
    city = clean_image_subject((location or destination).replace(",", " "))
    combined = clean_image_subject(f"{query} {city}") if city and city.lower() not in query.lower() else query
    candidates = [combined, query]
    urls = []
    seen = set()
    for idx, candidate in enumerate(candidates):
        if not candidate:
            continue
        keyword = re.sub(r"[^a-zA-Z0-9]+", ",", candidate).strip(",")
        encoded = quote(keyword, safe=",")
        for suffix in ["", ",landmark", ",travel"]:
            url = f"https://loremflickr.com/900/600/{encoded}{suffix}?lock={image_lock(candidate + suffix + str(idx))}"
            if url not in seen:
                urls.append(url)
                seen.add(url)
    return urls


def fit_image_cover(image_path: str, width: int, height: int):
    try:
        with Image.open(image_path) as image:
            image = image.convert("RGB")
            source_ratio = image.width / image.height
            target_ratio = width / height

            if source_ratio > target_ratio:
                new_width = int(image.height * target_ratio)
                left = max((image.width - new_width) // 2, 0)
                image = image.crop((left, 0, left + new_width, image.height))
            elif source_ratio < target_ratio:
                new_height = int(image.width / target_ratio)
                top = max((image.height - new_height) // 2, 0)
                image = image.crop((0, top, image.width, top + new_height))

            image = image.resize((width, height), Image.Resampling.LANCZOS)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
                image.save(temp_file.name, format="JPEG", quality=90)
                return temp_file.name
    except Exception as exc:
        LOGGER.warning("Failed to fit image for PDF: %s (%s)", image_path, exc)
        return None


def wrapped_lines(text: str, max_chars: int):
    words = safe_text(text).split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def create_place_visual_fallback(subject: str, destination: str, location: str = ""):
    title = clean_image_subject(subject) or clean_image_subject(destination) or "Travel Highlight"
    subtitle = clean_image_subject(location or destination)
    subject_key = slugify(f"{title} {subtitle}")
    palette_options = [
        ((13, 71, 91), (244, 162, 97), (255, 255, 255)),
        ((32, 84, 147), (94, 180, 155), (255, 255, 255)),
        ((89, 52, 110), (239, 202, 97), (255, 255, 255)),
        ((28, 88, 66), (233, 196, 106), (255, 255, 255)),
        ((119, 47, 26), (42, 157, 143), (255, 255, 255)),
    ]
    palette = palette_options[image_lock(subject_key) % len(palette_options)]
    background, accent, text_color = palette
    width, height = 1200, 700

    try:
        image = Image.new("RGB", (width, height), background)
        draw = Image.Draw.Draw(image)
        for idx in range(0, width, 24):
            shade = tuple(min(255, channel + (idx // 24) % 36) for channel in background)
            draw.line((idx, 0, max(0, idx - 300), height), fill=shade, width=9)

        draw.rectangle((0, 0, width, height), outline=accent, width=18)
        draw.rectangle((70, 70, width - 70, height - 70), outline=(255, 255, 255), width=3)

        icon_y = 130
        lowered = title.lower()
        if any(term in lowered for term in ("beach", "bay", "lake", "river", "island")):
            draw.arc((125, icon_y, 315, icon_y + 150), 185, 355, fill=accent, width=12)
            draw.line((115, icon_y + 140, 330, icon_y + 140), fill=accent, width=10)
        elif any(term in lowered for term in ("tower", "center", "building", "castle", "fort")):
            draw.rectangle((150, icon_y + 30, 290, icon_y + 190), outline=accent, width=12)
            draw.polygon([(135, icon_y + 30), (220, icon_y - 25), (305, icon_y + 30)], outline=accent)
        elif any(term in lowered for term in ("park", "garden", "everglades", "wildlife", "trail")):
            draw.ellipse((135, icon_y + 45, 235, icon_y + 145), outline=accent, width=12)
            draw.line((185, icon_y + 145, 185, icon_y + 220), fill=accent, width=12)
            draw.arc((185, icon_y + 130, 310, icon_y + 230), 200, 330, fill=accent, width=10)
        else:
            draw.polygon([(125, icon_y + 180), (225, icon_y + 30), (325, icon_y + 180)], outline=accent)
            draw.line((165, icon_y + 180, 285, icon_y + 180), fill=accent, width=12)

        try:
            title_font = ImageFont.truetype("arial.ttf", 68)
            subtitle_font = ImageFont.truetype("arial.ttf", 34)
            label_font = ImageFont.truetype("arial.ttf", 28)
        except Exception:
            title_font = ImageFont.load_default()
            subtitle_font = ImageFont.load_default()
            label_font = ImageFont.load_default()

        y = 325
        for line in wrapped_lines(title, 22)[:3]:
            draw.text((115, y), line, fill=text_color, font=title_font)
            y += 78
        if subtitle:
            draw.text((118, y + 10), subtitle, fill=accent, font=subtitle_font)
        draw.text((118, height - 105), "Relevant travel image fallback", fill=(230, 235, 240), font=label_font)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
            image.save(temp_file.name, format="JPEG", quality=92)
            if valid_image_path(temp_file.name):
                log_image_event(f"generated visual fallback selected for '{title}'")
                return temp_file.name
    except Exception as exc:
        LOGGER.warning("Failed to generate visual fallback for '%s': %s", title, exc)
    return None


def get_cached_subject_image(
    subject: str,
    destination: str,
    location: str = "",
    used_hashes: set[str] | None = None,
):
    keyword = place_image_keyword(subject, destination, location)
    cache_key = slugify(keyword)
    with IMAGE_CACHE_LOCK:
        cached_entry = IMAGE_CACHE.get(cache_key)
    cached_path = cached_entry[0] if cached_entry else None
    cached_url = cached_entry[1] if cached_entry else ""
    if valid_image_path(cached_path):
        cached_hash = image_path_fingerprint(cached_path)
        if used_hashes is not None and cached_hash in used_hashes:
            log_image_event(f"image cache skipped because already used: {keyword} -> {cached_path}")
            return None
        if used_hashes is not None and cached_hash:
            used_hashes.add(cached_hash)
        log_image_event(f"image cache hit: {keyword} -> {cached_path}")
        log_image_event(f"final selected image URL for '{keyword}': {cached_url}")
        return cached_path

    urls = get_wikimedia_place_image_urls(subject, location or destination)
    image_result = download_valid_image(urls, keyword, used_hashes)
    image_path = image_result[0] if image_result else None
    selected_url = image_result[1] if image_result else ""
    if not valid_image_path(image_path):
        for fallback_path in local_place_fallback_paths(subject):
            if valid_image_path(fallback_path):
                log_image_event(f"image URL selected: local place fallback {fallback_path}")
                image_path = fallback_path
                selected_url = fallback_path
                break
    if not valid_image_path(image_path) and not is_generic_image_subject(subject):
        log_image_event(f"remote random fallback disabled for '{keyword}' to avoid irrelevant images")
    if not valid_image_path(image_path):
        image_path = create_place_visual_fallback(subject, destination, location)
        selected_url = image_path or ""

    with IMAGE_CACHE_LOCK:
        IMAGE_CACHE[cache_key] = (image_path, selected_url) if valid_image_path(image_path) else None
    if valid_image_path(image_path):
        selected_hash = image_path_fingerprint(image_path)
        if selected_url == image_path and used_hashes is not None and selected_hash:
            if selected_hash in used_hashes:
                log_image_event(f"selected local image skipped because already used: {keyword} -> {image_path}")
                return None
            used_hashes.add(selected_hash)
        log_image_event(f"final selected image URL for '{keyword}': {selected_url}")
    else:
        log_image_event(f"no verified image selected for keyword '{keyword}'")
    return image_path


def get_day_images(
    destination: str,
    title: str,
    location: str,
    places: list[str],
    day_index: int,
    used_hashes: set[str],
    total_days: int | None = None,
):
    log_image_event(f"day title: {title}")
    minimum_places = 2 if is_generic_day_title(title) else 1
    places = validated_destination_places(normalize_places(places), destination, minimum=minimum_places)
    log_image_event(f"extracted places: {places}")

    if is_departure_day(title=title, day_index=day_index, total_days=total_days):
        subjects = departure_image_subjects(destination, location)
        log_image_event(f"image subjects for day {day_index}: {subjects}")
        paths = []
        for subject in subjects:
            image_path = get_cached_subject_image(subject, destination, location, used_hashes)
            if valid_image_path(image_path):
                paths = [image_path]
                break
        if not paths:
            for fallback_path in local_place_fallback_paths("Allama Iqbal International Airport Lahore"):
                if valid_image_path(fallback_path):
                    paths = [fallback_path]
                    break
        log_image_event(f"final departure images for day {day_index}: {paths}")
        return paths

    subjects = ordered_day_image_subjects(destination, title, location, places)
    log_image_event(f"image subjects for day {day_index}: {[place_image_keyword(subject, destination, location) for subject in subjects]}")

    paths = []
    seen_paths = set()
    for subject in subjects:
        image_path = get_cached_subject_image(subject, destination, location, used_hashes)
        if valid_image_path(image_path) and image_path not in seen_paths:
            paths.append(image_path)
            seen_paths.add(image_path)
        if len(paths) >= 2:
            break

    if len(paths) < 2:
        subject_keys = {canonical_place_key(subject) for subject in subjects}
        backup_subjects = [
            place
            for place in destination_place_pool(destination)
            if canonical_place_key(place) not in subject_keys
        ]
        log_image_event(f"backup image subjects for day {day_index}: {backup_subjects[:8]}")
        for subject in backup_subjects:
            image_path = get_cached_subject_image(subject, destination, location, used_hashes)
            if valid_image_path(image_path) and image_path not in seen_paths:
                paths.append(image_path)
                seen_paths.add(image_path)
            if len(paths) >= 2:
                break

    paths = [path for path in paths if valid_image_path(path)][:2]

    if len(paths) < 2:
        LOGGER.error("Only %s exact place-matched images found for day %s using subjects %s", len(paths), day_index, subjects)

    log_image_event(f"final 2 images for day {day_index}: {paths[:2]}")
    return paths[:2]


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
        places = normalize_places(item.get("places", []))[:3]
        detail_parts = []
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
    valid_paths = [image_path for image_path in image_paths if valid_image_path(image_path)][:2]
    if not valid_paths:
        log_image_event(f"PDF image grid skipped because only {len(valid_paths)} valid images were available")
        return y

    if len(valid_paths) == 1:
        positions = [(x, y, 119, 52)]
    else:
        positions = [
            (x, y, 58.5, 42),
            (x + 60.5, y, 58.5, 42),
        ]

    for idx, image_path in enumerate(valid_paths):
        px, py, pw, ph = positions[idx]
        try:
            fitted_path = fit_image_cover(image_path, int(pw * 20), int(ph * 20)) or image_path
            if valid_image_path(fitted_path):
                pdf.image(fitted_path, x=px, y=py, w=pw, h=ph)
            else:
                log_image_event(f"failed image path skipped: {fitted_path}")
        except Exception as exc:
            log_image_event(f"failed image path skipped: {image_path} ({exc})")

    return y + (46 if len(valid_paths) == 2 else 56)


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


def prepare_pdf_days(days: list[dict]):
    prepared = []
    destination_day_counts: dict[str, int] = {}
    for idx, item in enumerate(days):
        day = dict(item)
        destination_name = safe_text(day.get("destination", day.get("location", "")))
        destination_key = safe_text(destination_name).lower()
        if idx == 0 or day.get("is_destination_start"):
            day["arrival_note"] = "Arrive, check in, and rest before continuing with the planned places."
        if day.get("section_type") == "departure_transfer":
            day["title"] = day.get("title") or f"Departure from {destination_name}"
            day["places"] = normalize_places(day.get("places", []))[:1]
            day["morning"] = "Pack bags and check out from the hotel."
            day["afternoon"] = day.get("afternoon") or f"Transfer from {destination_name} with enough buffer time."
            day["evening"] = day.get("evening") or f"Depart from {destination_name}."
        elif is_departure_day(day, day_index=idx + 1, total_days=len(days)):
            day["title"] = day.get("title", "Departure")
        else:
            known_contexts = known_destination_contexts(destination_name)
            if known_contexts:
                context_index = destination_day_counts.get(destination_key, 0) % len(known_contexts)
                context_location, context_places = known_contexts[context_index]
                day["location"] = context_location
                day["places"] = context_places
                day["title"] = fallback_day_title(
                    destination_name,
                    context_location,
                    context_places,
                    context_index,
                    len(known_contexts),
                )
                day["morning"], day["afternoon"], day["evening"] = premium_day_copy(
                    context_location,
                    context_places,
                )
                destination_day_counts[destination_key] = destination_day_counts.get(destination_key, 0) + 1
                prepared.append(day)
                continue

            existing_places = normalize_places(day.get("places", []))
            minimum_places = 2 if is_generic_day_title(day.get("title", "")) or len(existing_places) < 2 else len(existing_places)
            famous_places = validated_destination_places(existing_places, destination_name or day.get("location", ""), minimum=minimum_places)
            if famous_places:
                day["places"] = famous_places
            if is_generic_day_title(day.get("title", "")) and len(famous_places) >= 2:
                day["title"] = fallback_day_title(
                    destination_name or day.get("location", ""),
                    day.get("location", destination_name),
                    famous_places,
                    idx,
                    len(days),
                )
                day["morning"], day["afternoon"], day["evening"] = premium_day_copy(
                    day.get("location", destination_name),
                    famous_places,
                )
        prepared.append(day)
    return prepared


def draw_destination_heading(pdf: TravelPlanPDF, destination: str):
    if pdf.get_y() > 244:
        pdf.add_page()
        pdf.set_y(18)
    pdf.set_x(18)
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(14, 44, 85)
    pdf.cell(0, 8, safe_text(f"{destination} Itinerary"))
    pdf.ln(8)


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
    draw_bullet_line(pdf, "Arrival & Rest", item.get("arrival_note", ""), content_x + 1)
    draw_bullet_line(pdf, "Places", places_text, content_x + 1)

    for label, content in [
        ("Morning", item["morning"]),
        ("Afternoon", item["afternoon"]),
        ("Evening", item["evening"]),
        ("Departure", item.get("departure_note", "")),
    ]:
        draw_bullet_line(pdf, label, content, content_x + 1)

    if [image_path for image_path in image_paths if valid_image_path(image_path)]:
        if pdf.get_y() > 218:
            pdf.add_page()
            pdf.set_y(18)
        image_y = pdf.get_y() + 3
        last_y = draw_image_grid(pdf, image_paths, content_x, image_y)
        pdf.set_y(last_y + 8)
    else:
        pdf.ln(8)

    block_end_y = pdf.get_y()
    pdf.set_draw_color(210, 220, 230)
    pdf.line(marker_x, block_start_y + 8, marker_x, block_end_y - 2)


def departure_transfer_day(destination: str, transfer_date: date, next_destination: str | None = None):
    title = f"Departure / Transfer from {destination}" if next_destination else f"Departure from {destination}"
    afternoon = (
        f"Travel from {destination} to {next_destination}. Keep luggage, timing, and station or airport buffers separate from the next itinerary."
        if next_destination
        else f"Travel toward the airport or station in {destination} with enough buffer time."
    )
    evening = f"Arrive in {next_destination} and rest before starting that itinerary." if next_destination else f"Depart from {destination}."
    return {
        "date": transfer_date,
        "title": title,
        "location": destination,
        "destination": destination,
        "places": [],
        "morning": "Pack bags, check out, and confirm transport details.",
        "afternoon": afternoon,
        "evening": evening,
        "section_type": "departure_transfer",
    }


def build_multi_destination_days(client: str, destinations: list[str], start_date: date, end_date: date):
    all_days = []
    ranges = allocate_destination_dates(destinations, start_date, end_date)
    for idx, (destination_name, destination_start, destination_end) in enumerate(ranges):
        log_image_event(f"current destination being processed: {destination_name}")
        destination_profile(destination_name)
        destination_days = get_ai_plan(client, destination_name, destination_start, destination_end)
        destination_days = enforce_unique_day_places(destination_days, destination_name)
        for day_idx, day in enumerate(destination_days):
            day["destination"] = destination_name
            day["is_destination_start"] = day_idx == 0
            day["places"] = validated_destination_places(day.get("places", []), destination_name, minimum=0)
            if is_departure_day(day) and day["places"]:
                day["title"] = fallback_day_title(
                    destination_name,
                    day.get("location", destination_name),
                    day["places"],
                    day_idx,
                    len(destination_days),
                )
            all_days.append(day)
        next_destination = ranges[idx + 1][0] if idx + 1 < len(ranges) else None
        all_days.append(departure_transfer_day(destination_name, destination_end, next_destination))
    return all_days


def build_pdf(client: str, title: str, destination: str, start_date: date, end_date: date, days: list[dict]) -> bytes:
    days = prepare_pdf_days(days)
    with USED_IMAGE_URLS_LOCK:
        USED_IMAGE_URLS.clear()
    used_image_hashes: set[str] = set()
    cover_path = get_cover_image(destination, used_image_hashes)
    day_images = [
        get_day_images(
            item.get("destination", destination),
            item["title"],
            item.get("location", item.get("destination", destination)),
            item.get("places", []),
            idx + 1,
            used_image_hashes,
            len(days),
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
    current_destination = None
    for idx, item in enumerate(days):
        item_destination = item.get("destination", destination)
        if item_destination != current_destination:
            draw_destination_heading(pdf, item_destination)
            current_destination = item_destination
        draw_day_block(pdf, item, day_images[idx])

    output = pdf.output(dest="S")
    if isinstance(output, str):
        return output.encode("latin-1", errors="ignore")
    if isinstance(output, bytearray):
        return bytes(output)
    return output


st.markdown("<div class='main-header'>Ai <span>Travel</span> Plan</div>", unsafe_allow_html=True)

st.markdown("### Trip Details")
left_col, right_col = st.columns(2)

with left_col:
    client_name = st.text_input("Client Name", "")
    destination = st.text_input("Destination", "")
    plan_title = st.text_input("Plan Title", "Travel Plan")

with right_col:
    start_date = st.date_input("Departure", datetime.now().date())
    end_date = st.date_input("Return", datetime.now().date() + timedelta(days=5))
    generate = st.button("Create Travel Plan")

if end_date < start_date:
    st.error("Return date cannot be before departure date.")
    st.stop()

if generate:
    destinations = parse_destinations(destination)
    if not destinations:
        st.error("Please enter a destination before creating the travel plan.")
        st.stop()

    progress = st.progress(0)
    status = st.empty()

    status.write("Preparing travel plan...")
    progress.progress(15)
    itinerary_days = build_multi_destination_days(client_name, destinations, start_date, end_date)

    status.write("Creating PDF and collecting photos...")
    progress.progress(55)
    destination_title = " to ".join(destinations)
    pdf_bytes = build_pdf(
        client=client_name,
        title=plan_title,
        destination=destination_title,
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
        file_name=f"{slugify(destination_title)}_travel_plan.pdf",
        mime="application/pdf",
    )
