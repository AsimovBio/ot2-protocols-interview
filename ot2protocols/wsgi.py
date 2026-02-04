import logging
from ot2protocols import app, elisa

application = app.generate_app()
gunicorn_logger = logging.getLogger('gunicorn.error')
application.logger.handlers = gunicorn_logger.handlers
application.logger.addHandler(logging.StreamHandler())
application.logger.setLevel(logging.INFO)
