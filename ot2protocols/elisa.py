"""
Provides functionality related to generating ELISA protocols for the OT2.
"""


import json

from flask import Blueprint, request, Response, render_template, jsonify
from wtforms import Form, IntegerField, validators
from werkzeug.datastructures import MultiDict

from ot2protocols import utils, protocol

NAME = "elisa"

bp = Blueprint(NAME, __name__)


class ElisaForm(Form):
    num_samples = IntegerField("Number of Samples",
                               [validators.NumberRange(min=1, max=8)])


@bp.route(f"/protocols/{NAME}", methods=["GET", "POST"])
def view():
    """A simple UI for generating an ELISA protocol."""
    form = ElisaForm(request.form)
    if request.method == "POST" and form.validate():
        try:
            num_samples = form.num_samples.data
            protocol = ElisaProtocol(num_samples)
            f = NAME + ".ot2"
            headers = {"Content-disposition": f"attachment; filename={f}"}
            return Response(protocol.generate(), mimetype="text", headers=headers)
        except Exception as e:
            form.errors = {"protocol_generation": [str(e)]}
            return render_template("html/protocol_generator.html",
                                   title=ElisaProtocol.title,
                                   description=ElisaProtocol.description,
                                   instructions=ElisaProtocol.instructions,
                                   form_action=NAME,
                                   input_fields=[form.num_samples],
                                   errors=form.errors)
    else:
        input_fields = [
            form.num_samples
        ]
        return render_template("html/protocol_generator.html",
                               title=ElisaProtocol.title,
                               description=ElisaProtocol.description,
                               instructions=ElisaProtocol.instructions,
                               form_action=NAME,
                               input_fields=input_fields)


@bp.route(f"/api/protocols/{NAME}", methods=["POST"])
def api():
    """An API endpoint for generating an ELISA protocol."""
    form = ElisaForm(MultiDict(mapping=request.json))
    if form.validate():
        num_samples = form.num_samples.data
        protocol = ElisaProtocol(num_samples)
        return jsonify(protocol.to_dict())
    else:
        return Response(json.dumps({"errors": form.errors}),
                        mimetype="application/json", status=400)


class ElisaProtocol(protocol.Protocol):
    """
    An ELISA protocol for the OT2.
    General information about ELISAs is stored in class variables.
    """
    short_name = NAME
    robot_config = "highvol"
    title = "ELISA Protocol"
    description = "Generate an ELISA protocol for the OT2."
    instructions = """<ol>
    <li>Fill plates and buffer trough as described in the
 <a href=\"https://example.com/elisa_sop\">ELISA SOP</a>.</li>
    <li> Find a robot in the {0} configuration and set it up following the
 instructions in the <a href=\"
 https://example.com/ot2_sop
 \">OT2 SOP</a>.</li>
 </ol>""".format(robot_config)

    def __init__(self, num_samples):
        self.num_samples = num_samples

    def generate(self):
        """Return the OT2 protocol as a string."""
        template_name = f"protocols/{NAME}_template.ot2"
        config_name = f"protocols/config_{self.robot_config}.ot2"
        parameters = {"num_samples": self.num_samples}
        return utils.protocol_from_template(parameters, template_name,
                                            robot_config=config_name)

    def to_dict(self):
        """Return a dictionary representation of the protocol."""
        as_dict = {}
        as_dict['num_samples'] = self.num_samples
        as_dict['protocol_string'] = self.generate()
        return as_dict
