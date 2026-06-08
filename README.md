# AI Travel Planner Agent

A professional Streamlit app for generating travel itineraries and downloadable PDF travel plans.

## Overview

This application helps travel agents create clean, day-by-day travel plans for clients. It generates itinerary titles, daily activities, destination places, image-based PDF pages, and a polished downloadable travel plan.

## Key Features

- **AI Travel Itinerary:** Creates day-wise travel plans from client, destination, and travel dates.
- **Arrival & Departure Rules:** First day is arrival/rest only, and last day is departure only with no visits.
- **Middle-Day Sightseeing:** Sightseeing places and images are used only for the main travel days.
- **PDF Download:** Generates a professional PDF travel plan with optimized image handling for smaller file size.
- **Streamlit Interface:** Simple local web app for entering trip details and downloading the final plan.

## Tech Stack

- **Language:** Python 3.x
- **Framework:** Streamlit
- **PDF:** fpdf2
- **Images:** Pillow
- **Web Requests:** requests

## Installation & Setup

1. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   ```

   Windows:

   ```bash
   .\.venv\Scripts\activate
   ```

   Mac/Linux:

   ```bash
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Run the application:

   ```bash
   streamlit run app.py
   ```

4. Open the local app:

   ```text
   http://127.0.0.1:8501
   ```

## Local Helper Scripts

- `start_localhost.bat` starts the Streamlit app on localhost.
- `start_localhost_hidden.vbs` starts the app in a hidden window.
- `stop_localhost_hidden.vbs` stops the hidden localhost server.
