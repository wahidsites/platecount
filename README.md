# PlateCount

PlateCount is a Flask + SQLite food logging app. The frontend is served by Flask; do not deploy it as a GitHub Pages-only site.

## Included files

- `index.html` — app interface
- `app.py` — Flask backend and API routes
- `requirements.txt` — Python dependencies
- `Procfile` — production start command for compatible hosts
- `.env.example` — example environment variables

The SQLite database (`platecount.db`) is created automatically on first start.

## Run locally on macOS / Linux

1. Install Python 3.10 or newer.
2. Open Terminal and move into this folder:

   ```bash
   cd /path/to/PlateCount
   ```

3. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

4. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

5. Start the app:

   ```bash
   python app.py
   ```

6. Open http://127.0.0.1:5000 in your browser.

## Enable AI meal estimates

The app's login, manual food logging, history and profile features work without a Gemini key. AI photo/description analysis requires a Gemini API key.

Set it before starting the server:

```bash
export GEMINI_API_KEY="your_real_key_here"
python app.py
```

On Windows PowerShell:

```powershell
$env:GEMINI_API_KEY="your_real_key_here"
python app.py
```

Alternatively, create a local `gemini_key.txt` containing only the key. That file is ignored by Git.

## Deploy

Deploy this folder to a Python-capable host that supports Flask/Gunicorn. Set the `GEMINI_API_KEY` environment variable in the host's dashboard. The start command is:

```bash
gunicorn app:app
```

**Important:** GitHub Pages cannot run `app.py`. Also check whether your hosting provider offers persistent storage: SQLite data can be lost on hosts with ephemeral filesystems unless a persistent disk/volume is configured.

## Notes

- Keep `GEMINI_API_KEY` private. Do not put it in `index.html`, commit it to GitHub, or share it publicly.
- The AI output is an estimate, not a medical measurement. Check and correct the values before saving.
- This package preserves the supplied app's existing calorie-target calculation. It is not a medically validated nutrition plan.
