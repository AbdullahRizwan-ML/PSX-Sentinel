# PSX Sentinel
**Institutional-Grade AI Financial Intelligence Platform for the Pakistan Stock Exchange**

### Platform Endpoints
<p align="center">
  <img src="assets/Swagger1.png" alt="Swagger UI Part 1" width="800">
  <br><br>
  <img src="assets/Swagger2.png" alt="Swagger UI Part 2" width="800">
</p>

## Overview
PSX Sentinel is an advanced, enterprise-grade AI financial intelligence platform designed specifically for the Pakistan Stock Exchange (PSX). Built with the architectural rigor of top-tier quantitative finance tools and platforms like Bloomberg Terminal, PSX Sentinel provides robust, observable, and highly reliable financial insights. The platform leverages a state-of-the-art dual intelligence engine, combining traditional machine learning predictive pipelines with a sophisticated multi-agent LLM narrative auditing system to deliver comprehensive market analysis.

## Enterprise Architecture
PSX Sentinel is engineered for high availability, deep observability, and fault tolerance, avoiding brittle abstraction frameworks in favor of custom, deterministic orchestration.

* **The LLM Gateway:** A centralized choke-point for all outbound LLM inference calls. This architecture ensures complete control over API interactions, allowing for uniform security, routing, and monitoring.
* **Resilience & Circuit Breakers:** Integrated Redis-backed circuit breakers automatically halt requests to failing downstream LLM providers (triggering a 5-minute cooldown after 3 consecutive failures), ensuring system stability and preventing cascading failures.
* **Automatic Fallback Routing:** Primary inference is powered by the Groq API (Llama 3.3-70B). In the event of primary provider degradation or failure, the gateway automatically routes requests to Gemini 2.0 Flash, ensuring uninterrupted intelligence delivery.
* **Full Observability & Audit Logging:** Every LLM interaction is exhaustively logged to a PostgreSQL database, capturing token usage, request latency, and cost metrics for strict financial and operational auditing.
* **Dual Intelligence Engine:** Integrates a quantitative Machine Learning predictive pipeline (XGBoost/LightGBM) with a qualitative 4-Agent LLM narrative auditor (Trend, News, Filings, Arbitrator) to synthesize holistic market intelligence. *(Note: Scraping and ML models are scheduled for Phase 2).*
* **Custom Orchestration:** No black-box frameworks (like CrewAI or LangChain) are used. All LLM orchestration is implemented via custom Python code to guarantee enterprise-level determinism, debugging, and observability.

## Tech Stack
* **Backend Framework:** FastAPI (Fully Asynchronous)
* **Database:** PostgreSQL (Neon Cloud) with SQLAlchemy 2.0 and `asyncpg`
* **Migrations:** Alembic
* **Caching & State:** Redis (Upstash) via `redis.asyncio`
* **Task Queue:** Celery
* **Authentication:** Custom JWT dual-token (Access + Refresh) implementation with bcrypt password hashing
* **AI/LLM Providers:** Groq API (Primary: Llama 3.3-70B), Google Gemini API (Fallback: Gemini 2.0 Flash)

## Quickstart

### Prerequisites
* Python 3.11 or higher
* PostgreSQL instance
* Redis instance

### Installation
1. **Clone the repository and navigate to the backend directory:**
   *(Assuming you have cloned the repository)*
   ```bash
   cd backend
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv venv
   
   # On Windows:
   venv\Scripts\activate
   
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Configuration:**
   Create a `.env` file in the root directory and populate it with your specific credentials:
   ```env
   # Database connection string (must use asyncpg)
   DATABASE_URL=postgresql+asyncpg://user:password@host:port/dbname
   
   # Redis connection string (use rediss:// for secure connections like Upstash)
   REDIS_URL=rediss://default:password@host:port
   
   # Security
   SECRET_KEY=your_super_secret_key_here
   
   # LLM API Keys
   GROQ_API_KEY=your_groq_api_key_here
   ```

5. **Run the Application:**
   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```
   The API documentation (Swagger UI) will be available at `http://localhost:8000/docs`.

## API Capabilities
PSX Sentinel currently provides 22 enterprise-grade REST endpoints categorized across the following domains:

* **Authentication:**
  * `POST /register` - Register a new user
  * `POST /login` - Authenticate and receive JWT tokens
  * `POST /refresh` - Refresh access tokens
  * `GET /me` - Retrieve current authenticated user profile
* **Market Data:**
  * `GET /companies` - List tracked PSX companies
  * `GET /companies/{ticker}/prices` - Retrieve historical price data
  * `GET /companies/{ticker}/news` - Retrieve aggregated company news
  * `GET /market/summary` - Retrieve high-level market overview
* **Intelligence & Analytics:**
  * `GET /companies/{ticker}/report` - Generate comprehensive LLM analytical report
  * `GET /alerts` - Manage and retrieve user-defined market alerts
  * `GET /watchlist` - Manage user watchlists

---
### Disclaimer
*This project is a technical portfolio piece demonstrating enterprise-level software architecture, LLM orchestration, and backend engineering. It is **not** a registered financial advisor, and the insights, predictions, or reports generated by this platform do not constitute actual financial, investment, or trading advice. Use at your own risk.*