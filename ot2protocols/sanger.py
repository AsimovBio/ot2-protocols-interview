"""Sanger sequencing workflow + external integrations.

Generates OT-2 protocols for preparing samples for Sanger sequencing:
1. Creates serial dilution series (1x, 2x, 4x, 8x) of each sample
2. Pauses for NanoDrop concentration measurement
3. Transfers the optimal dilution (10 µL) to output tubes
4. Optionally queries Benchling for sample sequences
5. Optionally submits order to GeneWiz

Key classes:
  - SangerForm: WTForms form for parameter input with validation
  - SangerParams: Dataclass holding validated parameters
  - SangerProtocol: Generates OT-2 protocol script
  - SangerSubmissionBuilder: Orchestrates submission with dependency injection

Key functions:
  - parse_factors: Parse and validate comma-separated dilution factors
  - _normalize_sample_ids: Generate or pad sample ID list with validation
  - build_pai_csv: Generate properly escaped CSV for sequencing order

Environment variables (optional):
  - BENCHLING_ENABLED: 'true' to fetch sequences from Benchling
  - GENEWIZ_ENABLED: 'true' to submit orders to GeneWiz
"""

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import Dict, List, Optional, Sequence, Tuple

from flask import Blueprint, Response, jsonify, render_template, request
from wtforms import Form, DecimalField, IntegerField, StringField, ValidationError, validators
from werkzeug.datastructures import MultiDict

from ot2protocols import protocol, utils
from ot2protocols.integrations import BenchlingClient, BenchlingError, GeneWizClient, GeneWizError

NAME = 'sanger'
FINAL_VOLUME_UL = 10
TARGET_MASS_NG = 1000
bp = Blueprint(NAME, __name__)


def _is_feature_enabled(env_var: str) -> bool:
    """Check if a feature is enabled via environment variable.

    Args:
        env_var: Environment variable name to check

    Returns:
        True if env var is set to '1', 'true', or 'yes' (case-insensitive)
    """
    return os.environ.get(env_var, 'false').lower() in ('1', 'true', 'yes')


@dataclass
class SangerParams:
    """Type-safe protocol parameters for Sanger sequencing.

    Attributes:
        num_samples: Number of samples to process (1-12)
        base_volume: Volume per well in dilution series (µL)
        dilution_factors: List of dilution factors (e.g., [1.0, 2.0, 4.0, 8.0])
        sample_ids: List of sample identifiers
        best_dilution: Selected dilution factor after NanoDrop (None if not yet selected)
        best_concentration: NanoDrop concentration for selected dilution (ng/µL, None if not measured)
        pai_text: Manual PAI sequences in "Sample:Sequence" format
    """
    num_samples: int
    base_volume: float
    dilution_factors: List[float]
    sample_ids: List[str]
    best_dilution: Optional[float]
    best_concentration: Optional[float]
    pai_text: str

    @property
    def selected_samples(self) -> List[str]:
        """Return the subset of sample IDs matching num_samples."""
        return self.sample_ids[:self.num_samples]


