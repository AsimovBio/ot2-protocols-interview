"""
Loads all custom labware models onto a robot.
"""


import json

from flask import Blueprint, request, Response, render_template, jsonify
from wtforms import Form, IntegerField, validators
from werkzeug.datastructures import MultiDict

from ot2protocols import utils, protocol

NAME = "labware"

bp = Blueprint(NAME, __name__)


@bp.route(f"/protocols/{NAME}", methods=["GET", "POST"])
def view():
    """A simple UI for downloading the labware creation protocol."""
    if request.method == "POST":
        try:
            protocol = LabwareProtocol()
            f = NAME + ".ot2"
            headers = {"Content-disposition": f"attachment; filename={f}"}
            return Response(protocol.generate(), mimetype="text", headers=headers)
        except Exception as e:
            return render_template('html/protocol_generator.html',
                                   title=LabwareProtocol.title,
                                   description=LabwareProtocol.description,
                                   instructions=LabwareProtocol.instructions,
                                   form_action=NAME,
                                   input_fields=[],
                                   errors={"protocol": [str(e)]})
    else:
        return render_template('html/protocol_generator.html',
                               title=LabwareProtocol.title,
                               description=LabwareProtocol.description,
                               instructions=LabwareProtocol.instructions,
                               form_action=NAME,
                               input_fields=[])


@bp.route(f"/api/protocols/{NAME}", methods=["POST"])
def api():
    """An API endpoint for downloading the calibration protocol."""
    try:
        protocol = LabwareProtocol()
        return jsonify(protocol.to_dict())
    except Exception as e:
        return Response(json.dumps({"errors": {"protocol": [str(e)]}}),
                        mimetype="application/json", status=400)


class LabwareProtocol(protocol.Protocol):
    """
    A protocol that generates all custom-defined labware.
    """
    short_name = NAME
    title = "Labware Creation Protocol"
    description = """Run this to load all Biocorp custom labware definitions
on a robot (if a robot is new or new labware has been added)."""
    instructions = "Simply download and run on the robot (no calibration required)."

    def __init__(self):
        pass

    def generate(self):
        """Return the OT2 protocol as a string."""
        template_name = f"protocols/{NAME}_template.ot2"
        parameters = {}
        return utils.protocol_from_template(parameters, template_name,
                                            include_utils=False)

    def to_dict(self):
        """Return a dictionary representation of the protocol."""
        as_dict = {}
        as_dict['protocol_string'] = self.generate()
        return as_dict
