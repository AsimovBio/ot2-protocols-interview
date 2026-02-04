"""
Provides functionality related to downloading calibration protocols for the OT2.
"""
import json

from flask import Blueprint, request, Response, render_template

from ot2protocols import utils, protocol


CONFIG_HIGHVOL = "highvol"
CONFIG_LOWVOL = "lowvol"

bp = Blueprint("calibrate", __name__)


@bp.route("/protocols/calibrate", methods=["GET"])
def view():
    """A simple UI for downloading calibration protocols."""
    calibrations = [
        Calibration(CONFIG_HIGHVOL, "96_PCR_flat"),
        Calibration(CONFIG_HIGHVOL, "tube_rack_48_cold_block"),
        Calibration(CONFIG_HIGHVOL, "384_plate"),
        Calibration(CONFIG_HIGHVOL, "tiprack_200ul"),
        Calibration(CONFIG_HIGHVOL, "96_flat"),
        Calibration(CONFIG_HIGHVOL, "pipettes"),
        Calibration(CONFIG_LOWVOL, "96_PCR_flat"),
        Calibration(CONFIG_LOWVOL, "tube_rack_48_cold_block"),
        Calibration(CONFIG_LOWVOL, "384_plate"),
        Calibration(CONFIG_LOWVOL, "tiprack_200ul"),
        Calibration(CONFIG_LOWVOL, "tiprack_10ul"),
        Calibration(CONFIG_LOWVOL, "96_flat"),
        Calibration(CONFIG_LOWVOL, "pipettes")
    ]
    return render_template('html/calibrate.html',
                           calibrations=calibrations)


@bp.route("/protocols/calibrate", methods=["POST"])
def form_submission():
    """Form post endpoints for calibration protocols."""
    try:
        protocol = Calibration(request.form["config_name"],
                               request.form["item_name"])
        f = protocol.name + ".ot2"
        headers = {"Content-disposition": f"attachment; filename={f}"}
        return Response(protocol.generate(), mimetype="text", headers=headers)
    except Exception as e:
        return Response(f"Error generating calibration protocol: {str(e)}",
                        mimetype="text/plain", status=400)


class Calibration(protocol.Protocol):
    """
    A calibration protocol for the OT2. Currently these are generated
    dynamically rather than stored as persistent resources.
    """
    def __init__(self, config_name, item_name):
        self.config_name = config_name
        self.item_name = item_name
        self.form_action = "calibrate"
        self.name = config_name + "_" + item_name

    def generate(self):
        """Return the OT2 protocol as a string."""
        template_path = f"protocols/calibrate/cal_{self.item_name}.ot2"
        config_path = f"protocols/config_{self.config_name}.ot2"
        parameters = {}
        return utils.protocol_from_template(parameters, template_path,
                                            robot_config=config_path)

    def to_dict(self):
        """Return a dictionary representation of the protocol."""
        as_dict = {}
        as_dict['protocol_string'] = self.generate()
        return as_dict