class SangerForm(Form):
    """Form for Sanger protocol parameter input with validation."""

    num_samples = IntegerField(
        'Sample count',
        [validators.NumberRange(min=1, max=12)],
        default=4
    )
    base_volume = DecimalField(
        'Final per-well volume (µL)',
        [validators.NumberRange(min=20, max=300)],  # P300 pipette range
        default=100
    )
    dilution_factors = StringField(
        'Dilution factors (comma separated)',
        default='1,2,4,8'
    )
    sample_ids = StringField('Sample IDs (comma separated)', default='')
    pai_sequences = StringField(
        'PAI sequences (Sample:Sequence per line)',
        default=''
    )
    best_dilution = DecimalField(
        'Selected dilution factor (leave blank until after NanoDrop)',
        [validators.Optional(), validators.NumberRange(min=1)],
        default=None
    )
    best_concentration = DecimalField(
        'NanoDrop concentration (ng/µL) for selected dilution (optional)',
        [validators.Optional(), validators.NumberRange(min=0)],
        default=None
    )

    def validate_base_volume(form, field):
        """Validate base_volume is compatible with P300 pipette range."""
        if field.data:
            if field.data < 20:
                raise ValidationError('Minimum 20 µL (P300 lower limit)')
            if field.data > 300:
                raise ValidationError('Maximum 300 µL (P300 upper limit)')

    def validate_dilution_factors(form, field):
        """Validate dilution factors don't exceed plate row limit."""
        try:
            factors = parse_factors(field.data)
            if len(factors) > 8:
                raise ValidationError('Maximum 8 dilution factors (96-well plate row limit)')
        except ValueError as exc:
            raise ValidationError(str(exc))

    def validate_best_dilution(form, field):
        """Ensure best_dilution matches a provided factor."""
        if field.data is None:
            return  # Optional field
        try:
            factors = parse_factors(form.dilution_factors.data)
            if field.data not in factors:
                raise ValidationError(f'Must be one of: {factors}')
        except ValueError:
            # Let dilution_factors validation catch the error
            pass


class SangerProtocol(protocol.Protocol):
    """Generates OT-2 protocol for Sanger sequencing preparation."""

    short_name = NAME
    title = 'Sanger Sequencing Prep'
    description = 'Generate serial dilutions and NanoDrop prep for Sanger requests.'
    instructions = (
        'Generate four dilution tiers per sample, pause for NanoDrop, then transfer the winning dilution into final tubes.'
    )

    def __init__(self, params: SangerParams):
        """Initialize with validated parameters.

        Args:
            params: SangerParams dataclass with all protocol settings

        Raises:
            ValueError: If parameters are invalid or inconsistent
        """
        self._validate_params(params)
        self.params = params

    def _validate_params(self, params: SangerParams) -> None:
        """Validate parameter consistency before use.

        Checks:
        - best_dilution is in dilution_factors (if provided)
        - best_concentration >= 0 (if provided)
        - num_samples <= len(sample_ids)

        Raises:
            ValueError: On validation failure
        """
        if params.best_dilution is not None and params.best_dilution not in params.dilution_factors:
            raise ValueError(
                f'Best dilution {params.best_dilution} not in provided factors '
                f'{params.dilution_factors}'
            )

        if params.best_concentration is not None and params.best_concentration < 0:
            raise ValueError('Best concentration cannot be negative')

        if params.num_samples > len(params.sample_ids):
            raise ValueError(
                f'Requested {params.num_samples} samples but only '
                f'{len(params.sample_ids)} sample IDs provided'
            )

    def generate(self) -> str:
        """Generate OT-2 protocol script.

        Returns:
            Python protocol string ready for execution on OT-2

        Raises:
            ValueError: If template parameters are invalid
        """
        parameters = self._build_template_parameters()
        return utils.protocol_from_template(
            parameters,
            'protocols/sanger_template.ot2',
            robot_config='protocols/config_highvol.ot2',
        )

    def _build_template_parameters(self) -> Dict[str, object]:
        """Build and validate template parameters.

        Returns:
            Dict ready for Jinja2 template rendering with all required fields
        """
        return {
            'num_samples': self.params.num_samples,
            'base_volume': self.params.base_volume,
            'dilution_factors': self.params.dilution_factors,
            'sample_ids': self.params.selected_samples,
            'best_dilution': self.params.best_dilution,
            'best_concentration': self.params.best_concentration,
        }

    def to_dict(self) -> Dict[str, object]:
        """Export protocol configuration as dictionary.

        Returns:
            Dict representation of protocol parameters
        """
        return self._build_template_parameters()


