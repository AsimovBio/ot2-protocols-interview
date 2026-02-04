"""
The main module.
Sets up the app and imports protocols as blueprints from their respective
modules.
"""


import json
import os

from flask import Flask, render_template, Response

from ot2protocols import elisa, labware, calibrate, sanger


PROTOCOL_CLASSES = [
    elisa.ElisaProtocol,
    sanger.SangerProtocol,
]


def main():
    """Create and run the flask app."""
    app = generate_app()
    app.run(host='0.0.0.0', port=5000)


def generate_app():
    """Create and return the flask app."""
    app = Flask(__name__)
    app.register_blueprint(elisa.bp, url_prefix="/")
    app.register_blueprint(labware.bp, url_prefix="/")
    app.register_blueprint(calibrate.bp, url_prefix="/")
    app.register_blueprint(sanger.bp, url_prefix="/")

    @app.route("/health", methods=['GET'])
    def health():
        return Response(status=200)

    @app.route('/')
    def index():
        """Return main page displaying links to protocol pages."""
        return render_template('html/index.html', protocols=PROTOCOL_CLASSES)

    return app


if __name__ == '__main__':
    main()
