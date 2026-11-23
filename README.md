# EstateFlow CRM - Production Backend API

High-performance FastAPI backend for the EstateFlow Real Estate CRM platform.

## Features
- **FastAPI + Async Motor / MongoDB Atlas**: High-throughput asynchronous queries.
- **Role-Based Access Control (RBAC)**: Super Admin, Admin, and Executive roles.
- **Inventory & Requirements Matching Engine**: Real-time matching between property listings and customer enquiries.
- **Daily Schedule & Follow-ups**: Automated cadence tracking, SLA alerts, and site visit scheduling.
- **Production URL**: `https://crm.thesspartners.com/api/v1`

## Setup & Running
1. Create virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Or .\venv\Scripts\activate on Windows
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy environment config:
   ```bash
   cp .env.example .env
   # Edit .env with your MongoDB Atlas connection string and production secrets
   ```
4. Start development server:
   ```bash
   uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

## Production Deployment
See [DEPLOYMENT.md](DEPLOYMENT.md) for full Nginx and systemd configuration on `crm.thesspartners.com`.
