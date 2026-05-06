"""
AI Travel Planner - Professional PDF Generation
Generates day-by-day travel itineraries with verified images and professional PDF layout.
"""

import json
import os
import re
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
import threading

import requests
import streamlit as st
from fpdf import FPDF
from PIL import Image

# ==================== CONFIGURATION ====================

used_urls_lock = threading.Lock()

st.set_page_config(page_title="AI Travel Planner", layout="wide")

# Custom CSS for dark theme UI
st.markdown("""
<style>
.stApp {
    background: linear-gradient(180deg, #07111f 0%, #0f172a 100%);
    color: #e2e8f0;
}
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0b1325 0%, #111827 100%);
    border-right: 1px solid rgba(148, 163, 184, 0.2);
}
.stButton>button, .stDownloadButton>button {
    background: #2563eb;
    color: white;
    border-radius: 10px;
    border: none;
    font-weight: 700;
    width: 100%;
}
.main-header { color: #f8fafc; font-weight: 800; font-size: 2.5rem; }
.hero-card {
    background: linear-gradient(135deg, rgba(37,99,235,0.26), rgba(14,165,233,0.12));
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 18px;
    padding: 1.2rem;
    margin-bottom: 1rem;
}
.day-card {
    background: rgba(15, 23, 42, 0.78);
    border: 1px solid rgba(148, 163, 184, 0.18);
    border-radius: 18px;
    padding: 1rem;
    margin-bottom: 1rem;
}
</style>
""", unsafe_allow_html=True)


# ==================== UTILITY FUNCTIONS ====================

def safe_text(value: str) -> str:
    """Normalize text for PDF compatibility."""
    normalized = unicodedata.normalize("NFKD", value or "")
    return normalized.encode("latin-1", "ignore").decode("latin-1").strip()


def format_date(day_date: date) -> str:
    """Format date as 'June 13 - Friday'."""
    return day_date.strftime("%B %d - %A")


def compact_date(day_date: date) -> str:
    """Format date as 'Jun 13, 2024'."""
    return day_date.strftime("%b %d, %Y")


def generate_days(start_date: date, end_date: date) -> list[date]:
    """Generate list of dates from start to end."""
    days = []
    current = start_date
    while current <= end_date:
        days.append(current)
        current += timedelta(days=1)
    return days


# ==================== IMAGE FETCHING ====================

