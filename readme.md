# AI Pipeline — Architecture & Engineering Hub

This repository contains the overarching AI Pipeline system primarily integrating `ai-service`, heavily powered by LangGraph and FastAPI. 

## Quick Start
1. Add `.env` file to `ai-service/` mapped from `.env.example`.
2. Run `docker-compose up -d --build`.
3. Application accessible at `http://localhost:8000/docs` (Swagger UI).
4. For visual debugging, see [LangGraph Studio Guide](ai-service/LANGGRAPH_STUDIO.md) or [Studio CLI Commands](ai-service/STUDIO_CLI.md).

## Structure
- `ai-service/`: FastAPI + LangGraph microservice doing the heavy lifting.
- `docs/`: Contracts and Architecture Decision Records (ADRs).