class SangerSubmissionBuilder:
    """Encapsulates submission logic with dependency injection for testing.

    Allows testing each component independently:
    - Sequence fetching (Benchling integration)
    - Protocol generation
    - CSV generation
    - Order submission (GeneWiz integration)

    Clients can inject mock Benchling/GeneWiz clients for unit testing.
    """

    def __init__(
        self,
        params: SangerParams,
        benchling_client: Optional[BenchlingClient] = None,
        genewiz_client: Optional[GeneWizClient] = None,
    ):
        """Initialize with optional injected clients for testing.

        Args:
            params: Protocol parameters
            benchling_client: Mock/real Benchling client (None = use default)
            genewiz_client: Mock/real GeneWiz client (None = use default)
        """
        self.params = params
        self._benchling_client = benchling_client
        self._genewiz_client = genewiz_client

    def get_benchling_client(self) -> Optional[BenchlingClient]:
        """Get Benchling client, falling back to default if not injected.

        Returns:
            BenchlingClient instance or None if disabled/unavailable
        """
        if self._benchling_client is not None:
            return self._benchling_client

        if not _is_feature_enabled('BENCHLING_ENABLED'):
            return None

        try:
            return BenchlingClient()
        except BenchlingError:
            return None

    def get_genewiz_client(self) -> Optional[GeneWizClient]:
        """Get GeneWiz client, falling back to default if not injected.

        Returns:
            GeneWizClient instance or None if disabled/unavailable
        """
        if self._genewiz_client is not None:
            return self._genewiz_client

        if not _is_feature_enabled('GENEWIZ_ENABLED'):
            return None

        try:
            return GeneWizClient()
        except GeneWizError:
            return None

    def build_sequence_map(self) -> Dict[str, str]:
        """Build sample -> sequence mapping from manual + Benchling sources.

        Manual sequences take precedence over Benchling lookups.

        Returns:
            Dict mapping sample names to sequences (tolerates Benchling errors)
        """
        manual = parse_pai_sequences(self.params.pai_text)
        benchling_data = {}

        benchling_client = self.get_benchling_client()
        if benchling_client:
            benchling_data = self._fetch_benchling_sequences(benchling_client)

        # Manual entries override Benchling
        result = {}
        for sample in self.params.selected_samples:
            result[sample] = manual.get(sample) or benchling_data.get(sample, '')

        return result

    def _fetch_benchling_sequences(self, client: BenchlingClient) -> Dict[str, str]:
        """Fetch sequences from Benchling, silently ignoring errors.

        Args:
            client: BenchlingClient instance

        Returns:
            Dict of successfully fetched sequences (empty dict on any error)
        """
        sequences = {}
        for sample in self.params.selected_samples:
            try:
                seq = client.fetch_sequence(sample)
                if seq:
                    sequences[sample] = seq
            except BenchlingError:
                # Continue on any error - Benchling is optional
                continue
        return sequences

    def build_protocol(self) -> str:
        """Generate OT-2 protocol script.

        Returns:
            Python protocol string ready for execution on OT-2

        Raises:
            ValueError: If protocol parameters are invalid
        """
        protocol = SangerProtocol(self.params)
        return protocol.generate()

    def build_complete_submission(self) -> Tuple[str, str, Dict[str, object]]:
        """Generate protocol, CSV, and attempt order submission.

        Returns:
            Tuple of (protocol_script, pai_csv, order_status_dict)

        Raises:
            ValueError: If CSV generation fails (e.g., missing concentration)
        """
        # Get sequence data (tolerates Benchling failures)
        sequence_map = self.build_sequence_map()

        # Generate protocol (strict validation here is OK - these are our parameters)
        protocol_script = self.build_protocol()

        # Generate CSV (may raise if data incomplete)
        pai_csv = build_pai_csv(self.params, sequence_map)

        # Build and submit order (tolerates GeneWiz failures)
        payload = build_order_payload(self.params, sequence_map, pai_csv)
        genewiz_client = self.get_genewiz_client()
        order_status = self._submit_order(genewiz_client, payload)

        return protocol_script, pai_csv, order_status

    def _submit_order(
        self,
        genewiz_client: Optional[GeneWizClient],
        payload: Dict[str, object]
    ) -> Dict[str, object]:
        """Attempt order submission with structured response.

        Args:
            genewiz_client: GeneWizClient instance or None if disabled
            payload: Order payload to submit

        Returns:
            Dict with 'status' key indicating result (submitted/failed/disabled)
        """
        if genewiz_client is None:
            return {'status': 'disabled', 'message': 'GeneWiz not enabled'}

        try:
            response = genewiz_client.place_order(payload)
            return {
                'status': 'submitted',
                'response': response,
                'timestamp': datetime.now().isoformat(),
            }
        except GeneWizError as exc:
            return {
                'status': 'failed',
                'error': str(exc),
                'type': 'genewiz_error',
            }


