"""
These are convenience functions for use in the protocol generators (not the
final protocol files).
"""


import json

from flask import render_template


def protocol_from_template(parameters, protocol_template, robot_config=None,
                           include_utils=True):
    """
    Write a protocol file from the parameters and template file given.
    parameters should be a Python dictionary, which must be serializable to a string.
    The parameters will be serialized to a string and pasted into the output file; the
    parameters will be deserialized back to a Python dict when the protocol is run
    on the robot. The template file with the given name will be located and also
    written into the output file.
    Note that the resulting protocol file is NOT safe if the source of the parameters
    is unsafe; however, the application itself (and our software infrastructure)
    remains safe from attack because the code generated from user parameters is never
    actually run by the application (just provided back to the user).
    """
    parameter_str = str(parameters)

    return render_template('protocols/generic_protocol.ot2',
                           parameter_str=parameter_str,
                           parameters=parameters,
                           protocol_template=protocol_template,
                           robot_config=robot_config,
                           include_utils=include_utils)
