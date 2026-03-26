# ELD Trip Planner - Backend

This is the Django backend for the FMCSA-compliant ELD Trip Planner application. It handles routing integration (via Mapbox Directions, with OpenRouteService as an optional geocoding fallback) and computes the complex 70-hour/8-day Hours of Service (HOS) rules.

## Prerequisites

- Python 3.10+
- PostgreSQL (if running in production)

## Tech Stack

- **Framework**: Django 4.2+ & Django REST Framework
- **Authentication**: SimpleJWT
- **Database**: SQLite (local dev) / PostgreSQL (production via Neon)
- **API Documentation**: drf-spectacular (Swagger UI)
- **Security**: django-cors-headers, django-csp

## Getting Started

1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. Configure environment variables (copy from `.env.example`):
   ```bash
   cp .env.example .env
   ```
   **Crucial:** To calculate routes accurately, you need a Mapbox access token. Obtain a free key from [mapbox.com](https://account.mapbox.com/) and set `MAPBOX_ACCESS_TOKEN` in your `.env`. Optionally, you can also provide an `ORS_API_KEY` to enable OpenRouteService-based geocoding; otherwise the app will use a simple city-name fallback for development.

3. Run migrations:
   ```bash
   python manage.py migrate
   ```

4. Start the development server:
   ```bash
   python manage.py runserver
   ```

## API Documentation

When the server is running, you can access the interactive API documentation at:
- **Swagger UI**: `http://localhost:8000/api/v1/docs/`

## Testing

The backend includes a comprehensive pytest suite covering the HOS rule engine (specifically the 70-hour/8-day rule).

To run the tests:
```bash
pytest
# or
pytest -v trips/tests/
```

## Core Algorithm details
The Hours of Service algorithm is located in `trips/hos_engine.py`. It meticulously computes a driver's daily logs, ensuring any on-duty period limits driving correctly against the 11-hour, 14-hour, and 70-hour/8-day rules. Any gaps are automatically filled with OFF_DUTY entries spanning the calendar day.