def parse_factors(raw: str) -> List[float]:
    """Parse and validate dilution factors from comma-separated string.

    Args:
        raw: Comma-separated factors (e.g., '1,2,4,8')

    Returns:
        List of float dilution factors

    Raises:
        ValueError: If fewer than 4 factors, non-numeric values, non-positive values,
                   or more than 8 factors (plate row limit)

    Examples:
        >>> parse_factors('1,2,4,8')
        [1.0, 2.0, 4.0, 8.0]

        >>> parse_factors('1')
        ValueError: Provide at least four dilution factors
    """
    tokens = [token.strip() for token in raw.split(',') if token.strip()]

    if len(tokens) < 4:
        raise ValueError('Provide at least four dilution factors (e.g. 1,2,4,8).')

    if len(tokens) > 8:
        raise ValueError('Maximum 8 dilution factors (96-well plate row limit).')

    values = []
    for token in tokens:
        try:
            value = float(token)
        except ValueError as exc:
            raise ValueError(f'Invalid dilution factor "{token}".') from exc

        if value <= 0:
            raise ValueError('Dilution factors must be positive.')

        # Check for NaN and Infinity
        if not (-1e308 < value < 1e308):
            raise ValueError(f'Dilution factor "{value}" is out of range.')

        values.append(value)

    return values


def _normalize_sample_ids(raw: str, min_count: int) -> List[str]:
    """Generate sample IDs from comma-separated input or use defaults.

    Args:
        raw: Comma-separated sample ID string (can be empty)
        min_count: Minimum number of IDs to generate

    Returns:
        List of sample IDs (exactly min_count elements)

    Raises:
        ValueError: If provided IDs contain empty values or exceed length limits

    Examples:
        >>> _normalize_sample_ids('Sample1,Sample2', 2)
        ['Sample1', 'Sample2']

        >>> _normalize_sample_ids('', 3)
        ['Sample1', 'Sample2', 'Sample3']

        >>> _normalize_sample_ids('A,,C', 3)
        ValueError: Sample IDs cannot be empty
    """
    if not raw or not raw.strip():
        # Generate defaults only if input is truly empty
        return [f'Sample{i + 1}' for i in range(min_count)]

    ids = []
    for sid in raw.split(','):
        sid = sid.strip()
        if not sid:
            raise ValueError('Sample IDs cannot be empty (found comma with no text)')
        if len(sid) > 50:
            raise ValueError(f'Sample ID "{sid[:20]}..." exceeds 50 character limit')
        ids.append(sid)

    # Pad if provided fewer than min_count
    while len(ids) < min_count:
        ids.append(f'Sample{len(ids) + 1}')

    # Only use first min_count
    return ids[:min_count]


def _validate_best_dilution(best: float, factors: Sequence[float]) -> None:
    """Validate that best_dilution is in the provided factors list.

    Args:
        best: The selected best dilution factor
        factors: List of available dilution factors

    Raises:
        ValueError: If best is not in factors
    """
    if best not in factors:
        raise ValueError(f'Selected dilution {best} must be one of the provided factors: {list(factors)}')


