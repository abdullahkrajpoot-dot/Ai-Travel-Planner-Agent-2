import json
import os
import re
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import requests
import streamlit as st
from fpdf import FPDF


st.set_page_config(page_title="AI Travel Planner", layout="wide")

st.markdown(
    """
    <style>
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


DESTINATION_LANDMARKS = {
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


def get_destination_landmarks(destination: str) -> list[list[str]]:
    key = slugify(destination).replace("_", " ")
    for name, landmarks in DESTINATION_LANDMARKS.items():
        if name in key:
            return landmarks
    normalized_destination = safe_text(destination) or "the destination"
    return [
        [
            f"Main landmark of {normalized_destination}",
            f"Historic center of {normalized_destination}",
            f"Signature museum of {normalized_destination}",
            f"Scenic viewpoint in {normalized_destination}",
        ]
    ]


def fallback_plan(destination: str, start_date: date, end_date: date):
    total_days = max((end_date - start_date).days + 1, 1)
    templates = get_destination_landmarks(destination)
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
                "morning": f"Begin with {places[0]} and {places[1]} for a relaxed but polished introduction to {destination}.",
                "afternoon": f"Continue toward {places[2]} for local culture, lunch, and neighborhood exploration.",
                "evening": f"End the day around {places[3]} with sunset views, dinner, and comfortable transfer back to the hotel.",
                "places": places,
            }
        )
    return days


def parse_ai_plan(raw_text: str, destination: str, start_date: date, end_date: date):
    total_days = max((end_date - start_date).days + 1, 1)
    expected_dates = [start_date + timedelta(days=i) for i in range(total_days)]
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

    for day in normalized:
        if not day["places"]:
            day["places"] = [f"{destination} city center"]

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


def download_image(url: str, suffix: str = ".jpg"):
    try:
        response = requests.get(
            url,
            timeout=30,
            allow_redirects=True,
            headers={
                "User-Agent": "AI-Travel-Planner/1.0 (+https://github.com/abdullahkrajpoot-dot/Ai-Travel-Planner-Agent)",
                "Accept": "image/jpeg,image/png,image/*;q=0.8,*/*;q=0.5",
            },
        )
        response.raise_for_status()
        content_type = (response.headers.get("Content-Type", "") or "").lower()
        if "image/" not in content_type:
            return None

        ext_by_type = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
        }
        chosen_suffix = ext_by_type.get(content_type.split(";")[0], suffix)
        if chosen_suffix not in {".jpg", ".png"}:
            return None

        if len(response.content) < 1024:
            return None

        with tempfile.NamedTemporaryFile(delete=False, suffix=chosen_suffix) as temp_file:
            temp_file.write(response.content)
            return temp_file.name
    except Exception:
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
                "srlimit": 3,
                "srsearch": query,
            },
            headers={"User-Agent": "AI-Travel-Planner/1.0"},
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
            image_url = _wikipedia_summary_image(title)
            if image_url:
                return image_url
        return None
    except Exception:
        return None


def build_place_image(place_name: str, destination: str | None = None):
    # Strict place matching first to keep image relevance high.
    queries = _candidate_place_queries(place_name, destination)

    for query in queries:
        direct_url = _wikipedia_summary_image(query)
        if direct_url:
            path = download_image(direct_url, suffix=".jpg")
            if path:
                return {"place": place_name, "url": direct_url, "path": path}

        searched_url = _wikipedia_search_image(query)
        if not searched_url:
            continue
        path = download_image(searched_url, suffix=".jpg")
        if path:
            return {"place": place_name, "url": searched_url, "path": path}

    # Do not force unrelated stock images when exact place imagery isn't available.
    return {"place": place_name, "url": None, "path": None}


def build_image_bundle(days: list[dict], destination: str):
    cover_candidates = []
    for day in days[:2]:
        cover_candidates.extend(day.get("places", []))
    if not cover_candidates:
        cover_candidates = [destination]

    unique_places = []
    for place in cover_candidates + [place for day in days for place in day.get("places", [])]:
        if place not in unique_places:
            unique_places.append(place)

    results = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(build_place_image, place, destination): place for place in unique_places}
        for future in as_completed(future_map):
            place = future_map[future]
            try:
                results[place] = future.result()
            except Exception:
                results[place] = {"place": place, "url": None, "path": None}

    cover_path = None
    for place in cover_candidates:
        cover_path = results.get(place, {}).get("path")
        if cover_path:
            break

    day_images = []
    for day in days:
        entries = []
        for place in day.get("places", []):
            entry = results.get(place, {"place": place, "url": None, "path": None})
            if entry.get("path"):
                entries.append(entry)
        if not entries:
            for fallback_places in get_destination_landmarks(destination):
                for place in fallback_places:
                    entry = results.get(place)
                    if not entry:
                        entry = build_place_image(place, destination)
                        results[place] = entry
                    if entry.get("path") and all(item["place"] != entry["place"] for item in entries):
                        entries.append(entry)
                    if len(entries) == 4:
                        break
                if len(entries) == 4:
                    break
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
        pdf.image(cover_path, x=10, y=38, w=190, h=86)
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
    output = pdf.output()
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
    client_name = st.text_input("Client Name", "Mr. Rana Muhammad Asif")
    destination = st.text_input("Destination", "Belgium")
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
    progress = st.progress(0, text="Generating itinerary...")
    itinerary_days = get_ai_plan(client_name, destination, start_date, end_date)
    progress.progress(35, text="Fetching images for PDF (place matched)...")
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
    cleanup_temp_images(cover_path, day_images)

result = st.session_state.get("trip_result")

if result:
    st.success("Your travel plan is ready.")
    st.markdown(
        f"""
        <div class="hero-card">
            <div class="day-title">{html_text(result["plan_title"])} - {html_text(result["destination"])}</div>
            <div class="day-date">{compact_date(result["start_date"])} to {compact_date(result["end_date"])}</div>
            <span class="summary-pill">Client: {html_text(result["client_name"])}</span>
            <span class="summary-pill">{len(result["days"])} itinerary days</span>
            <span class="summary-pill">Place-matched image search</span>
            <span class="summary-pill">HD PDF export</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Per your request: don't show images on the website, only in the PDF.
    for item in result["days"]:
        place_html = "".join(
            f'<span class="place-chip">{html_text(place)}</span>' for place in item.get("places", [])
        )
        st.markdown(
            f"""
            <div class="day-card">
                <div class="day-date">{html_text(format_day_label(item["date"]))}</div>
                <div class="day-title">{html_text(item["title"])}</div>
                <div class="section-title">Morning</div>
                <div>{html_text(item["morning"])}</div>
                <div class="section-title">Afternoon</div>
                <div>{html_text(item["afternoon"])}</div>
                <div class="section-title">Evening</div>
                <div>{html_text(item["evening"])}</div>
                <div class="section-title">Matched Places (for PDF photos)</div>
                <div>{place_html or '<span class="place-chip">No places found</span>'}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.download_button(
        label="Download Matching PDF",
        data=BytesIO(result["pdf_bytes"]),
        file_name=f"{slugify(result['destination'])}_travel_plan.pdf",
        mime="application/pdf",
    )