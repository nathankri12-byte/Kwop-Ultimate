# Kwop Ultimate

An independent SaaS launch workspace inspired by publicly described product-launch workflows. Not affiliated with Kwop.

## Implemented

- Supabase Auth email/password registration, sign-in, sign-out and refreshable sessions
- Supabase Postgres storage with row-level security for user-owned data
- Gemini AI SaaS idea generation and detailed product briefs
- Gemini AI short-form video script analysis from a supplied transcript/description
- AI-generated prioritized feature roadmap
- AI-generated runnable Flask MVP ZIP export
- Creator outreach records, referral links and click/view metrics
- Cloud-synced 30-day checklist, preferences, feature roadmap and saved product briefs
- Dashboard metrics and responsive workspace

## Run locally

1. Install Python 3.11+.
2. Install dependencies: `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and set a long random `SECRET_KEY`.
4. Set `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` from the Supabase project. The publishable key is intended for app use; never put a Supabase secret/service-role key in this application.
5. Set `GEMINI_API_KEY` from Google AI Studio to enable real AI generation. Keep it server-side.
6. Run `python app.py` and open http://localhost:5000.

## Deploy

Use a Python web service with build command `pip install -r requirements.txt` and start command `gunicorn app:app`. Set these environment variables on the host:
- `SECRET_KEY`: long random secret
- `SUPABASE_URL`: project API URL
- `SUPABASE_PUBLISHABLE_KEY`: publishable key
- `GEMINI_API_KEY`: optional until AI features are used
- `GEMINI_MODEL`: optional model override, defaults to `gemini-3.8-flash`
- `COOKIE_SECURE=1`: secure cookies behind HTTPS

## Honest limitations

- Gemini free-tier quotas apply and may change. A valid API key is required for AI functions.
- Video links are not automatically downloaded or watched. Supply a transcript or description; the AI analyses that content only.
- Platform view counts are not available automatically and must be entered manually.
- The generated ZIP is a runnable starter application, not a one-click production deployment.
- Payment processing, creator payouts, custom domains, email marketing, and automated social publishing are not integrated. Payment-provider accounts and commercial activity may require an adult/guardian and provider approval.
- Render free web services may sleep when idle; first requests can be slow. Hosting providers can change free-tier limits.
- Review privacy, data retention, abuse prevention, and production security before inviting external users.

## Privacy and security

Passwords are managed by Supabase Auth. User-owned database rows are protected by RLS. AI prompts are sent to Google Gemini when AI features are used. Do not submit sensitive personal or payment information. Review Google's Gemini API data-use terms and Supabase project settings.

## License

MIT