def parse_pai_sequences(raw: str) -> Dict[str, str]:
    """Parse manually provided PAI sequences in "Sample:Sequence" format.

    Args:
        raw: Multi-line string with format "Sample:Sequence" per line

    Returns:
        Dict mapping sample names to sequences (empty dict if no valid lines)

    Examples:
        >>> parse_pai_sequences('Sample1:ATCG\\nSample2:GGCC')
        {'Sample1': 'ATCG', 'Sample2': 'GGCC'}
    """
    entries = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or ':' not in line:
            continue
        sample, sequence = line.split(':', 1)
        entries[sample.strip()] = sequence.strip()
    return entries


def build_pai_csv(params: SangerParams, sequence_map: Dict[str, str]) -> str:
    """Generate properly escaped CSV output from protocol parameters.

    Args:
        params: Protocol parameters including concentration and volume
        sequence_map: Sample name -> sequence mapping

    Returns:
        CSV string with header and sample rows (properly escaped for special characters)

    Raises:
        ValueError: If best_concentration required for calculation but not provided

    Examples:
        >>> params = SangerParams(..., best_concentration=100.0, ...)
        >>> csv_output = build_pai_csv(params, {'SampleA': 'ATCG'})
        >>> 'SampleA,ATCG' in csv_output
        True
    """
    # Validate that concentration is available for final mass calculation
    if params.best_concentration is None and params.best_dilution is not None:
        raise ValueError(
            'NanoDrop concentration required to generate final CSV. '
            'Complete measurements before exporting.'
        )

    # Calculate final mass if concentration available
    final_mass = (params.best_concentration * FINAL_VOLUME_UL
                  if params.best_concentration else None)

    # Use csv module for proper escaping
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=['Sample', 'Sequence', 'Best Dilution',
                   'Selected Concentration (ng/µL)', 'Final Volume (µL)', 'Final Mass (ng)'],
        restval='N/A'
    )
    writer.writeheader()

    for sample in params.selected_samples:
        writer.writerow({
            'Sample': sample,
            'Sequence': sequence_map.get(sample, ''),
            'Best Dilution': params.best_dilution or 'N/A',
            'Selected Concentration (ng/µL)': params.best_concentration or 'N/A',
            'Final Volume (µL)': FINAL_VOLUME_UL,
            'Final Mass (ng)': final_mass if final_mass else 'N/A',
        })

    return output.getvalue()


def build_order_payload(
    params: SangerParams,
    sequence_map: Dict[str, str],
    pai_csv: str,
) -> Dict[str, object]:
    """Build order payload for GeneWiz submission.

    Args:
        params: Protocol parameters
        sequence_map: Sample name -> sequence mapping
        pai_csv: CSV string for order

    Returns:
        Dict with order details for GeneWiz API
    """
    return {
        'service': 'Sanger Sequencing',
        'samples': [
            {
                'name': sample,
                'dilution_factors': params.dilution_factors,
                'pai_sequence': sequence_map.get(sample, ''),
            }
            for sample in params.selected_samples
        ],
        'instructions': f"{len(params.selected_samples)} samples prepared at {params.best_concentration} ng/µL",
        'pai_csv': pai_csv,
        'best_dilution': params.best_dilution,
        'best_concentration': params.best_concentration,
        'final_volume_ul': FINAL_VOLUME_UL,
        'final_mass_ng': TARGET_MASS_NG,
    }


def _input_fields(form: SangerForm) -> List:
    """Extract form fields for rendering in template.

    Args:
        form: SangerForm instance

    Returns:
        List of form fields to display
    """
    return [
        form.num_samples,
        form.base_volume,
        form.dilution_factors,
        form.sample_ids,
        form.pai_sequences,
        form.best_dilution,
        form.best_concentration,
    ]