def bing_images_scrape(query: str, max_images: int = 5) -> list[str]:
    """Scrape Bing Images directly. More reliable than Google for scraping."""
    try:
        search_url = f"https://www.bing.com/images/search?q={requests.utils.quote(query)}&form=HDRSC2&first=1"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        }
        
        response = requests.get(search_url, headers=headers, timeout=15)
        if response.status_code != 200:
            return []
        
        # Bing stores image data in 'm' attribute of 'iusc' class
        pattern = r'm="({.*?})"'
        matches = re.findall(pattern, response.text)
        
        image_urls = []
        for match in matches:
            try:
                # Unescape HTML entities
                clean_json = match.replace('&quot;', '"').replace('&amp;', '&')
                data = json.loads(clean_json)
                url = data.get("murl")
                if url and url.startswith('http') and any(ext in url.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                    image_urls.append(url)
            except:
                continue
                
        return list(dict.fromkeys(image_urls))[:max_images * 2]
    except Exception:
        return []


def fetch_verified_images(query: str, destination: str, used_urls: set, max_images: int = 4) -> list[str]:
    """
    Fetch verified images by scraping Google Images directly.
    Returns list of local file paths to valid images.
    """
    image_paths = []
    
    # Try multiple query variations for higher accuracy and realism
    queries = [
        f"{query} {destination} landscape photography",
        f"{query} {destination} landmark view",
        f"{query} {destination} tourism 4k",
        f"{query} architectural photography"
    ]
    
    # Negative keywords to avoid drawings, flyers, text-heavy content, and watermarked stock photos
    negative_keywords = [
        "drawing", "vector", "flyer", "poster", "text", "infographic", "advertisement", "diagram",
        "alamy", "stock", "shutterstock", "dreamstime", "123rf", "istockphoto", "watermark", "bus crash"
    ]
    for i in range(len(queries)):
        queries[i] += " " + " ".join([f"-{kw}" for kw in negative_keywords])
    
    for q in queries:
        if len(image_paths) >= max_images:
            break
        
        # Get image URLs from Bing (More reliable)
        image_urls = bing_images_scrape(q, max_images=5)
        
        # Fallback to DuckDuckGo if Bing fails
        if not image_urls:
            image_urls = duckduckgo_images(q)
        
        for image_url in image_urls:
            if len(image_paths) >= max_images:
                break
            
            with used_urls_lock:
                if image_url in used_urls:
                    continue
            
            # Download and verify
            try:
                img_resp = requests.get(image_url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
                # Increase minimum size to 15KB to filter out simple icons/drawings
                if img_resp.status_code != 200 or len(img_resp.content) < 15360: 
                    continue
                
                # Verify it's a valid image
                img = Image.open(BytesIO(img_resp.content))
                img.verify()
                
                # Check minimum dimensions
                img = Image.open(BytesIO(img_resp.content))
                width, height = img.size
                if width < 200 or height < 200:  # Skip tiny images
                    continue
                
                # Convert and save
                img = img.convert("RGB")
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
                    img.save(f.name, format="JPEG", quality=90)
                    image_paths.append(f.name)
                    with used_urls_lock:
                        used_urls.add(image_url)
                    
            except Exception:
                continue
    
    # FINAL FALLBACK: Unsplash (High reliability)
    if len(image_paths) < max_images:
        try:
            # Use direct Unsplash download URL
            unsplash_url = f"https://images.unsplash.com/photo-1500000000000?auto=format&fit=crop&w=800&q=80&q={requests.utils.quote(query)}"
            # Actually, better to use the source API or a direct keyword URL
            keyword_url = f"https://source.unsplash.com/featured/800x600?{requests.utils.quote(query)}"
            resp = requests.get(keyword_url, timeout=10, allow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 2048:
                img = Image.open(BytesIO(resp.content)).convert("RGB")
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
                    img.save(f.name, format="JPEG")
                    image_paths.append(f.name)
        except:
            pass

    return image_paths


def duckduckgo_images(query: str) -> list[str]:
    """Fetch images from DuckDuckGo search."""
    try:
        # DuckDuckGo image search
        url = f"https://duckduckgo.com/?q={requests.utils.quote(query)}&iax=images&ia=images"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        
        # Get the page
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return []
        
        # Extract image URLs from the response
        # Look for image URLs in the JSON data embedded in the page
        urls = []
        # Pattern to find image URLs
        pattern = r'https?://[^\s\"<>]+\.(?:jpg|jpeg|png|webp)'
        matches = re.findall(pattern, resp.text, re.IGNORECASE)
        
        for match in matches[:10]:
            if match.startswith('http') and 'duckduckgo' not in match.lower():
                urls.append(match)
        
        return urls[:5]
    except Exception:
        return []


def wikipedia_image(query: str) -> str | None:
    """Get image from Wikipedia."""
    try:
        # Search Wikipedia
        search_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={requests.utils.quote(query)}&format=json"
        resp = requests.get(search_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        results = data.get("query", {}).get("search", [])
        if not results:
            return None
        
        # Get first result's page ID
        page_title = results[0].get("title", "")
        if not page_title:
            return None
        
        # Get page images
        image_url = f"https://en.wikipedia.org/w/api.php?action=query&titles={requests.utils.quote(page_title)}&prop=pageimages&format=json&pithumbsize=800"
        resp = requests.get(image_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        for page_id, page_data in pages.items():
            thumbnail = page_data.get("thumbnail", {})
            source = thumbnail.get("source", "")
            if source:
                return source
        
        return None
    except Exception:
        return None


def download_and_save_image(image_url: str, used_urls: set) -> str | None:
    """Download image and save to temp file."""
    with used_urls_lock:
        if not image_url or image_url in used_urls:
            return None
    
    try:
        resp = requests.get(image_url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200 and len(resp.content) >= 1024:
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
                img.save(f.name, format="JPEG", quality=90)
                with used_urls_lock:
                    used_urls.add(image_url)
                return f.name
    except Exception:
        pass
    
    return None


def get_fallback_image(destination: str, used_urls: set) -> str | None:
    """Get a high-quality fallback image for the destination using direct scraping."""
    
    # Try 1: Direct Google Images scraping
    queries = [f"{destination} city landmark", f"{destination} tourism", f"{destination} scenic view"]
    
    for query in queries:
        image_urls = bing_images_scrape(query, max_images=5)
        for image_url in image_urls:
            path = download_and_save_image(image_url, used_urls)
            if path:
                return path
    
    # Try 2: DuckDuckGo
    ddg_queries = [f"{destination} landmark", f"{destination} tourism", f"{destination} city"]
    for query in ddg_queries:
        urls = duckduckgo_images(query)
        for url in urls:
            path = download_and_save_image(url, used_urls)
            if path:
                return path
    
    # Try 3: Wikipedia
    wiki_url = wikipedia_image(destination)
    if wiki_url:
        path = download_and_save_image(wiki_url, used_urls)
        if path:
            return path
    
    # Try 4: Unsplash
    try:
        unsplash_url = f"https://source.unsplash.com/800x600/?{requests.utils.quote(destination + ' landmark')}"
        resp = requests.get(unsplash_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
        if resp.status_code == 200 and len(resp.content) >= 1024:
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
                img.save(f.name, format="JPEG", quality=90)
                return f.name
    except Exception:
        pass
    
    return None


def create_placeholder_image(destination: str, place_name: str) -> str:
    """Create a colored placeholder image with text when no real image is available."""
    colors = [
        (59, 130, 246),   # Blue
        (16, 185, 129),   # Green
        (245, 158, 11),   # Orange
        (139, 92, 246),   # Purple
        (236, 72, 153),   # Pink
    ]
    
    # Use hash of place_name to pick consistent color
    color_idx = hash(place_name) % len(colors)
    color = colors[color_idx]
    
    # Create image
    img = Image.new('RGB', (400, 300), color)
    
    # Add text overlay
    try:
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(img)
        
        # Try to use a font, fallback to default
        try:
            font = ImageFont.truetype("arial.ttf", 20)
            small_font = ImageFont.truetype("arial.ttf", 16)
        except:
            font = ImageFont.load_default()
            small_font = ImageFont.load_default()
        
        # Draw place name
        text = safe_text(place_name)[:40]
        # Get text size for centering
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (400 - text_width) // 2
        y = (300 - text_height) // 2 - 10
        
        # Draw white text with shadow
        draw.text((x+2, y+2), text, fill=(0, 0, 0), font=font)
        draw.text((x, y), text, fill=(255, 255, 255), font=font)
        
        # Draw destination below
        dest_text = safe_text(destination)[:30]
        bbox2 = draw.textbbox((0, 0), dest_text, font=small_font)
        text_width2 = bbox2[2] - bbox2[0]
        x2 = (400 - text_width2) // 2
        y2 = y + 35
        
        draw.text((x2+1, y2+1), dest_text, fill=(0, 0, 0), font=small_font)
        draw.text((x2, y2), dest_text, fill=(255, 255, 255), font=small_font)
        
    except Exception:
        pass
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
        img.save(f.name, format="JPEG", quality=85)
        return f.name


def build_day_images(day_places: list[str], destination: str, used_urls: set) -> list[dict]:
    """Build list of images with place names for a day (3-4 images). 100% guarantee."""
    images_with_names = []
    
    # Try to get images for each place
    for place in day_places[:4]:
        if len(images_with_names) >= 4:
            break
        paths = fetch_verified_images(place, destination, used_urls, max_images=1)
        if paths:
            images_with_names.append({"path": paths[0], "name": place})
        else:
            # Try fallback for this specific place
            fallback = get_fallback_image(place, used_urls)
            if fallback:
                images_with_names.append({"path": fallback, "name": place})
    
    # Fill remaining slots with destination fallback
    while len(images_with_names) < 4:
        fallback = get_fallback_image(destination, used_urls)
        if fallback:
            images_with_names.append({"path": fallback, "name": destination})
        else:
            # Absolute last resort: Placeholder
            placeholder = create_placeholder_image(destination, destination)
            images_with_names.append({"path": placeholder, "name": destination})
    
    return images_with_names


# ==================== ITINERARY GENERATION ====================

def generate_itinerary(customer: str, destination: str, start_date: date, end_date: date) -> dict:
    """Generate day-by-day itinerary with AI or template."""
    days = generate_days(start_date, end_date)
    
    # Try AI generation first
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    
    if api_key:
        try:
            itinerary = generate_ai_itinerary(customer, destination, days, api_key)
            if itinerary:
                return itinerary
        except Exception:
            pass
    
    # Fallback to template generation
    return generate_template_itinerary(customer, destination, days)


def generate_ai_itinerary(customer: str, destination: str, days: list[date], api_key: str) -> dict | None:
    """Generate itinerary using AI API."""
    prompt = f"""Create a detailed travel itinerary for {customer} visiting {destination}.

Create a JSON response with this exact structure:
{{
  "customer": "{customer}",
  "destination": "{destination}",
  "days": [
    {{
      "date": "YYYY-MM-DD",
      "title": "Day Title",
      "morning": "Morning activities...",
      "afternoon": "Afternoon activities...",
      "evening": "Evening activities...",
      "places": ["Place 1", "Place 2", "Place 3", "Place 4"]
    }}
  ]
}}

Requirements:
- {len(days)} days total from {days[0]} to {days[-1]}
- Each day must have 4 specific places
- Morning, Afternoon, Evening sections
- First day is "Arrival & First Impressions"
- Last day is "Departure Day"
- Real, specific landmarks and attractions only

Return ONLY valid JSON."""

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
            },
            timeout=60,
        )
        
        if response.status_code != 200:
            return None
            
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        
        # Extract JSON
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1:
            return None
            
        itinerary = json.loads(content[start:end+1])
        
        # Global place deduplication
        seen_places = set()
        
        # Ensure all dates are formatted correctly and places are unique
        for i, day_data in enumerate(itinerary.get("days", [])):
            if i < len(days):
                day_data["date"] = days[i].strftime("%Y-%m-%d")
                day_data["date_display"] = format_date(days[i])
                
                # Filter out duplicate places
                unique_day_places = []
                for p in day_data.get("places", []):
                    if p.lower() not in seen_places:
                        unique_day_places.append(p)
                        seen_places.add(p.lower())
                
                # If we have less than 4, add more specific landmarks if possible
                day_data["places"] = unique_day_places[:4]
                
        return itinerary
        
    except Exception:
        return None


def generate_template_itinerary(customer: str, destination: str, days: list[date]) -> dict:
    """Generate template-based itinerary when AI fails."""
    itinerary_days = []
    
    dest_lower = destination.lower()
    
    # Pakistan City-Specific Landmark Database
    pakistan_cities = {
        "lahore": [
            "Badshahi Mosque Lahore", "Lahore Fort", "Minar-e-Pakistan", "Shalamar Gardens Lahore",
            "Wazir Khan Mosque Lahore", "Anarkali Bazaar", "Wagah Border Ceremony", "Delhi Gate Lahore",
            "Lahore Museum", "Jallo Park", "Greater Iqbal Park", "Liberty Market Lahore",
            "Data Darbar", "Chauburji Lahore", "Shahi Hammam", "Lawrence Gardens",
            "Tomb of Jahangir", "Gulshan-e-Iqbal Park", "Model Town Park", "Lahore Zoo",
            "Food Street Fort Road", "Packages Mall Lahore", "Emporium Mall", "Race Course Park"
        ],
        "karachi": [
            "Mohatta Palace Karachi", "Mazar-e-Quaid Karachi", "Clifton Beach Karachi", "Pakistan Maritime Museum",
            "Frere Hall Karachi", "PAF Museum Karachi", "Dolmen Mall Clifton", "Port Grand Karachi",
            "Chaukhandi Tombs", "Turtle Beach Karachi", "Empress Market", "DHA Golf Club Karachi",
            "National Museum of Pakistan", "Hill Park Karachi", "Bin Qasim Park", "St. Patrick's Cathedral",
            "Do Darya Karachi", "Dreamworld Resort", "Lucky One Mall", "Manora Island"
        ],
        "islamabad": [
            "Faisal Mosque Islamabad", "Daman-e-Koh Islamabad", "Lok Virsa Museum", "Pakistan Monument",
            "Rawal Lake Islamabad", "Centaurus Mall", "Margalla Hills National Park", "Saidpur Village",
            "Shakarparian Park", "Rose and Jasmine Garden", "National Art Gallery Islamabad", "The Monal",
            "Lake View Park", "Fatima Jinnah Park", "Safari Park Islamabad", "Giga Mall",
            "Shah Allah Ditta Caves", "Golra Sharif Railway Museum", "Islamabad Club", "Japanese Park",
            "Safa Gold Mall", "F-6 Markaz", "F-7 Flower Market", "National Museum of Natural History",
            "Blue Area Islamabad", "Supreme Court Building", "Parliament House Islamabad", "Rawal Dam",
            "Simly Dam", "King Faisal Mosque Exterior", "Margalla Road Drive", "Islamabad Zoo Ruins"
        ],
        "bahawalpur": [
            "Noor Mahal Bahawalpur", "Derawar Fort", "Uch Sharif", "Abbasi Mosque",
            "Gulzar Mahal", "Sadiq Garh Palace", "Lal Suhanra National Park", "Bahawalpur Zoo",
            "Central Library Bahawalpur", "Darbar Mahal", "Jamia Masjid Al-Sadiq", "Farid Gate"
        ],
        "murree": [
            "Mall Road Murree", "Pindi Point", "Kashmir Point", "Patriata Chair Lift",
            "Ayubia National Park", "Nathia Gali", "Thandiani", "Mushkpuri Peak",
            "Dunga Gali", "Pipe Line Track", "Changla Gali", "Ghora Gali"
        ],
        "swat": [
            "Mingora Bazaar", "Malam Jabba", "Fizagat Park", "Marghuzar White Palace",
            "Kalam Valley", "Mahodand Lake", "Ushu Forest", "Gabina Jabba",
            "Madyan Swat", "Behrain Swat", "Kundol Lake", "Spin Khwar Lake"
        ]
    }
    
    # Check if a specific city is mentioned in the destination
    matched_city = None
    for city in pakistan_cities:
        if city in dest_lower:
            matched_city = city
            break
            
    # Extract all relevant landmarks into a single pool
    landmark_pool = []
    if matched_city:
        landmark_pool = pakistan_cities[matched_city].copy()
    elif "pakistan" in dest_lower:
        # Flatten all Pakistan city groups into one pool
        for group in [
            ["Faisal Mosque Islamabad", "Daman-e-Koh Islamabad", "Lok Virsa Museum", "Pakistan Monument"],
            ["Badshahi Mosque Lahore", "Lahore Fort", "Minar-e-Pakistan", "Shalamar Gardens Lahore"],
            ["Wazir Khan Mosque Lahore", "Anarkali Bazaar", "Wagah Border Ceremony", "Delhi Gate Lahore"],
            ["Noor Mahal Bahawalpur", "Derawar Fort", "Uch Sharif", "Abbasi Mosque"],
            ["Mohatta Palace Karachi", "Mazar-e-Quaid Karachi", "Clifton Beach Karachi", "Pakistan Maritime Museum"],
            ["Lake Saif-ul-Malook", "Babusar Pass", "Lulusar Lake", "Kiwai Waterfalls"],
            ["Hunza Valley", "Baltit Fort", "Attabad Lake", "Passu Cones"]
        ]:
            landmark_pool.extend(group)
    else:
        landmark_pool = [
            f"Main landmark of {destination}",
            f"Historic center of {destination}",
            f"Famous museum in {destination}",
            f"Popular market in {destination}",
            f"Scenic viewpoint in {destination}",
            f"Cultural district of {destination}",
            f"Old town area of {destination}",
            f"Riverside/waterfront of {destination}",
            f"Local park in {destination}",
            f"Shopping mall in {destination}",
            f"Botanical garden of {destination}",
            f"Art gallery in {destination}"
        ]

    for i, day in enumerate(days):
        # Pick 4 unique landmarks from the pool
        day_places = []
        for j in range(4):
            if landmark_pool:
                day_places.append(landmark_pool.pop(0))
            else:
                # Varied fallback names to ensure unique search results without using unprofessional numbering
                fallback_names = [
                    "Historic District", "Central Square", "Riverside View", "Heritage Site",
                    "Cultural Plaza", "Main Boulevard", "Green Park", "Art District",
                    "Old Town Area", "Scenic Lookout", "Clock Tower Square", "Garden Walk",
                    "Modern Skyview", "Waterfront Area", "Memorial Park", "Market Street",
                    "Government Building", "University Grounds", "Botanical Corner", "Craft Center"
                ]
                name_idx = (i * 4 + j) % len(fallback_names)
                day_places.append(f"{fallback_names[name_idx]} in {city_name}")
        # Determine the city name for descriptions
        if matched_city:
            city_name = matched_city.title()
        elif "pakistan" in dest_lower:
            # For country-wide, try to infer city from the first landmark of the day
            parts = day_places[0].split()
            city_name = parts[-1] if len(parts) > 0 else destination
        else:
            city_name = destination
        
        if i == 0:
            title = f"Arrival & First Impressions of {city_name}"
            morning = f"Arrive in {city_name}. Visit {day_places[0]} to start your journey."
            afternoon = f"Explore the vibrant atmosphere of {day_places[1]}."
            evening = f"Enjoy a welcome dinner near {day_places[2]}."
        elif i == len(days) - 1:
            title = f"Departure from {city_name}"
            morning = f"Final morning visit to {day_places[0]} for souvenirs."
            afternoon = f"Last-minute photography at {day_places[1]}."
            evening = f"Transfer to the airport after a brief stop at {day_places[2]}."
        else:
            title = f"Exploring {city_name}"
            morning = f"Start your day with a visit to the historic {day_places[0]}."
            afternoon = f"Continue your exploration at {day_places[1]} and local surroundings."
            evening = f"Evening stroll through {day_places[2]} followed by dinner at a popular spot."
        
        itinerary_days.append({
            "date": day.strftime("%Y-%m-%d"),
            "date_display": format_date(day),
            "title": title,
            "morning": morning,
            "afternoon": afternoon,
            "evening": evening,
            "places": day_places,
        })
    
    return {
        "customer": customer,
        "destination": destination,
        "days": itinerary_days,
    }


# ==================== PDF GENERATION ====================

class TravelPDF(FPDF):
    """Custom PDF class with page numbering."""
    
    def __init__(self, customer: str):
        super().__init__()
        self.customer = customer
        self.total_pages = 0
        
    def header(self):
        """No header on every page for this layout."""
        pass
        
    def footer(self):
        """Add page numbering at the bottom right."""
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}", align="R")

    def draw_timeline(self, y_start, y_end):
        """Draw a vertical line on the left with a calendar icon."""
        self.set_draw_color(220, 220, 220)
        self.set_line_width(0.4)
        self.line(25, y_start, 25, y_end)
        
        # Draw a circle for the icon
        self.set_draw_color(100, 116, 139) # Slate color
        self.set_line_width(0.6)
        self.set_fill_color(255, 255, 255)
        self.circle(25, y_start + 1, 6, style="FD")
        
        # Calendar icon representation
        self.set_draw_color(100, 116, 139)
        self.rect(22.5, y_start - 0.5, 5, 4, style="D")
        self.line(22.5, y_start + 0.5, 27.5, y_start + 0.5)


def create_pdf(itinerary: dict, day_images: dict) -> bytes:
    """Create professional PDF with grid layout."""
    customer = itinerary.get("customer", "Customer")
    destination = itinerary.get("destination", "Destination")
    days = itinerary.get("days", [])
    
    pdf = TravelPDF(customer)
    pdf.set_auto_page_break(auto=True, margin=20)
    
    # --- Page 1: COVER PAGE ---
    pdf.add_page()
    pdf.ln(20)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(50, 50, 50)
    pdf.cell(0, 10, safe_text(customer.upper()), ln=True, align="C")
    pdf.ln(10)
    
    cover_image = None
    if day_images.get(0):
        cover_image = day_images[0][0]["path"]
    
    if cover_image:
        try:
            pdf.image(cover_image, x=20, y=50, w=170, h=100)
        except: pass
    
    pdf.set_y(160)
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_text_color(0, 102, 204)
    pdf.cell(0, 15, "Travel Plan", ln=True, align="C")
    
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(100, 100, 100)
    date_range = f"{compact_date(datetime.strptime(days[0]['date'], '%Y-%m-%d').date())} - {compact_date(datetime.strptime(days[-1]['date'], '%Y-%m-%d').date())}"
    pdf.cell(0, 10, safe_text(date_range), ln=True, align="C")
    
    # --- Page 2: TRIP SUMMARY ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(50, 50, 50)
    pdf.cell(0, 15, "Trip Summary", ln=True)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(10)
    
    y_summary_start = pdf.get_y()
    for i, day in enumerate(days):
        pdf.set_x(30)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(50, 50, 50)
        pdf.cell(40, 8, safe_text(day.get("date_display", "")))
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 8, safe_text(day.get("title", "")), ln=True)
        pdf.ln(2)
        
    y_summary_end = pdf.get_y()
    pdf.set_draw_color(220, 220, 220)
    pdf.line(25, y_summary_start, 25, y_summary_end)
    
    # --- DAY PAGES ---
    for i, day in enumerate(days):
        pdf.add_page()
        y_day_start = pdf.get_y()
        
        pdf.set_font("Helvetica", "B", 18)
        pdf.set_text_color(50, 50, 50)
        pdf.set_x(15)
        pdf.cell(0, 15, safe_text(day.get("date_display", f"Day {i+1}")))
        pdf.set_draw_color(220, 230, 240)
        pdf.line(15, pdf.get_y() + 12, 195, pdf.get_y() + 12)
        pdf.ln(20)
        
        # Activity Sections
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(60, 60, 60)
        
        sections = [
            ("Morning", day.get("morning", "")),
            ("Afternoon", day.get("afternoon", "")),
            ("Evening", day.get("evening", ""))
        ]
        
        def write_activity_text(label, text, places):
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(51, 65, 85)
            pdf.set_draw_color(30, 64, 175)
            pdf.set_fill_color(30, 64, 175)
            pdf.circle(32, pdf.get_y() + 2.5, 0.8, style="FD")
            
            pdf.set_x(35)
            pdf.write(6, f" {label}: ")
            
            pdf.set_font("Helvetica", "", 11)
            pdf.set_text_color(71, 85, 105)
            
            words = text.split()
            for word in words:
                clean_word = re.sub(r'[^a-zA-Z]', '', word)
                is_landmark = any(p.lower().startswith(clean_word.lower()) for p in places if len(clean_word) > 3)
                
                if is_landmark:
                    pdf.set_text_color(37, 99, 235)
                    pdf.write(6, word + " ")
                    pdf.set_text_color(71, 85, 105)
                else:
                    pdf.write(6, word + " ")
            pdf.ln(8)

        for label, text in sections:
            write_activity_text(label, text, day.get("places", []))
            
        pdf.ln(5)
        
        # Image Grid (4-image Mosaic)
        images = day_images.get(i, [])
        if images and len(images) >= 4:
            content_x = 35
            y_img_start = pdf.get_y()
            grid_w = 160
            img_large_w = 100
            img_large_h = 70
            gap = 2
            img_sm_w = (grid_w - img_large_w - 2 * gap) / 2
            img_sm_h = (img_large_h - gap) / 2
            
            try:
                pdf.image(images[0]["path"], x=content_x, y=y_img_start, w=img_large_w, h=img_large_h)
                pdf.image(images[1]["path"], x=content_x + img_large_w + gap, y=y_img_start, w=img_sm_w, h=img_sm_h)
                pdf.image(images[2]["path"], x=content_x + img_large_w + gap, y=y_img_start + img_sm_h + gap, w=img_sm_w, h=img_sm_h)
                pdf.image(images[3]["path"], x=content_x + img_large_w + img_sm_w + 2 * gap, y=y_img_start, w=img_sm_w, h=img_large_h)
            except: pass
            
            pdf.set_y(y_img_start + img_large_h + 10)
        
        y_day_end = pdf.get_y()
        pdf.draw_timeline(y_day_start + 18, y_day_end)
    
    # Update total pages and regenerate for correct footer
    output = pdf.output()
    return bytes(output) if isinstance(output, bytearray) else output.encode("latin-1", errors="ignore") if isinstance(output, str) else output


# ==================== MAIN APP ====================

def main():
    """Main Streamlit application."""
    
    st.markdown('<p class="main-header">AI Travel Planner</p>', unsafe_allow_html=True)
    st.markdown("<p style='color:#cbd5e1;'>Generate professional travel itineraries with verified images</p>", unsafe_allow_html=True)
    
    # Sidebar form
    with st.sidebar:
        st.header("Trip Details")
        
        customer_name = st.text_input("Customer Name", placeholder="e.g., Mr. Rana Muhammad Asif")
        destination = st.text_input("Destination", placeholder="e.g., Cape Town")
        
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("Start Date", date.today())
        with col2:
            end_date = st.date_input("End Date", date.today() + timedelta(days=7))
        
        generate_btn = st.button("Generate Itinerary", type="primary")
        
        st.divider()
        st.caption("Images fetched from Bing Search with verification")
        
        if not os.getenv("OPENROUTER_API_KEY"):
            st.warning("⚠️ API Key missing. Using basic templates instead of AI.")
    
    # Main content
    if generate_btn:
        if not customer_name or not destination:
            st.error("Please fill in all required fields.")
            return
            
        if end_date < start_date:
            st.error("End date must be after start date.")
            return
        
        progress = st.progress(0, text="Generating itinerary...")
        
        try:
            # Step 1: Generate itinerary
            progress.progress(20, text="Creating day-by-day plan...")
            itinerary = generate_itinerary(customer_name, destination, start_date, end_date)
            
            # Step 2: Fetch images for each day (Parallelized)
            progress.progress(50, text="Fetching verified images (Parallel)...")
            used_urls = set()
            day_images = {}
            
            days = itinerary.get("days", [])
            
            def fetch_day_images(idx, day_data):
                return idx, build_day_images(day_data.get("places", []), destination, used_urls)

            with ThreadPoolExecutor(max_workers=min(len(days), 8)) as executor:
                futures = [executor.submit(fetch_day_images, i, day) for i, day in enumerate(days)]
                for future in as_completed(futures):
                    idx, result = future.result()
                    day_images[idx] = result
            
            # Step 3: Generate PDF
            progress.progress(80, text="Building PDF...")
            pdf_bytes = create_pdf(itinerary, day_images)
            
            progress.progress(100, text="Done!")
            
            # Display results
            st.success(f"Itinerary ready for {customer_name}!")
            
            # Hero card
            date_range = f"{compact_date(start_date)} - {compact_date(end_date)}"
            st.markdown(f"""
            <div class="hero-card">
                <h3 style="color:#f8fafc;margin:0;">{destination}</h3>
                <p style="color:#93c5fd;margin:0.5rem 0;">{date_range}</p>
                <p style="color:#cbd5e1;margin:0;">{len(days)} days • Professional PDF with verified images</p>
            </div>
            """, unsafe_allow_html=True)
            
            # Display days
            for i, day in enumerate(days):
                with st.container():
                    st.markdown(f"""
                    <div class="day-card">
                        <h4 style="color:#f8fafc;margin:0;">{day.get('date_display', '')}</h4>
                        <p style="color:#38bdf8;margin:0.2rem 0;font-weight:600;">{day.get('title', '')}</p>
                        <p style="color:#e5e7eb;margin:0.3rem 0;"><strong>Morning:</strong> {day.get('morning', '')}</p>
                        <p style="color:#e5e7eb;margin:0.3rem 0;"><strong>Afternoon:</strong> {day.get('afternoon', '')}</p>
                        <p style="color:#e5e7eb;margin:0.3rem 0;"><strong>Evening:</strong> {day.get('evening', '')}</p>
                    </div>
                    """, unsafe_allow_html=True)
            
            # Download button
            st.download_button(
                label="Download PDF Itinerary",
                data=BytesIO(pdf_bytes),
                file_name=f"{destination.lower().replace(' ', '_')}_itinerary.pdf",
                mime="application/pdf",
            )
            
        except Exception as e:
            st.error(f"Error generating itinerary: {str(e)}")
            progress.empty()


if __name__ == "__main__":
    main()
