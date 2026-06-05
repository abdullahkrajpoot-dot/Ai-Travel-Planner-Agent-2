import json
import os
import re
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
import time

import requests
import streamlit as st
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError


st.set_page_config(page_title="Ai Travel Planner", layout="wide")

st.markdown(
    """
    <style>
    /* Force dark color scheme and hide Streamlit theme menu to prevent light mode */
    :root, html, body { color-scheme: dark !important; background-color: #07111f !important; }
    .stApp { color-scheme: dark !important; }
    #MainMenu { display: none !important; }
    footer { display: none !important; }
    header { visibility: hidden !important; height: 0 !important; }
    [data-testid="stHeader"] { display: none !important; }
    [data-testid="stToolbar"] { display: none !important; }
    [data-testid="stDecoration"] { display: none !important; }
    [data-testid="stStatusWidget"] { display: none !important; }
    [data-testid="collapsedControl"] { display: none !important; }
    [data-testid="stSidebarCollapseButton"] { display: none !important; }
    button[kind="header"] { display: none !important; }

    .stApp {
        background: linear-gradient(180deg, #07111f 0%, #0f172a 100%);
        color: #e2e8f0;
    }
    [data-testid="stAppViewContainer"] {
        background: linear-gradient(180deg, #07111f 0%, #0f172a 100%);
    }
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0b1325 0%, #111827 100%);
        border-right: 1px solid rgba(148, 163, 184, 0.2);
    }
    [data-testid="stSidebar"] * {
        color: #dbeafe;
    }
    .stTextInput>div>div>input, .stDateInput input {
        background: rgba(15, 23, 42, 0.92) !important;
        color: #f8fafc !important;
        border: 1px solid rgba(148, 163, 184, 0.35) !important;
        border-radius: 10px !important;
    }
    .stTextInput>label, .stDateInput>label {
        color: #bfdbfe !important;
        font-weight: 600 !important;
    }
    .stAlert {
        background: rgba(30, 41, 59, 0.75) !important;
        border: 1px solid rgba(148, 163, 184, 0.25) !important;
        color: #f8fafc !important;
    }
    .main-header { color: #f8fafc; font-weight: 800; font-size: 2.5rem; margin-bottom: 0.15rem; }
    .sub-header { color: #cbd5e1; margin-bottom: 1rem; }
    .hero-card {
        background: linear-gradient(135deg, rgba(37,99,235,0.26), rgba(14,165,233,0.12));
        border: 1px solid rgba(148, 163, 184, 0.25);
        border-radius: 18px;
        padding: 1.2rem 1.3rem;
        margin-bottom: 1rem;
    }
    .summary-pill {
        display: inline-block;
        margin: 0.2rem 0.35rem 0 0;
        padding: 0.28rem 0.7rem;
        border-radius: 999px;
        background: rgba(30, 41, 59, 0.88);
        color: #e2e8f0;
        border: 1px solid rgba(148, 163, 184, 0.22);
        font-size: 0.88rem;
    }
    .day-card {
        background: rgba(15, 23, 42, 0.78);
        border: 1px solid rgba(148, 163, 184, 0.18);
        border-radius: 18px;
        padding: 1rem 1.1rem;
        margin-bottom: 1rem;
        color: #e5e7eb;
        box-shadow: 0 18px 40px rgba(0, 0, 0, 0.18);
    }
    .day-title { color: #f8fafc; font-weight: 700; font-size: 1.1rem; margin-bottom: 0.2rem; }
    .day-date { color: #93c5fd; font-weight: 700; margin-bottom: 0.7rem; }
    .section-title { color: #38bdf8; font-weight: 700; margin-top: 0.45rem; }
    .place-chip {
        display: inline-block;
        margin: 0.2rem 0.35rem 0 0;
        padding: 0.22rem 0.62rem;
        border-radius: 999px;
        background: rgba(37, 99, 235, 0.17);
        color: #dbeafe;
        border: 1px solid rgba(96, 165, 250, 0.25);
        font-size: 0.82rem;
    }
    .stButton>button, .stDownloadButton>button {
        background: #2563eb;
        color: white;
        border-radius: 10px;
        border: none;
        font-weight: 700;
        width: 100%;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def safe_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return normalized.encode("latin-1", "ignore").decode("latin-1").strip()


def html_text(value: str) -> str:
    return safe_text(value).replace("\n", "<br>")


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value or "")
    return cleaned.strip("_").lower() or "travel_plan"


def format_day_label(day_date: date) -> str:
    return day_date.strftime("%B %d - %A")


def compact_date(day_date: date) -> str:
    return day_date.strftime("%b %d, %Y")


def extract_json_block(raw_text: str) -> str:
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("JSON block not found")
    return raw_text[start : end + 1]


def normalize_place_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    places = []
    for item in value:
        text = safe_text(str(item))
        if text and text not in places:
            places.append(text)
    return places[:4]


WIKI_HEADERS = {
    "User-Agent": "AI-Travel-Planner/1.0 (+https://github.com/abdullahkrajpoot-dot/Ai-Travel-Planner-Agent)",
    "Accept": "application/json",
}


BAD_PLACE_WORDS = {
    "seizure", "attack", "battle", "war", "incident", "massacre", "bombing",
    "riot", "siege", "uprising", "crash", "fire", "disaster", "politics",
    "election", "team", "club", "season",
}


def is_valid_place_name(place_name: str) -> bool:
    tokens = set(slugify(place_name).split("_"))
    return bool(place_name) and not BAD_PLACE_WORDS.intersection(tokens)


DESTINATION_LANDMARKS = {
    "makkah": [
        ["Masjid al-Haram, Makkah", "Kaaba, Makkah", "Maqam Ibrahim, Makkah", "Safa and Marwa, Makkah"],
        ["Jabal al-Nour, Makkah", "Hira Cave, Makkah", "Jannat al-Mu'alla, Makkah", "Masjid al-Jinn, Makkah"],
        ["Mount Arafat, Makkah", "Jabal al-Rahmah, Makkah", "Mina, Makkah", "Muzdalifah, Makkah"],
        ["Masjid Aisha, Makkah", "Abraj Al Bait, Makkah", "Clock Tower Museum, Makkah", "Makkah Museum"],
        ["Thawr Cave, Makkah", "Jabal Thawr, Makkah", "Al Kiswa Factory, Makkah", "Hudaibiyah, Makkah"],
        ["Masjid al-Khayf, Mina", "Jamaraat Bridge, Mina", "Bay'ah Mosque, Makkah", "Al Diyafa Mall, Makkah"],
    ],
    "mecca": [
        ["Masjid al-Haram, Makkah", "Kaaba, Makkah", "Maqam Ibrahim, Makkah", "Safa and Marwa, Makkah"],
        ["Jabal al-Nour, Makkah", "Hira Cave, Makkah", "Jannat al-Mu'alla, Makkah", "Masjid al-Jinn, Makkah"],
        ["Mount Arafat, Makkah", "Jabal al-Rahmah, Makkah", "Mina, Makkah", "Muzdalifah, Makkah"],
        ["Masjid Aisha, Makkah", "Abraj Al Bait, Makkah", "Clock Tower Museum, Makkah", "Makkah Museum"],
        ["Thawr Cave, Makkah", "Jabal Thawr, Makkah", "Al Kiswa Factory, Makkah", "Hudaibiyah, Makkah"],
        ["Masjid al-Khayf, Mina", "Jamaraat Bridge, Mina", "Bay'ah Mosque, Makkah", "Al Diyafa Mall, Makkah"],
    ],
    "madinah": [
        ["Al-Masjid an-Nabawi, Madinah", "Green Dome, Madinah", "Jannat al-Baqi, Madinah", "Quba Mosque, Madinah"],
        ["Mount Uhud, Madinah", "Uhud Martyrs Cemetery, Madinah", "The Seven Mosques, Madinah", "Masjid al-Qiblatayn, Madinah"],
        ["Dar Al Madinah Museum", "Hejaz Railway Museum, Madinah", "Quran Exhibition, Madinah", "Al Noor Mall, Madinah"],
    ],
    "medina": [
        ["Al-Masjid an-Nabawi, Madinah", "Green Dome, Madinah", "Jannat al-Baqi, Madinah", "Quba Mosque, Madinah"],
        ["Mount Uhud, Madinah", "Uhud Martyrs Cemetery, Madinah", "The Seven Mosques, Madinah", "Masjid al-Qiblatayn, Madinah"],
        ["Dar Al Madinah Museum", "Hejaz Railway Museum, Madinah", "Quran Exhibition, Madinah", "Al Noor Mall, Madinah"],
    ],
    "paris": [
        ["Eiffel Tower, Paris", "Louvre Museum, Paris", "Tuileries Garden, Paris", "Place de la Concorde, Paris"],
        ["Notre-Dame de Paris", "Sainte-Chapelle, Paris", "Latin Quarter, Paris", "Pantheon, Paris"],
        ["Arc de Triomphe, Paris", "Champs-Elysees, Paris", "Grand Palais, Paris", "Seine River, Paris"],
        ["Sacred Heart Basilica of Montmartre, Paris", "Montmartre, Paris", "Moulin Rouge, Paris", "Galeries Lafayette, Paris"],
    ],
    "london": [
        ["Tower Bridge, London", "Tower of London", "St Paul's Cathedral, London", "Borough Market, London"],
        ["Buckingham Palace, London", "Westminster Abbey, London", "Big Ben, London", "London Eye"],
        ["British Museum, London", "Covent Garden, London", "Trafalgar Square, London", "National Gallery, London"],
    ],
    "dubai": [
        ["Burj Khalifa, Dubai", "Dubai Mall", "Dubai Fountain", "Dubai Opera"],
        ["Museum of the Future, Dubai", "Dubai Frame", "Zabeel Park, Dubai", "Al Seef, Dubai"],
        ["Palm Jumeirah, Dubai", "Atlantis The Palm, Dubai", "Dubai Marina", "Jumeirah Beach Residence, Dubai"],
    ],
    "istanbul": [
        ["Hagia Sophia, Istanbul", "Blue Mosque, Istanbul", "Topkapi Palace, Istanbul", "Basilica Cistern, Istanbul"],
        ["Grand Bazaar, Istanbul", "Suleymaniye Mosque, Istanbul", "Galata Tower, Istanbul", "Karakoy, Istanbul"],
        ["Dolmabahce Palace, Istanbul", "Ortakoy Mosque, Istanbul", "Bosphorus Bridge, Istanbul", "Taksim Square, Istanbul"],
    ],
    "new york": [
        ["Statue of Liberty, New York", "Ellis Island, New York", "Battery Park, New York", "One World Trade Center, New York"],
        ["Times Square, New York", "Bryant Park, New York", "New York Public Library", "Grand Central Terminal, New York"],
        ["Central Park, New York", "Metropolitan Museum of Art", "Fifth Avenue, New York", "Rockefeller Center, New York"],
    ],
    "rome": [
        ["Colosseum, Rome", "Roman Forum, Rome", "Palatine Hill, Rome", "Capitoline Hill, Rome"],
        ["Trevi Fountain, Rome", "Pantheon, Rome", "Piazza Navona, Rome", "Spanish Steps, Rome"],
        ["St. Peter's Basilica, Vatican City", "Vatican Museums", "Castel Sant'Angelo, Rome", "Trastevere, Rome"],
    ],
    "tokyo": [
        ["Senso-ji, Tokyo", "Tokyo Skytree", "Ueno Park, Tokyo", "Ameya-Yokocho, Tokyo"],
        ["Meiji Shrine, Tokyo", "Shibuya Crossing, Tokyo", "Harajuku, Tokyo", "Omotesando, Tokyo"],
        ["Tokyo Tower", "Imperial Palace, Tokyo", "Ginza, Tokyo", "Hamarikyu Gardens, Tokyo"],
    ],
    "bangkok": [
        ["Grand Palace, Bangkok", "Wat Phra Kaew, Bangkok", "Wat Pho, Bangkok", "Wat Arun, Bangkok"],
        ["Jim Thompson House, Bangkok", "Bangkok Art and Culture Centre", "Siam Paragon, Bangkok", "Lumphini Park, Bangkok"],
        ["Chatuchak Weekend Market, Bangkok", "Asiatique The Riverfront, Bangkok", "Yaowarat Road, Bangkok", "Chao Phraya River, Bangkok"],
    ],
    "singapore": [
        ["Marina Bay Sands, Singapore", "Gardens by the Bay, Singapore", "Merlion Park, Singapore", "ArtScience Museum, Singapore"],
        ["Sentosa Island, Singapore", "Universal Studios Singapore", "S.E.A. Aquarium, Singapore", "Siloso Beach, Singapore"],
        ["Singapore Botanic Gardens", "Orchard Road, Singapore", "Chinatown, Singapore", "Clarke Quay, Singapore"],
    ],
    "barcelona": [
        ["Sagrada Familia, Barcelona", "Park Guell, Barcelona", "Casa Batllo, Barcelona", "Casa Mila, Barcelona"],
        ["Gothic Quarter, Barcelona", "Barcelona Cathedral", "La Rambla, Barcelona", "Boqueria Market, Barcelona"],
        ["Montjuic, Barcelona", "Magic Fountain of Montjuic", "Poble Espanyol, Barcelona", "Barceloneta Beach, Barcelona"],
    ],
    "kuala lumpur": [
        ["Petronas Twin Towers, Kuala Lumpur", "KLCC Park, Kuala Lumpur", "Aquaria KLCC, Kuala Lumpur", "Menara Kuala Lumpur"],
        ["Batu Caves, Selangor", "Merdeka Square, Kuala Lumpur", "Sultan Abdul Samad Building", "Central Market Kuala Lumpur"],
        ["Thean Hou Temple, Kuala Lumpur", "Perdana Botanical Gardens", "National Mosque of Malaysia", "Bukit Bintang, Kuala Lumpur"],
    ],
    "lahore": [
        ["Badshahi Mosque, Lahore", "Lahore Fort, Lahore", "Minar-e-Pakistan, Lahore", "Hazuri Bagh, Lahore"],
        ["Shalimar Gardens, Lahore", "Wazir Khan Mosque, Lahore", "Delhi Gate, Lahore", "Lahore Museum"],
        ["Tomb of Jahangir, Lahore", "Tomb of Nur Jahan, Lahore", "Akbari Sarai, Lahore", "Kamran's Baradari, Lahore"],
        ["Lahore Museum", "Anarkali Bazaar, Lahore", "Tollinton Market, Lahore", "Mall Road, Lahore"],
        ["Data Darbar, Lahore", "Masjid Wazir Khan, Lahore", "Shahi Hammam, Lahore", "Sunehri Mosque, Lahore"],
        ["Greater Iqbal Park, Lahore", "Lahore Zoo", "Bagh-e-Jinnah, Lahore", "Jilani Park, Lahore"],
        ["Emporium Mall, Lahore", "Packages Mall, Lahore", "Liberty Market, Lahore", "Fortress Stadium, Lahore"],
    ],
    "islamabad": [
        ["Faisal Mosque, Islamabad", "Pakistan Monument, Islamabad", "Lok Virsa Museum, Islamabad", "Daman-e-Koh, Islamabad"],
        ["Saidpur Village, Islamabad", "Margalla Hills National Park", "Rawal Lake, Islamabad", "Shakarparian, Islamabad"],
        ["Trail 5, Islamabad", "Pir Sohawa, Islamabad", "The Monal, Islamabad", "Japanese Park, Islamabad"],
        ["Centaurus Mall, Islamabad", "F-9 Park, Islamabad", "Pakistan National Council of Arts", "Rose and Jasmine Garden, Islamabad"],
    ],
    "karachi": [
        ["Mazar-e-Quaid, Karachi", "Mohatta Palace, Karachi", "Frere Hall, Karachi", "Clifton Beach, Karachi"],
        ["Empress Market, Karachi", "Port Grand, Karachi", "National Museum of Pakistan", "Dolmen Mall Clifton, Karachi"],
        ["Pakistan Maritime Museum, Karachi", "PAF Museum, Karachi", "Charna Island, Karachi", "Turtle Beach, Karachi"],
        ["Chaukhandi Tombs, Karachi", "Quaid-e-Azam House Museum", "Tooba Mosque, Karachi", "Burns Road, Karachi"],
    ],
    "pakistan": [
        ["Badshahi Mosque, Lahore", "Lahore Fort, Lahore", "Minar-e-Pakistan, Lahore", "Shalimar Gardens, Lahore"],
        ["Faisal Mosque, Islamabad", "Pakistan Monument, Islamabad", "Lok Virsa Museum, Islamabad", "Daman-e-Koh, Islamabad"],
        ["Mazar-e-Quaid, Karachi", "Mohatta Palace, Karachi", "Frere Hall, Karachi", "Clifton Beach, Karachi"],
        ["Altit Fort, Hunza", "Baltit Fort, Hunza", "Attabad Lake, Hunza", "Passu Cones, Hunza"],
        ["Derawar Fort, Bahawalpur", "Noor Mahal, Bahawalpur", "Lal Suhanra National Park, Bahawalpur", "Uch Sharif, Bahawalpur"],
    ],
    "belgium": [
        ["Grand Place, Brussels", "Galeries Royales Saint-Hubert, Brussels", "Mont des Arts, Brussels", "Atomium, Brussels"],
        ["Belfry of Bruges, Bruges", "Market Square, Bruges", "Canals of Bruges, Bruges", "Basilica of the Holy Blood, Bruges"],
        ["Gravensteen, Ghent", "Saint Bavo's Cathedral, Ghent", "Graslei, Ghent", "Korenlei, Ghent"],
        ["Antwerp Central Station, Antwerp", "Cathedral of Our Lady, Antwerp", "Grote Markt, Antwerp", "Museum aan de Stroom, Antwerp"],
    ],
}


def split_destination_names(destination: str) -> list[str]:
    cleaned = safe_text(destination)
    if not cleaned:
        return []
    parts = re.split(r"\s*(?:,|;|/|\+|&|\band\b|\bthen\b)\s*", cleaned, flags=re.IGNORECASE)
    unique = []
    for part in parts:
        item = part.strip()
        if item and item.lower() not in {value.lower() for value in unique}:
            unique.append(item)
    return unique or [cleaned]


def _landmarks_for_one_destination(destination: str) -> list[list[str]]:
    key = slugify(destination).replace("_", " ")
    for name, landmarks in DESTINATION_LANDMARKS.items():
        if name in key:
            return landmarks

    discovered = discover_destination_landmarks(destination)
    if discovered:
        return chunk_places(discovered)

    normalized_destination = safe_text(destination) or "the destination"
    return [[
        f"{normalized_destination} historic center",
        f"{normalized_destination} main square",
        f"{normalized_destination} national museum",
        f"{normalized_destination} scenic viewpoint",
    ]]


def get_destination_landmarks(destination: str) -> list[list[str]]:
    destinations = split_destination_names(destination)
    if len(destinations) <= 1:
        return _landmarks_for_one_destination(destination)

    combined_places = []
    for item in destinations[:4]:
        for group in _landmarks_for_one_destination(item):
            for place in group:
                if place not in combined_places:
                    combined_places.append(place)
    return chunk_places(combined_places)


def chunk_places(places: list[str], size: int = 4) -> list[list[str]]:
    chunks = [places[idx : idx + size] for idx in range(0, len(places), size)]
    if not chunks:
        return []
    while len(chunks[-1]) < size:
        chunks[-1].append(chunks[-1][len(chunks[-1]) % len(chunks[-1])])
    return chunks


def flat_unique_places(groups: list[list[str]]) -> list[str]:
    places = []
    for group in groups:
        for place in group:
            clean_place = safe_text(place)
            if clean_place and clean_place not in places and is_valid_place_name(clean_place):
                places.append(clean_place)
    return places


def get_landmark_groups_for_days(destination: str, total_days: int) -> list[list[str]]:
    groups = get_destination_landmarks(destination)
    places = flat_unique_places(groups)
    target_places = max(total_days * 4, 4)

    if len(places) < target_places:
        for item in split_destination_names(destination):
            for place in discover_destination_landmarks(item):
                if len(places) >= target_places:
                    break
                if place not in places and is_valid_place_name(place):
                    places.append(place)

    if len(places) < target_places:
        base = safe_text(destination) or "Destination"
        generic_types = [
            "Old Town",
            "Central Market",
            "Heritage Museum",
            "Main Mosque",
            "City Park",
            "Waterfront",
            "Scenic Viewpoint",
            "Cultural Center",
            "Historic Quarter",
            "National Museum",
            "Main Square",
            "Botanical Garden",
            "Old Bazaar",
            "City Museum",
            "Grand Mosque",
            "Riverside Promenade",
            "Art Gallery",
            "Fortress",
            "Clock Tower",
            "Public Garden",
            "Heritage Street",
            "Traditional Market",
            "Archaeological Site",
            "Panoramic Lookout",
        ]
        for place_type in generic_types:
            if len(places) >= target_places:
                break
            place = f"{base} {place_type}"
            if place not in places:
                places.append(place)

    return chunk_places(places[:target_places])


def _wiki_api(params: dict, timeout: int = 20) -> dict:
    response = requests.get(
        "https://en.wikipedia.org/w/api.php",
        params={"format": "json", "utf8": 1, **params},
        timeout=timeout,
        headers=WIKI_HEADERS,
    )
    response.raise_for_status()
    return response.json()


def _wiki_search_titles(query: str, limit: int = 8) -> list[str]:
    try:
        payload = _wiki_api({
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
        })
        titles = []
        for row in payload.get("query", {}).get("search", []):
            title = safe_text(row.get("title", ""))
            lowered = title.lower()
            if title and "disambiguation" not in lowered and not lowered.startswith("list of"):
                titles.append(title)
        return titles
    except Exception:
        return []


def _wiki_page_summary(title: str) -> dict | None:
    try:
        response = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(title.replace(' ', '_'))}",
            timeout=15,
            headers=WIKI_HEADERS,
        )
        if response.status_code != 200:
            return None
        return response.json()
    except Exception:
        return None


def _destination_coordinates(destination: str) -> tuple[float, float] | None:
    titles = [destination] + _wiki_search_titles(destination, limit=3)
    for title in titles:
        summary = _wiki_page_summary(title)
        coords = (summary or {}).get("coordinates") or {}
        lat = coords.get("lat")
        lon = coords.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return float(lat), float(lon)
    return None


def _nominatim_place(destination: str) -> dict | None:
    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": destination,
                "format": "jsonv2",
                "limit": 1,
                "addressdetails": 1,
            },
            timeout=20,
            headers={"User-Agent": WIKI_HEADERS["User-Agent"]},
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            return None
        return results[0]
    except Exception:
        return None


def _destination_coordinates_any(destination: str) -> tuple[float, float] | None:
    coords = _destination_coordinates(destination)
    if coords:
        return coords
    place = _nominatim_place(destination)
    if not place:
        return None
    try:
        return float(place["lat"]), float(place["lon"])
    except Exception:
        return None


def _osm_landmarks(destination: str, target_count: int = 60) -> list[str]:
    coords = _destination_coordinates_any(destination)
    if not coords:
        return []

    radius = 18000
    lat, lon = coords
    query = f"""
    [out:json][timeout:35];
    (
      node(around:{radius},{lat},{lon})["name"]["tourism"];
      way(around:{radius},{lat},{lon})["name"]["tourism"];
      relation(around:{radius},{lat},{lon})["name"]["tourism"];
      node(around:{radius},{lat},{lon})["name"]["historic"];
      way(around:{radius},{lat},{lon})["name"]["historic"];
      relation(around:{radius},{lat},{lon})["name"]["historic"];
      node(around:{radius},{lat},{lon})["name"]["amenity"~"place_of_worship|theatre|arts_centre|marketplace|library"];
      way(around:{radius},{lat},{lon})["name"]["amenity"~"place_of_worship|theatre|arts_centre|marketplace|library"];
      relation(around:{radius},{lat},{lon})["name"]["amenity"~"place_of_worship|theatre|arts_centre|marketplace|library"];
      node(around:{radius},{lat},{lon})["name"]["leisure"~"park|garden|nature_reserve"];
      way(around:{radius},{lat},{lon})["name"]["leisure"~"park|garden|nature_reserve"];
      relation(around:{radius},{lat},{lon})["name"]["leisure"~"park|garden|nature_reserve"];
      node(around:{radius},{lat},{lon})["name"]["building"~"mosque|church|cathedral|temple|synagogue|museum"];
      way(around:{radius},{lat},{lon})["name"]["building"~"mosque|church|cathedral|temple|synagogue|museum"];
      relation(around:{radius},{lat},{lon})["name"]["building"~"mosque|church|cathedral|temple|synagogue|museum"];
    );
    out tags center {target_count};
    """
    try:
        response = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=45,
            headers={"User-Agent": WIKI_HEADERS["User-Agent"]},
        )
        response.raise_for_status()
        elements = response.json().get("elements", [])
    except Exception:
        return []

    city_label = safe_text(destination)
    scored: list[tuple[int, str]] = []
    priority_tags = {
        "museum": 80,
        "attraction": 75,
        "viewpoint": 70,
        "artwork": 65,
        "gallery": 65,
        "castle": 80,
        "monument": 75,
        "memorial": 60,
        "archaeological_site": 70,
        "place_of_worship": 72,
        "park": 55,
        "garden": 55,
        "marketplace": 50,
        "library": 45,
    }
    banned_tokens = {*BAD_PLACE_WORDS, "hotel", "station", "airport", "school", "hospital", "clinic"}

    for element in elements:
        tags = element.get("tags", {}) or {}
        name = safe_text(tags.get("name", ""))
        if not name or not is_valid_place_name(name):
            continue
        tokens = set(slugify(name).split("_"))
        if banned_tokens.intersection(tokens):
            continue
        score = 10
        for tag_key in ("tourism", "historic", "amenity", "leisure", "building"):
            tag_value = safe_text(tags.get(tag_key, "")).lower()
            score += priority_tags.get(tag_value, 0)
        if city_label and city_label.lower() not in name.lower():
            name = f"{name}, {city_label}"
        scored.append((score, name))

    scored.sort(reverse=True, key=lambda item: item[0])
    unique = []
    seen = set()
    for _, name in scored:
        key = slugify(name)
        if key not in seen:
            unique.append(name)
            seen.add(key)
        if len(unique) >= target_count:
            break
    return unique


def _nearby_wiki_titles(destination: str) -> list[str]:
    coords = _destination_coordinates(destination)
    if not coords:
        return []
    try:
        payload = _wiki_api({
            "action": "query",
            "list": "geosearch",
            "gscoord": f"{coords[0]}|{coords[1]}",
            "gsradius": 10000,
            "gslimit": 35,
        })
        banned = {
            "station", "metro", "subway", "railway", "airport", "hotel", "school",
            *BAD_PLACE_WORDS,
        }
        titles = []
        for row in payload.get("query", {}).get("geosearch", []):
            title = safe_text(row.get("title", ""))
            lowered = title.lower()
            if title and not any(word in lowered for word in banned):
                titles.append(title)
        return titles
    except Exception:
        return []


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def discover_destination_landmarks(destination: str) -> list[str]:
    normalized_destination = safe_text(destination)
    if not normalized_destination:
        return []

    candidates: list[str] = []
    candidates.extend(_osm_landmarks(normalized_destination, target_count=80))
    search_queries = [
        f"{normalized_destination} landmarks tourist attractions",
        f"{normalized_destination} famous places",
        f"{normalized_destination} museums historic sites",
    ]
    for query in search_queries:
        candidates.extend(_wiki_search_titles(query, limit=15))
    candidates.extend(_nearby_wiki_titles(normalized_destination))

    unique = []
    seen = set()
    destination_tokens = {token for token in slugify(normalized_destination).split("_") if len(token) > 2}
    attraction_words = {
        "museum", "palace", "castle", "cathedral", "mosque", "church", "temple", "fort",
        "tower", "square", "park", "garden", "monument", "bridge", "gallery", "market",
        "old", "historic", "gate", "wall", "harbour", "waterfront", "viewpoint",
        "shrine", "basilica", "beach", "lake", "cave", "mount", "mountain", "mall",
        "zoo", "aquarium", "library", "theatre", "opera", "citadel", "fortress",
    }
    banned_words = {*BAD_PLACE_WORDS, "people", "list"}

    for title in candidates:
        clean_title = safe_text(title)
        key = slugify(clean_title)
        if not clean_title or key in seen:
            continue
        title_tokens = set(key.split("_"))
        if banned_words.intersection(title_tokens) or not is_valid_place_name(clean_title):
            continue
        has_context = bool(destination_tokens.intersection(title_tokens))
        has_attraction_word = bool(attraction_words.intersection(title_tokens))
        if not has_context and not has_attraction_word:
            continue
        seen.add(key)
        if normalized_destination.lower() not in clean_title.lower():
            unique.append(f"{clean_title}, {normalized_destination}")
        else:
            unique.append(clean_title)
        if len(unique) >= 80:
            break

    return unique


def fallback_plan(destination: str, start_date: date, end_date: date):
    total_days = max((end_date - start_date).days + 1, 1)
    templates = get_landmark_groups_for_days(destination, total_days)
    days = []
    for idx in range(total_days):
        current_date = start_date + timedelta(days=idx)
        places = templates[idx % len(templates)]
        if idx == 0:
            title = "Arrival & First Impressions"
        elif idx == total_days - 1:
            title = "Departure Day"
        else:
            title = f"Day {idx + 1} Signature Highlights"
        days.append(
            {
                "date": current_date,
                "title": title,
                "morning": f"Begin with {places[0]} and {places[1]}, two well-known highlights connected to {destination}.",
                "afternoon": f"Continue toward {places[2]} for local culture, photos, lunch, and neighborhood exploration.",
                "evening": f"End the day around {places[3]} with sunset views, dinner, and a comfortable transfer back to the hotel.",
                "places": places,
            }
        )
    return days


def parse_ai_plan(raw_text: str, destination: str, start_date: date, end_date: date):
    total_days = max((end_date - start_date).days + 1, 1)
    expected_dates = [start_date + timedelta(days=i) for i in range(total_days)]
    landmark_templates = get_landmark_groups_for_days(destination, total_days)
    payload = json.loads(extract_json_block(raw_text))
    raw_days = payload.get("days", [])
    if not isinstance(raw_days, list) or not raw_days:
        return fallback_plan(destination, start_date, end_date)

    normalized = []
    for idx in range(total_days):
        item = raw_days[idx] if idx < len(raw_days) and isinstance(raw_days[idx], dict) else {}
        normalized.append(
            {
                "date": expected_dates[idx],
                "title": safe_text(item.get("title") or f"Day {idx + 1} Highlights"),
                "morning": safe_text(item.get("morning", "")),
                "afternoon": safe_text(item.get("afternoon", "")),
                "evening": safe_text(item.get("evening", "")),
                "places": normalize_place_list(item.get("places", [])),
            }
        )

    used_plan_places: set[str] = set()
    for idx, day in enumerate(normalized):
        fresh_places = []
        for place in day["places"]:
            if is_valid_place_name(place) and place not in used_plan_places:
                fresh_places.append(place)
                used_plan_places.add(place)
        day["places"] = fresh_places
        fallback_places = landmark_templates[idx % len(landmark_templates)] if landmark_templates else []
        for place in fallback_places:
            if len(day["places"]) >= 4:
                break
            if place not in used_plan_places and is_valid_place_name(place):
                day["places"].append(place)
                used_plan_places.add(place)

    return normalized


def get_ai_plan(client: str, destination: str, start_date: date, end_date: date):
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return fallback_plan(destination, start_date, end_date)

    total_days = max((end_date - start_date).days + 1, 1)
    prompt = f"""
Create a professional travel itinerary in strict JSON.

Client: {client}
Destination: {destination}
Trip length: {total_days} days
Dates: {start_date} to {end_date}

Rules:
- Return JSON only.
- Root object must be {{ "days": [ ... ] }}.
- Include exactly {total_days} day objects.
- Each day object must contain:
  - "title"
  - "morning"
  - "afternoon"
  - "evening"
  - "places"
- "places" must be an array of 3 or 4 real, specific place names strongly related to that day's activities.
- Place names should be exact enough for image search, like "Badshahi Mosque, Lahore" not generic terms like "city center".
- Do not repeat the same place on another day unless the trip is longer than the destination's available attractions.
- Each day's morning, afternoon, and evening text must describe that same day's places.
- Use visitor attractions only: mosques, museums, mountains, caves, parks, markets, towers, monuments, heritage sites, viewpoints, and malls.
- Do not include events, incidents, attacks, battles, seizures, wars, disasters, hotels, airports, or railway stations as places.
- Keep the itinerary realistic and tourist-friendly.
"""

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "anthropic/claude-3-haiku",
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=90,
        )
        response.raise_for_status()
        data = response.json()
        raw_text = data["choices"][0]["message"]["content"]
        return parse_ai_plan(raw_text, destination, start_date, end_date)
    except Exception:
        return fallback_plan(destination, start_date, end_date)


def save_pdf_ready_image(content: bytes) -> str | None:
    try:
        image = Image.open(BytesIO(content))
        image.load()
        if image.width < 120 or image.height < 120:
            return None
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        elif image.mode == "L":
            image = image.convert("RGB")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
            image.save(temp_file.name, format="JPEG", quality=88, optimize=True)
            return temp_file.name
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def create_text_image(place_name: str, destination: str | None = None) -> str | None:
    try:
        image = Image.new("RGB", (1200, 800), (235, 239, 244))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1200, 800), fill=(235, 239, 244))
        draw.rectangle((0, 0, 1200, 160), fill=(22, 48, 86))
        draw.rectangle((70, 220, 1130, 700), outline=(150, 163, 184), width=4)
    except Exception:
        return None

    try:
        title_font = ImageFont.truetype("arial.ttf", 58)
        body_font = ImageFont.truetype("arial.ttf", 38)
    except Exception:
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()

    try:
        safe_place = safe_text(place_name) or "Travel Highlight"
        safe_destination = safe_text(destination or "")
        draw.text((70, 52), "Travel Highlight", fill=(255, 255, 255), font=title_font)
        wrapped_place = wrap_text(safe_place, 34)
        y = 280
        for line in wrapped_place:
            draw.text((110, y), line, fill=(15, 23, 42), font=body_font)
            y += 54
        if safe_destination:
            draw.text((110, 620), safe_destination, fill=(71, 85, 105), font=body_font)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
            image.save(temp_file.name, format="JPEG", quality=88, optimize=True)
            return temp_file.name
    except Exception:
        return None


def wrap_text(value: str, max_chars: int) -> list[str]:
    words = value.split()
    lines = []
    current = ""
    for word in words:
        next_line = f"{current} {word}".strip()
        if len(next_line) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = next_line
    if current:
        lines.append(current)
    return lines[:5]


def download_image(url: str, suffix: str = ".jpg"):
    # Retry downloads a few times with backoff to reduce intermittent failures.
    headers = {
        "User-Agent": "AI-Travel-Planner/1.0 (+https://github.com/abdullahkrajpoot-dot/Ai-Travel-Planner-Agent)",
        "Accept": "image/jpeg,image/png,image/*;q=0.8,*/*;q=0.5",
    }
    for attempt in range(1, 4):
        try:
            response = requests.get(url, timeout=30, allow_redirects=True, headers=headers)
            response.raise_for_status()
            content_type = (response.headers.get("Content-Type", "") or "").lower()
            if "image/" not in content_type:
                return None

            if len(response.content) < 1024:
                return None

            return save_pdf_ready_image(response.content)
        except Exception:
            if attempt < 3:
                time.sleep(0.8 * attempt)
                continue
            return None


def _candidate_place_queries(place_name: str, destination: str | None) -> list[str]:
    candidates = [safe_text(place_name)]
    if "," in place_name:
        candidates.append(safe_text(place_name.split(",", 1)[0]))
    if destination and destination.lower() not in place_name.lower():
        candidates.append(safe_text(f"{place_name}, {destination}"))
    unique = []
    for item in candidates:
        if item and item not in unique:
            unique.append(item)
    return unique


def _wikipedia_summary_image(query: str) -> str | None:
    title = requests.utils.quote(query.strip().replace(" ", "_"))
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
    try:
        response = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent": "AI-Travel-Planner/1.0",
                "Accept": "application/json",
            },
        )
        if response.status_code != 200:
            return None
        payload = response.json()
        image_obj = payload.get("originalimage") or payload.get("thumbnail") or {}
        image_url = image_obj.get("source")
        if image_url and image_url.startswith("http"):
            return image_url
        return None
    except Exception:
        return None


def _wikipedia_page_image(query: str) -> str | None:
    try:
        payload = _wiki_api({
            "action": "query",
            "prop": "pageimages",
            "piprop": "original|thumbnail",
            "pithumbsize": 1200,
            "redirects": 1,
            "titles": query,
        })
        pages = payload.get("query", {}).get("pages", {})
        for page in pages.values():
            image_obj = page.get("original") or page.get("thumbnail") or {}
            image_url = image_obj.get("source")
            if image_url and image_url.startswith("http"):
                return image_url
        return None
    except Exception:
        return None


def _wikipedia_search_image(query: str) -> str | None:
    search_url = "https://en.wikipedia.org/w/api.php"
    try:
        response = requests.get(
            search_url,
            timeout=25,
            params={
                "action": "query",
                "list": "search",
                "format": "json",
                "utf8": 1,
                "srlimit": 5,
                "srsearch": query,
            },
            headers=WIKI_HEADERS,
        )
        response.raise_for_status()
        results = response.json().get("query", {}).get("search", [])
        if not results:
            return None

        query_tokens = {
            token
            for token in slugify(query).split("_")
            if len(token) > 2 and token not in {"the", "and", "for"}
        }
        ranked_titles = []
        for row in results:
            title = safe_text(row.get("title", ""))
            title_tokens = {token for token in slugify(title).split("_") if len(token) > 2}
            overlap = len(query_tokens.intersection(title_tokens))
            ranked_titles.append((overlap, title))
        ranked_titles.sort(reverse=True, key=lambda item: item[0])

        for overlap, title in ranked_titles:
            if overlap == 0:
                continue
            image_url = _wikipedia_page_image(title)
            if image_url:
                return image_url
            image_url = _wikipedia_summary_image(title)
            if image_url:
                return image_url
        return None
    except Exception:
        return None


def _commons_search_image(query: str) -> str | None:
    """Search Wikimedia Commons for a matching file and return a direct image URL."""
    try:
        search_params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "srnamespace": 6,  # File namespace on Commons
            "srlimit": 4,
        }
        resp = requests.get("https://commons.wikimedia.org/w/api.php", params=search_params, timeout=20, headers=WIKI_HEADERS)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("query", {}).get("search", [])
        if not results:
            return None

        query_tokens = {
            token
            for token in slugify(query).split("_")
            if len(token) > 2 and token not in {"the", "and", "for", "lahore", "makkah", "madinah"}
        }
        ranked_results = []
        for row in results:
            title = safe_text(row.get("title", ""))
            title_tokens = {token for token in slugify(title).split("_") if len(token) > 2}
            overlap = len(query_tokens.intersection(title_tokens))
            ranked_results.append((overlap, row))
        ranked_results.sort(reverse=True, key=lambda item: item[0])

        for overlap, row in ranked_results:
            if overlap == 0 and query_tokens:
                continue
            title = row.get("title")
            if not title:
                continue
            # Get imageinfo for this file title
            info_params = {
                "action": "query",
                "format": "json",
                "prop": "imageinfo",
                "iiprop": "url",
                "iiurlwidth": 1200,
                "titles": title,
            }
            info_resp = requests.get("https://commons.wikimedia.org/w/api.php", params=info_params, timeout=20, headers=WIKI_HEADERS)
            info_resp.raise_for_status()
            pages = info_resp.json().get("query", {}).get("pages", {})
            for page in pages.values():
                imageinfo = page.get("imageinfo") or []
                if imageinfo:
                    url = imageinfo[0].get("thumburl") or imageinfo[0].get("url")
                    if url and url.startswith("http"):
                        return url
        return None
    except Exception:
        return None


def _google_custom_search_images(query: str) -> list[str]:
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    cse_id = os.getenv("GOOGLE_CSE_ID", "").strip()
    if not api_key or not cse_id:
        return []
    try:
        response = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": api_key,
                "cx": cse_id,
                "q": query,
                "searchType": "image",
                "num": 6,
                "safe": "active",
                "imgSize": "large",
                "rights": "cc_publicdomain,cc_attribute,cc_sharealike",
            },
            timeout=20,
            headers={"User-Agent": WIKI_HEADERS["User-Agent"]},
        )
        response.raise_for_status()
        urls = []
        for item in response.json().get("items", []):
            link = item.get("link", "")
            if link.startswith("http"):
                urls.append(link)
        return urls
    except Exception:
        return []


def _duckduckgo_image_search_urls(query: str) -> list[str]:
    try:
        page = requests.get(
            "https://duckduckgo.com/",
            params={"q": query},
            timeout=20,
            headers={"User-Agent": WIKI_HEADERS["User-Agent"]},
        )
        page.raise_for_status()
        token_match = re.search(r"vqd=['\"]([^'\"]+)['\"]", page.text)
        if not token_match:
            return []
        response = requests.get(
            "https://duckduckgo.com/i.js",
            params={
                "l": "us-en",
                "o": "json",
                "q": query,
                "vqd": token_match.group(1),
                "f": ",,,",
                "p": "1",
            },
            timeout=25,
            headers={
                "User-Agent": WIKI_HEADERS["User-Agent"],
                "Referer": "https://duckduckgo.com/",
            },
        )
        response.raise_for_status()
        urls = []
        for item in response.json().get("results", [])[:8]:
            image_url = item.get("image") or item.get("thumbnail")
            if image_url and image_url.startswith("http"):
                urls.append(image_url)
        return urls
    except Exception:
        return []


def _web_image_search_urls(query: str) -> list[str]:
    clean_query = f"{safe_text(query)} landmark tourist place travel photo"
    urls = []
    for provider_urls in (
        _google_custom_search_images(clean_query),
        _duckduckgo_image_search_urls(clean_query),
    ):
        for url in provider_urls:
            if url not in urls:
                urls.append(url)
    return urls


def build_place_image(place_name: str, destination: str | None = None):
    # Strict place matching first to keep image relevance high.
    queries = _candidate_place_queries(place_name, destination)
    best_url = None

    for query in queries:
        page_url = _wikipedia_page_image(query)
        if page_url:
            best_url = best_url or page_url
            path = download_image(page_url, suffix=".jpg")
            if path:
                return {"place": place_name, "url": page_url, "path": path}

        direct_url = _wikipedia_summary_image(query)
        if direct_url:
            best_url = best_url or direct_url
            path = download_image(direct_url, suffix=".jpg")
            if path:
                return {"place": place_name, "url": direct_url, "path": path}

        searched_url = _wikipedia_search_image(query)
        if not searched_url:
            # Try Wikimedia Commons as an additional fallback for image search
            commons_url = _commons_search_image(query)
            if commons_url:
                best_url = best_url or commons_url
                path = download_image(commons_url, suffix=".jpg")
                if path:
                    return {"place": place_name, "url": commons_url, "path": path}
            continue
        best_url = best_url or searched_url
        path = download_image(searched_url, suffix=".jpg")
        if path:
            return {"place": place_name, "url": searched_url, "path": path}

    for query in queries:
        for image_url in _web_image_search_urls(query):
            best_url = best_url or image_url
            path = download_image(image_url, suffix=".jpg")
            if path:
                return {"place": place_name, "url": image_url, "path": path}

    fallback_query = requests.utils.quote(f"{place_name} {destination or ''} landmark travel photo".strip())
    fallback_urls = []
    if best_url:
        fallback_urls.append(best_url)
    fallback_urls.extend([
        f"https://source.unsplash.com/1200x800/?{fallback_query}",
        f"https://loremflickr.com/1200/800/{fallback_query}",
    ])

    for fallback_url in fallback_urls:
        path = download_image(fallback_url, suffix=".jpg")
        if path:
            return {"place": place_name, "url": fallback_url, "path": path}

    placeholder_path = create_text_image(place_name, destination)
    return {"place": place_name, "url": fallback_urls[0] if fallback_urls else None, "path": placeholder_path}


def build_image_bundle(days: list[dict], destination: str):
    cover_candidates = []
    for day in days[:2]:
        cover_candidates.extend(day.get("places", []))
    if not cover_candidates:
        cover_candidates = [destination]

    unique_places = []
    planned_places = [place for day in days for place in day.get("places", [])]
    for place in cover_candidates + planned_places:
        if place not in unique_places:
            unique_places.append(place)

    results = {}
    with ThreadPoolExecutor(max_workers=12) as executor:
        future_map = {executor.submit(build_place_image, place, destination): place for place in unique_places}
        for future in as_completed(future_map):
            place = future_map[future]
            try:
                results[place] = future.result()
            except Exception:
                results[place] = {"place": place, "url": None, "path": None}

    cover_path = None
    used_sources: set[str] = set()
    used_places: set[str] = set()
    for place in cover_candidates:
        entry = results.get(place, {})
        path = entry.get("path")
        if path:
            cover_path = path
            break

    day_images: list[list[dict]] = []
    for day in days:
        entries: list[dict] = []
        for place in day.get("places", []):
            entry = results.get(place, {"place": place, "url": None, "path": None})
            path = entry.get("path")
            source = path or entry.get("url")
            if source and source not in used_sources and place not in used_places:
                entries.append(entry)
                used_sources.add(source)
                used_places.add(place)

        day_images.append(entries[:4])

    return cover_path, day_images


class TravelPlanPDF(FPDF):
    def header(self):
        if self.page_no() >= 3:
            self.set_font("Helvetica", "B", 8)
            self.set_text_color(0, 0, 0)
            self.cell(0, 6, f"Page {self.page_no()} of {{nb}}", align="R")
            self.ln(3)


def draw_cover_page(pdf: TravelPlanPDF, client: str, title: str, destination: str, start_date: date, end_date: date, cover_path: str | None):
    pdf.add_page()
    pdf.set_fill_color(255, 255, 255)
    pdf.rect(0, 0, 210, 297, "F")
    pdf.set_xy(0, 18)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(210, 10, safe_text(client.upper()), align="C")
    if cover_path:
        try:
            pdf.image(cover_path, x=10, y=38, w=190, h=86)
        except Exception:
            pass
    pdf.set_xy(0, 132)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(14, 44, 85)
    pdf.cell(210, 10, safe_text(title), align="C")
    pdf.set_xy(0, 146)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(53, 90, 129)
    pdf.cell(210, 8, safe_text(f"{compact_date(start_date)} - {compact_date(end_date)}"), align="C")
    pdf.set_xy(0, 156)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(70, 85, 110)
    pdf.cell(210, 7, safe_text(destination), align="C")


def draw_summary_page(pdf: TravelPlanPDF, days: list[dict]):
    pdf.add_page()
    pdf.set_xy(14, 18)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(20, 54, 109)
    pdf.cell(0, 10, "Trip Summary")
    pdf.ln(14)
    for item in days:
        pdf.set_draw_color(210, 220, 230)
        pdf.line(14, pdf.get_y() + 2, 196, pdf.get_y() + 2)
        pdf.ln(5)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(35, 68, 115)
        pdf.cell(110, 8, safe_text(item["title"]))
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 8, safe_text(format_day_label(item["date"])), align="R")
        pdf.ln(7)
        pdf.set_x(14)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(90, 100, 120)
        pdf.multi_cell(182, 5, safe_text(", ".join(item.get("places", []))))


def draw_timeline_marker(pdf: TravelPlanPDF, y_top: float):
    x = 20
    pdf.set_draw_color(190, 200, 210)
    pdf.line(x, y_top, x, y_top + 24)
    pdf.ellipse(x - 4, y_top, 8, 8, "D")
    pdf.set_xy(x - 2, y_top + 1.2)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(50, 50, 50)
    pdf.cell(4, 4, "+", align="C")


def draw_image_grid(pdf: TravelPlanPDF, image_entries: list[dict], x: float, y: float):
    if not image_entries:
        return y
    positions = [
        (x, y, 68, 40),
        (x + 70, y, 34, 19),
        (x + 106, y, 34, 19),
        (x + 70, y + 21, 34, 19),
    ]
    for idx, image_entry in enumerate(image_entries[:4]):
        px, py, pw, ph = positions[idx]
        image_path = image_entry.get("path")
        if not image_path:
            continue
        try:
            pdf.image(image_path, x=px, y=py, w=pw, h=ph)
        except Exception:
            continue
    return y + 44


def draw_day_block(pdf: TravelPlanPDF, item: dict, image_entries: list[dict]):
    if pdf.get_y() > 230:
        pdf.add_page()
    start_y = pdf.get_y() + 4
    draw_timeline_marker(pdf, start_y)
    content_x = 32
    pdf.set_xy(content_x, start_y - 1)
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(52, 86, 129)
    pdf.cell(0, 8, safe_text(format_day_label(item["date"])))
    pdf.ln(8)
    pdf.set_x(content_x)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 7, safe_text(item["title"]))
    pdf.ln(7)
    for label, content in [("Morning", item["morning"]), ("Afternoon", item["afternoon"]), ("Evening", item["evening"])]:
        if not safe_text(content):
            continue
        pdf.set_x(content_x)
        pdf.set_font("Helvetica", "B", 10)
        pdf.write(5, f"{label}: ")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(150, 5, safe_text(content))
    if item.get("places"):
        pdf.set_x(content_x)
        pdf.set_font("Helvetica", "B", 10)
        pdf.write(5, "Places: ")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(150, 5, safe_text(", ".join(item["places"])))
    if image_entries:
        image_y = pdf.get_y() + 2
        last_y = draw_image_grid(pdf, image_entries, content_x, image_y)
        pdf.set_y(last_y + 7)
    else:
        pdf.ln(7)
    pdf.set_draw_color(220, 228, 236)
    pdf.line(14, pdf.get_y(), 196, pdf.get_y())
    pdf.ln(5)


def build_pdf(client: str, title: str, destination: str, start_date: date, end_date: date, days: list[dict], cover_path: str | None, day_images: list[list[dict]]) -> bytes:
    pdf = TravelPlanPDF("P", "mm", "A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=16)
    draw_cover_page(pdf, client, title, destination, start_date, end_date, cover_path)
    draw_summary_page(pdf, days)
    pdf.add_page()
    pdf.set_y(12)
    for idx, item in enumerate(days):
        draw_day_block(pdf, item, day_images[idx] if idx < len(day_images) else [])
    output = pdf.output(dest="S")
    if isinstance(output, bytearray):
        return bytes(output)
    if isinstance(output, str):
        return output.encode("latin-1", errors="ignore")
    return output


def cleanup_temp_images(cover_path: str | None, day_images: list[list[dict]]):
    paths = set()
    if cover_path:
        paths.add(cover_path)
    for entries in day_images:
        for entry in entries:
            path = entry.get("path")
            if path:
                paths.add(path)
    for file_path in paths:
        try:
            path_obj = Path(file_path)
            if path_obj.exists():
                path_obj.unlink()
        except Exception:
            continue


st.markdown("<div class='main-header'>AI Travel Planner</div>", unsafe_allow_html=True)
st.markdown(
    "<div class='sub-header'>Generate place-accurate travel plans with sharper images and a cleaner preview.</div>",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### Trip Details")
    client_name = st.text_input("Client Name", "", placeholder="Enter client name")
    destination = st.text_input(
        "Destination",
        "",
        placeholder="Makkah, Madinah",
        help="For two cities, separate them with a comma. Example: Makkah, Madinah",
    )
    plan_title = st.text_input("Plan Title", "Travel Plan")
    start_date = st.date_input("Departure", datetime.now().date())
    end_date = st.date_input("Return", datetime.now().date() + timedelta(days=5))
    generate = st.button("Create Travel Plan")

if end_date < start_date:
    st.error("Return date cannot be before departure date.")
    st.stop()

if "trip_result" not in st.session_state:
    st.session_state["trip_result"] = None

if generate:
    if not client_name.strip() or not destination.strip():
        st.error("Please enter client name and destination.")
        st.stop()
    progress = st.progress(0, text="Generating itinerary...")
    itinerary_days = get_ai_plan(client_name, destination, start_date, end_date)
    progress.progress(35, text="Fetching all destination images together...")
    cover_path, day_images = build_image_bundle(itinerary_days, destination)
    progress.progress(80, text="Building PDF...")
    pdf_bytes = build_pdf(
        client=client_name,
        title=plan_title,
        destination=destination,
        start_date=start_date,
        end_date=end_date,
        days=itinerary_days,
        cover_path=cover_path,
        day_images=day_images,
    )
    progress.progress(100, text="Travel plan ready.")
    st.session_state["trip_result"] = {
        "client_name": client_name,
        "destination": destination,
        "plan_title": plan_title,
        "start_date": start_date,
        "end_date": end_date,
        "days": itinerary_days,
        "cover_path": cover_path,
        "day_images": day_images,
        "pdf_bytes": pdf_bytes,
    }

result = st.session_state.get("trip_result")

if result:
    st.download_button(
        label="Download Professional PDF",
        data=result["pdf_bytes"],
        file_name=f"{slugify(result['destination'])}_travel_plan.pdf",
        mime="application/pdf",
    )
