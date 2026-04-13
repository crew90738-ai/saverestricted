# Procfile — used by Render (Web Service) and Heroku-compatible platforms.
#
# We run a single Python process that:
#   • Binds an HTTP health-check endpoint on $PORT (required by Render)
#   • Runs both Pyrogram clients (bot + user session)
#
# No gunicorn / uvicorn needed — main.py manages its own async loop.

web: python main.py
