# Accommodation Service - Architecture & Code Summary

## Service Overview
The **Accommodation Service** is a multi-tier microservice application designed to manage accommodations (hotels, hostels, ryokans, apartments, guesthouses) and provide AI-powered travel recommendations using local LLMs (Ollama). 

The architecture consists of three main components:
1. **Frontend (`frontend/`)**: A Flask-based web application rendering HTML templates (with HTMX support) that interacts with the backend service.
2. **Backend (`backend/`)**: A Flask REST API that coordinates business logic, communicates with the database service, and integrates with Ollama LLM for intelligent ranking and recommendations.
3. **Database (`database/`)**: A Flask & SQLAlchemy SQLite-backed microservice providing persistent storage, filtering, sorting, pagination, and health checks for accommodations and amenities.

---

## File Breakdown & Function Descriptions

### 1. Frontend Service (`frontend/`)

#### `frontend/app.py`
Main Flask application for the user interface.
* **`create_app()`**: Factory function initializing the Flask app, configuring routes, and setting up backend communication helpers.
* **`backend_request(method, path, params, json_body)`**: Makes HTTP requests to the backend service.
* **`parse_float(value)`**: Safely converts form input values to float, returning `None` on failure.
* **`parse_form_payload()`**: Extracts and cleans accommodation form data submitted by users.
* **`fetch_accommodations(params)`**: Retrieves a filtered/sorted list of accommodations from the backend.
* **`fetch_accommodation(accommodation_id)`**: Fetches details for a specific accommodation ID.
* **`index()`**: Renders the main listing/search page with filters (supports HTMX partial updates).
* **`new_accommodation_form()`**: Renders the form for creating a new accommodation.
* **`create_accommodation()`**: Handles form submission to create a new accommodation via the backend.
* **`accommodation_detail(accommodation_id)`**: Renders the detailed view of an accommodation.
* **`edit_accommodation_form(accommodation_id)`**: Renders the form to edit an existing accommodation.
* **`update_accommodation(accommodation_id)`**: Updates an existing accommodation via PATCH request to the backend.
* **`delete_accommodation(accommodation_id)`**: Deletes an accommodation via DELETE request to the backend.
* **`recommend_page()`**: Renders the AI recommendation query page.
* **`recommend()`**: Submits user city, max price, and interests to the backend AI recommendation endpoint and renders results.
* **`health()`**: Returns health status for the frontend app.

---

### 2. Backend Service (`backend/src/`)

#### `backend/src/app.py`
Entry point for the backend API service.
* **`create_app()`**: Initializes Flask, enables CORS, and registers accommodations and AI blueprints.

#### `backend/src/routes/accommodations.py`
REST endpoints for managing accommodations, proxying requests to the database service.
* **`_forwarded_params()`**: Filters allowed query parameters from incoming requests.
* **`health()`**: Checks and returns database service health status.
* **`list_accommodations()`**: Forwards query parameters to list/filter accommodations from the database service.
* **`get_accommodation(accommodation_id)`**: Retrieves a single accommodation by ID.
* **`create_accommodation()`**: Creates a new accommodation record.
* **`patch_accommodation(accommodation_id)`**: Partially updates an accommodation.
* **`put_accommodation(accommodation_id)`**: Replaces an accommodation record.
* **`delete_accommodation(accommodation_id)`**: Deletes an accommodation.

#### `backend/src/routes/ai.py`
Endpoints for AI-powered planning, ranking, and recommendations.
* **`_plan_request(payload)`**: Derives a structured search plan (city, max price, interests) from user input.
* **`_query_filters(plan)`**: Converts the AI plan into database query filter parameters.
* **`_rank_records(records, plan)`**: Uses the LLM client to rank accommodation candidates by relevance.
* **`ai_health()`**: Checks and returns Ollama LLM connection and model status.
* **`ai_recommend()`**: Orchestrates the full AI recommendation flow: parses plan -> filters database records -> ranks candidates via LLM -> generates final recommendation response.

#### `backend/src/services/database_api.py`
HTTP client wrapper for communicating with the database microservice.
* **`DatabaseServiceError`**: Custom exception for database connectivity or communication failures.
* **`NotFoundError`**: Exception raised on 404 responses from the database service.
* **`ValidationError`**: Exception raised on 400/422 validation errors.
* **`_read_error_payload(response)`**: Helper to extract error message JSON from HTTP responses.
* **`_request(method, path, **kwargs)`**: Core HTTP request helper with error handling and status code mapping.
* **`list_accommodations(params)`**: Calls GET `/accommodations`.
* **`get_accommodation(id)`**: Calls GET `/accommodations/{id}`.
* **`create_accommodation(payload)`**: Calls POST `/accommodations`.
* **`update_accommodation(id, payload)`**: Calls PATCH `/accommodations/{id}`.
* **`replace_accommodation(id, payload)`**: Calls PUT `/accommodations/{id}`.
* **`delete_accommodation(id)`**: Calls DELETE `/accommodations/{id}`.
* **`get_health()`**: Calls GET `/health`.

