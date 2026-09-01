# Deployment Guide

## Railway (Recommended Free Tier)

1. Create account at https://railway.app
2. New Project ? Deploy from GitHub repo
3. Set environment variables:
   - `ENVIRONMENT=production`
   - `LLM_PROVIDER=mock`
   - `DATABASE_URL=your-neon-connection-string`
   - `API_KEY=your-secret-key`
4. Railway auto-detects `railway.toml`
5. Get URL: `https://your-app.up.railway.app`

## Render

1. Create account at https://render.com
2. New Web Service ? Connect GitHub
3. Use `render.yaml` blueprint
4. Set env vars in dashboard
5. Get URL: `https://your-app.onrender.app`

## Health Check
- `GET /health` returns 200 when healthy
- `GET /ready` checks DB connectivity

## API Documentation
- Swagger UI: `https://your-url/docs`
- ReDoc: `https://your-url/redoc`