def _build_params(form: SangerForm) -> SangerParams:
    """Convert validated form data to SangerParams dataclass.

    Args:
        form: Validated SangerForm instance

    Returns:
        SangerParams with all parameters

    Raises:
        ValueError: If parameter validation fails (cross-field checks)
    """
    factors = parse_factors(form.dilution_factors.data)
    best_dilution = float(form.best_dilution.data) if form.best_dilution.data else None
    best_concentration = float(form.best_concentration.data) if form.best_concentration.data else None

    if best_dilution is not None:
        _validate_best_dilution(best_dilution, factors)

    sample_ids = _normalize_sample_ids(form.sample_ids.data, form.num_samples.data)

    return SangerParams(
        num_samples=form.num_samples.data,
        base_volume=float(form.base_volume.data),
        dilution_factors=factors,
        sample_ids=sample_ids,
        best_dilution=best_dilution,
        best_concentration=best_concentration,
        pai_text=form.pai_sequences.data,
    )


def _prepare_submission(params: SangerParams) -> Tuple[str, str, Dict[str, object]]:
    """Generate protocol, CSV, and attempt order submission.

    This function delegates to SangerSubmissionBuilder for the actual work.
    It's kept as a public interface for backwards compatibility.

    Args:
        params: Validated protocol parameters

    Returns:
        Tuple of (protocol_script, pai_csv, order_status_dict)

    Raises:
        ValueError: If CSV generation fails (e.g., missing best_concentration)
    """
    builder = SangerSubmissionBuilder(params)
    return builder.build_complete_submission()


def _render_form(form: SangerForm) -> str:
    """Render form template with validation errors.

    Args:
        form: SangerForm instance (may have errors from validation)

    Returns:
        Rendered HTML template string
    """
    return render_template(
        'html/protocol_generator.html',
        title=SangerProtocol.title,
        description=SangerProtocol.description,
        instructions=SangerProtocol.instructions,
        form_action=NAME,
        input_fields=_input_fields(form),
    )


@bp.route(f'/protocols/{NAME}', methods=['GET', 'POST'])
def view():
    """Handle Sanger protocol form requests (GET) and submissions (POST)."""
    form = SangerForm(request.form)

    if request.method == 'GET':
        try:
            return _render_form(form)
        except Exception as exc:
            return Response(f"Error rendering form: {str(exc)}", mimetype='text/plain', status=500)

    # POST request
    if not form.validate():
        return _render_form(form), 400

    try:
        params = _build_params(form)
    except ValueError as exc:
        form.best_dilution.errors.append(str(exc))
        return _render_form(form), 400

    try:
        script, _, _ = _prepare_submission(params)
    except ValueError as exc:
        # Handle errors like missing best_concentration
        form.best_concentration.errors.append(str(exc))
        return _render_form(form), 400
    except Exception as exc:
        # Handle template rendering errors and other unexpected exceptions
        form.best_concentration.errors.append(f"Protocol generation error: {str(exc)}")
        return _render_form(form), 400

    headers = {'Content-disposition': 'attachment; filename=sanger.ot2'}
    return Response(script, mimetype='text', headers=headers)


@bp.route(f'/api/protocols/{NAME}', methods=['POST'])
def api():
    """API endpoint for Sanger protocol generation.

    Request body should be JSON with protocol parameters.

    Returns:
        JSON with protocol_string, pai_csv, order status, and parameters
    """
    form = SangerForm(MultiDict(mapping=request.json))

    if not form.validate():
        return Response(
            json.dumps({'errors': form.errors}),
            mimetype='application/json',
            status=400
        )

    try:
        params = _build_params(form)
    except ValueError as exc:
        return Response(
            json.dumps({'errors': {'validation': [str(exc)]}}),
            mimetype='application/json',
            status=400
        )

    try:
        protocol_str, pai_csv, order_status = _prepare_submission(params)
    except ValueError as exc:
        return Response(
            json.dumps({'errors': {'submission': [str(exc)]}}),
            mimetype='application/json',
            status=400
        )
    except Exception as exc:
        return Response(
            json.dumps({'errors': {'submission': [f"Protocol generation error: {str(exc)}"]}}),
            mimetype='application/json',
            status=400
        )

    return jsonify({
        'protocol_string': protocol_str,
        'order': order_status,
        'pai_csv': pai_csv,
        'best_dilution': params.best_dilution,
        'best_concentration': params.best_concentration,
    })
