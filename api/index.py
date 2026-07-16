from vercel_wsgi import make_handler
from app import app as flask_app

# `handler` is the entrypoint Vercel looks for.
handler = make_handler(flask_app)
