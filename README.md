# GovRadar: website + collector

## 1. Database (Supabase)
SQL editor: run `schema.sql`, then `schema_collector.sql`. Auth > Providers > Email on.
Copy Project URL, anon key, and the service_role key (keep the last one secret).

## 2. Website (Netlify)
Edit the CFG line at the top of `index.html`: supabaseUrl, supabaseKey (anon). Drag this folder onto app.netlify.com/drop.
Site settings > Environment variables: SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY, GH_TOKEN, GH_REPO, GH_REF.
Sign up on the site, then in Supabase: update profiles set role='admin' where email='you@example.com';

## 3. Collector (GitHub)
Put the `govcollect/` folder in a GitHub repo (with .github/workflows/collect.yml). Repo secrets: SUPABASE_URL,
SUPABASE_SERVICE_KEY, GOV_CONTACT (an email address shown to site owners in the crawler's User-Agent).
It runs every 2 hours (high-volume sources) and daily (all hosts), or on demand from the website's Admin tab:
"Update all sources now" -> Netlify function -> GitHub Action -> crawl -> Supabase -> progress shown live.

Alternative to GitHub: run `GOV_ADMIN_TOKEN=... python -m govcollect serve` on your own server and set CFG.collectorUrl.
