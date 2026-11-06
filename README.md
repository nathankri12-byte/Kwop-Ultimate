# Kwop Ultimate

An independent SaaS launch workspace inspired by publicly described product-launch workflows. Not affiliated with Kwop.

## Current working features

- Account registration and login with password hashing
- Gemini-powered SaaS idea generation and product specifications
- Gemini-powered short-form video script adaptation from a transcript or user-provided description
- AI-generated runnable Flask MVP project bundle as a ZIP
- Creator database with unique redirect tracking links and click counts
- Local 30-day launch checklist and feature roadmap in the browser
- Dashboard metrics and a local-first responsive interface

## Run locally

1. Install Python 3.11+.
2. Run `pip install -r requirements.txt`.
3. Set a strong `SECRET_KEY`.
4. Set `GEMINI_API_KEY` from Google AI Studio. The API key must stay on the server and must never be committed to GitHub.
5. Optional: set `GEMINI_MODEL` (default: `gemini-3.8-flash`).
6. Run `python app.py` and open http://localhost:5000.

The Gemini API currently offers a free tier for selected models, subject to quotas and terms. Free-tier availability and limits can change. See the official [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing).

## Deployment

The backend requires a persistent database and secure environment variables. The current build uses SQLite for local development. Do not deploy this version to an ephemeral filesystem and assume accounts will persist: the database would be lost on restarts/redeploys. A managed PostgreSQL/Supabase adapter and deployment configuration are the next infrastructure step.

Required server environment:
- `SECRET_KEY`: long random secret
- `GEMINI_API_KEY`: server-side Gemini API key
- `GEMINI_MODEL`: optional model override
- `DATABASE_PATH`: optional path to persistent SQLite file for a single-instance host
- `COOKIE_SECURE=1`: enable secure session cookies behind HTTPS

## Important limitations

- Video URL alone is not automatically fetched or watched yet. The AI analyses a transcript or description entered by the user; automatic video ingestion is a separate integration.
- Creator link clicks are tracked by Kwop Ultimate. External-platform views are not automatically available; they are entered manually.
- Generated project ZIPs are real starter applications, but one-click hosting/deployment and production payment processing are not connected yet.
- The 30-day checklist and roadmap currently use this browser's local storage. Cloud sync for these parts is still to be added.
- The current database is SQLite. A persistent production database and backups are required before public launch.
- This is an early build. Security review, abuse protection, privacy controls, tests, and provider setup are still required before real customers should use it.

## Privacy

Account passwords are stored as password hashes. AI prompts are sent to Google Gemini when AI features are used. Do not enter sensitive personal or payment data. Review Google's Gemini API data-use terms before using it with other people's information.

## License

MIT
