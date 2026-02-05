**NOTE: THIS IS A PUBLIC REPO**

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

## Sanger Sequencing Workflow

This workflow generates four serial dilutions (1×, 2×, 4×, 8×) of each plasmid sample, then pauses for the NanoDrop so you can inspect each well. Once you select the dilution that measured ≥100 ng/µL, you record that NanoDrop concentration as the “selected” value and the dilution factor used. The OT-2 then transfers 10 µL of that winning dilution into the final tube strip (target mass = 1000 ng). The API response includes:

- `pai_csv`: lists each sample, its sequence, the winning dilution, selected concentration, and final volume/mass.
- `order`: the GeneWiz payload plus best dilution metadata (still disabled unless you flip `GENEWIZ_ENABLED=true`).

The UI exposes the same fields plus a `PAI sequences` block (Sample:Sequence per line). Leave it empty to let Benchling populate sequences once you enable that integration (testing key: `j6da2A0lpn-4wC01bD`). The manual NanoDrop step is apparent in the generated protocol comments, so the lab operator will physically measure each dilution, enter the winning concentration, and then proceed with the final 10 µL transfer.

## GeneWiz & Benchling integrations

To keep your sequencing orders consistent, the Sanger API can optionally emit and submit payloads to GeneWiz. Enable it with:

- `GENEWIZ_ENABLED` – set to `true` to activate the service (default: `false`).
- `GENEWIZ_API_KEY` – **required** when the feature is enabled; keep it secret.
- `GENEWIZ_API_URL` – optional override (handy for staging).
- `GENEWIZ_TIMEOUT` – optional request timeout in seconds (default: `10`).

You can also fetch PAI sequences from Benchling when you leave the manual `Sample:Sequence` list empty:

- `BENCHLING_ENABLED` – set `true` to attempt Benchling lookups.
- `BENCHLING_API_KEY` – API key used by the placeholder client (we can use `j6da2A0lpn-4wC01bD` for testing, but swap it out in production).
- `BENCHLING_API_URL` – optional base URL override.
- `BENCHLING_TIMEOUT` – optional request timeout.

The integration package includes a Benchling helper that only returns sequences when the feature flag is on and the key is present. The Sanger API always responds with a `pai_csv` column that lists each sample and its derived sequence (manual input preferred; Benchling is a backup). All external requests run inside `requests` with timeouts, and responses or errors are shaved into structured JSON so the frontend can handle them safely.
