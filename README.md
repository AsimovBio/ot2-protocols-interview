# OT2 Workflows

This is a Flask app that serves Biocorp's OT2 workflows, which are generated from input parameters. The app has both an extremely basic web GUI and a JSON-based API. We may want to turn this into an actual REST api with persistent resources for stored protocols etc. later.

## Usage

### Simple

Go to xxx.biocorp.io to browse and download protocols.

### Advanced

Get protocols by calling the API endpoint for each protocol (e.g. xxx.biocorp.io/api/protocols/elisa). Send a POST request with the parameters for your protocol (documentation will go here once we have some actual protocols).


## Developer Notes

The process of dynamically generating OT2 workflows is complicated by the fact that their protocol files are not a proper data file format that can be easily manipulated. Instead, the OT2 app expects protocol files in the form of Python scripts. Rather than dynamically write Python scripts line by line, this package takes the slightly more sensible approach of saving Python protocol templates and then pasting in a parameters={"keys", "values"} variable at the top to generate the final protocol.

Therefore, all the files in the protocol templates folder require the opentrons package (installed on the robot) and a parameters variable pasted in at the top to actually run.

OpenTrons is working on a JSON file format which should improve the situation.
