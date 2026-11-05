# Vinted Searcher

Runs your saved Vinted (UK) searches every hour and shows all the results in one phone-friendly feed.

- **Free route:** GitHub Actions runs the open-source `vinted_scraper` library.
- **Fallback:** if Vinted blocks a search, it is re-run through Apify (only if an `APIFY_TOKEN` secret is set, and at most once every 2 hours so a long block can't drain your credit).
- **App:** `index.html` on GitHub Pages. Add, edit and pause searches on your phone; likes, hidden items and "read" state are kept on the phone.

App address once set up: https://jamescarpenter1989.github.io/VintedSearcher/

## Setup (about 15 minutes)

1. **Create the repo.** On GitHub, create a **public** repository called `VintedSearcher` (Pages and the free route need it public on a free account). Upload every file from this zip.
   - The `.github` folder is hidden on some computers. If it doesn't upload, use **Add file → Create new file**, type the name `.github/workflows/scrape.yml`, and paste in the contents of that file.
2. **Turn on Pages.** Repo **Settings → Pages → Build and deployment**: Source *Deploy from a branch*, Branch `main`, folder `/ (root)`, Save.
3. **Apify (fallback).** Sign up free at apify.com. In the Apify console go to **Settings → API & Integrations** and copy your API token. In the GitHub repo go to **Settings → Secrets and variables → Actions → New repository secret**, name it `APIFY_TOKEN` and paste the token.
4. **Phone token (for editing searches).** Go to github.com → your picture → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
   - Repository access: *Only select repositories* → `VintedSearcher`
   - Permissions: **Contents** read and write, **Actions** read and write
   - Copy the token.
5. **Open the app** on your phone at the address above. Tap the menu button → **Settings**, paste the token, Save, then **Test connection**.
6. In Chrome tap **⋮ → Add to Home screen**.
7. Add your searches (menu → **Searches → Add search**), then tap the refresh button to run them straight away.

## Adding a search

Best: set the search up on vinted.co.uk with all its filters (category, size, brand, condition), then copy the address bar link into **Vinted search link**. Or just type keywords.

- **Hide items whose title contains** – words that knock an item out (e.g. `kids, replica`).
- **Only keep items whose title contains one of** – optional must-have words.
- Results are always fetched newest first.

## Status screen

Shows each hourly run: whether each search used the free route, Apify, or failed, and why. If you see Apify being used every couple of hours, the free route is being blocked.

## Settings you can change

In `scraper/scrape.py` (top of file) or as environment variables in the workflow:

| Setting | Default | What it does |
|---|---|---|
| `APIFY_MIN_HOURS` | 2 | Minimum gap between Apify runs |
| `APIFY_ACTOR` | `louisdeconinck/vinted-scraper` | Which Apify actor to use |
| `PER_SEARCH` | 48 | Newest listings fetched per search per run |
| `KEEP_DAYS` | 14 | Drop items not seen for this long |

The schedule is in `.github/workflows/scrape.yml` (`17 * * * *` = every hour at :17).

## Notes

- Scraping Vinted is against its Terms of Service. Keep this personal and low volume.
- Apify is paid per use. Check usage at console.apify.com → Billing now and then.
- GitHub may delay hourly runs by a few minutes when it is busy.
