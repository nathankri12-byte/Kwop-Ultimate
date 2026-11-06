# Kwop Ultimate

An independent SaaS launch workspace inspired by publicly described product-launch workflows. It is not affiliated with Kwop and does not copy its proprietary code or branding.

## Available modules

- Supabase Auth registration, sign-in, sign-out and refreshable sessions
- Supabase Postgres cloud storage with row-level security
- Gemini SaaS idea generation, product briefs, viral-script writing, feature roadmaps, campaign drafts and launch assistant
- 30-day launch plan, profile preferences, project briefs and feature roadmap
- Creator outreach pipeline with tracked referral links
- Public product landing pages with pricing cards and consent-based aggregate page/CTA analytics
- Lead CRM with status and score fields
- Manual revenue ledger and business readiness score
- Email campaign drafts (no sending provider connected)
- Runnable starter-MVP ZIP export

## Run locally

1. Install Python 3.11+.
2. Install dependencies: `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and set a long random `SECRET_KEY`.
4. Set `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` from Supabase. Never use a service-role/secret key in the app.
5. Set `GEMINI_API_KEY` from Google AI Studio for AI tools.
6. Run `python app.py` and open http://localhost:5000.

## Deploy

Python web service:
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app`
- Required: `SECRET_KEY`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `COOKIE_SECURE=1`
- AI: `GEMINI_API_KEY` and optional `GEMINI_MODEL` (defaults to `gemini-3.8-flash`)

## Limitations (not represented as active integrations)

- No Stripe/Whop checkout, subscription billing, creator payouts or payment processing.
- No real email sending, automated social posting, Google Pages publishing or affiliate payout portal.
- Analytics covers consented aggregate page views and CTA clicks on launch pages; it is not full-fidelity session replay or visitor heatmaps.
- Video URLs are not automatically downloaded or watched; supply a transcript or description.
- Platform view counts and revenue entries are manual; they are not imported from external services.
- The generated ZIP is a runnable starter app, not a one-click production deployment.
- External payment and commercial accounts may require adult/guardian involvement and provider approval.
- Render free web services sleep after 15 minutes without inbound traffic and are intended for hobby/testing, not production.

## Privacy and security

Passwords are managed by Supabase Auth. User-owned data is protected by RLS. Public launch-page analytics are sent only after visitor consent and do not collect keystrokes or form contents. AI prompts are sent to Google Gemini when AI tools are used. Review applicable privacy and data-retention requirements before inviting external users.

## License

MIT