#### `backend/src/services/llm_client.py`
Client wrapper for interacting with the Ollama LLM backend.
* **`LLMServiceError`**: Custom exception for LLM service failures.
* **`get_ollama_status()`**: Checks if Ollama is reachable and if the target model is loaded.
* **`generate_recommendation(prompt, model, timeout)`**: Sends prompt generation request to Ollama API.
* **`_build_prompt(request_payload, shortlist)`**: Builds recommendation prompt using template files.
* **`_build_ranking_prompt(request_payload, candidates)`**: Builds candidate ranking prompt.
* **`_parse_json_recommendations(value)`**: Safely parses JSON strings or code blocks returned by the LLM.
* **`rank_accommodations(request_payload, candidates)`**: Prompts LLM to score and rank candidates.
* **`recommend_accommodation(request_payload, shortlist)`**: Prompts LLM to generate top accommodation recommendations with reasoning.

#### `backend/src/services/prompt_loader.py`
* **`load_prompt(filename)`**: Reads text prompt templates from the prompts directory.

---

### 3. Database Service (`database/src/`)

#### `database/src/app.py`
Entry point for the database microservice.
* **`create_app()`**: Initializes Flask and registers health and accommodation blueprints.

#### `database/src/db.py`
Database engine and session management.
* **`set_sqlite_pragma(dbapi_connection, connection_record)`**: Enables SQLite foreign key enforcement.
* **`get_db()`**: Provides a scoped database session.

#### `database/src/init_db.py`
Database migration and seeding utility.
* **`init_db(force)`**: Creates database tables and seeds initial accommodation/amenity data from `seed.json`.

#### `database/src/routes/accommodations.py`
CRUD endpoints and advanced query filters using SQLAlchemy.
* **`serialize_accommodation(acc, full)`**: Converts an Accommodation ORM model instance into a JSON dictionary.
* **`list_accommodations()`**: Filters, sorts, and paginates accommodations based on query parameters (`q`, `destination_city`, `type`, price range, rating, amenities).
* **`get_accommodation(acc_id)`**: Retrieves an accommodation by ID or returns 404.
* **`create_accommodation()`**: Validates input and persists a new accommodation record with amenities.
* **`update_accommodation(acc_id)`**: Updates attributes and amenity relationships for an existing accommodation.
* **`delete_accommodation(acc_id)`**: Deletes an accommodation record.

#### `database/src/routes/health.py`
* **`health_check()`**: Executes a lightweight SQL ping (`SELECT 1`) to verify database connectivity.

#### `database/src/models/`
* **`accommodation.py`**: Defines the `Accommodation` SQLAlchemy model (fields, constraints, relationships to amenities).
* **`amenity.py`**: Defines the `Amenity` model.
* **`accommodation_amenity.py`**: Defines the many-to-many association table between accommodations and amenities.

---

## Call Steps & Request Flows

### 1. Standard Listing & Filtering Flow (CRUD)
1. **User Action**: User visits `/` or applies filters on the web frontend.
2. **Frontend (`frontend/app.py`)**: `index()` invokes `fetch_accommodations(filters)`.
3. **Backend Proxy (`backend/src/routes/accommodations.py`)**: `list_accommodations()` calls `database_api.list_accommodations(_forwarded_params())`.
4. **Database Client (`backend/src/services/database_api.py`)**: Sends HTTP `GET http://localhost:6002/accommodations` with query parameters.
5. **Database Service (`database/src/routes/accommodations.py`)**: 
   - Parses query parameters (`q`, `destination_city`, `min_price`, etc.).
   - Executes SQLAlchemy query against SQLite (`accommodation-db.sqlite`).
   - Returns paginated total count and serialized accommodation list as JSON.
6. **Response Rendering**: Backend returns JSON to frontend; frontend renders `list.html` (or `_list_table.html` for HTMX).

### 2. AI Recommendation Flow
1. **User Action**: User enters destination city, maximum price, and interests on `/recommend`.
2. **Frontend (`frontend/app.py`)**: `recommend()` sends a POST request with payload to Backend (`POST /ai/recommend`).
3. **Backend AI Route (`backend/src/routes/ai.py`)**:
   - **Plan**: `_plan_request()` normalizes user inputs (city, price, interests).
   - **Act (Database Lookup)**: `_query_filters()` builds filters and calls `database_api.list_accommodations()`. Database service returns matching records.
   - **Observe / Rank**: `_rank_records()` calls `llm_client.rank_accommodations()` to score candidates via Ollama.
   - **Adapt (LLM Recommendation)**: Calls `llm_client.recommend_accommodation()` passing the prompt template and shortlist to Ollama (`POST http://localhost:11434/api/generate`).
   - Ollama returns structured JSON reasoning and recommendations.
   - Backend constructs final response containing plan, match count, observations, and recommendations.
4. **Response Rendering**: Frontend receives result JSON and renders `recommend.html` displaying top recommended accommodations and AI reasoning.
