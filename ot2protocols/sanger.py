"""Sanger sequencing workflow with Benchling/GeneWiz integrations.

Generates OT-2 protocols for preparing samples for Sanger sequencing:
1. Creates serial dilution series of each sample
2. Pauses for NanoDrop concentration measurement
3. Transfers the optimal dilution to output tubes
4. Optionally queries Benchling for sequences and submits to GeneWiz
"""

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple, Type

from flask import Blueprint, Response, jsonify, render_template, request
from wtforms import Form, DecimalField, IntegerField, StringField, ValidationError, validators
from werkzeug.datastructures import MultiDict

from ot2protocols import protocol, utils
from ot2protocols.integrations import (
    APIError, BaseAPIClient, BenchlingClient, BenchlingError, GeneWizClient, GeneWizError
)

NAME = 'sanger'
FINAL_VOLUME_UL = 10
TARGET_MASS_NG = 1000
bp = Blueprint(NAME, __name__)


def _is_feature_enabled(env_var: str) -> bool:
    """Check if a feature is enabled via environment variable."""
    return os.environ.get(env_var, 'false').lower() in ('1', 'true', 'yes')


def _get_client(
    client_class: Type[BaseAPIClient],
    env_var: str,
    injected: Optional[BaseAPIClient] = None
) -> Optional[BaseAPIClient]:
    """Get an API client, using injected instance or creating one if enabled."""
    if injected is not None:
        return injected
    if not _is_feature_enabled(env_var):
        return None
    try:
        return client_class()
    except APIError:
        return None


@dataclass
class SangerParams:
    """Protocol parameters for Sanger sequencing."""
    num_samples: int
    base_volume: float
    dilution_factors: List[float]
    sample_ids: List[str]
    best_dilution: Optional[float]
    best_concentration: Optional[float]
    pai_text: str

    @property
    def selected_samples(self) -> List[str]:
        """Return sample IDs matching num_samples."""
        return self.sample_ids[:self.num_samples]


class SangerForm(Form):
    """Form for Sanger protocol parameters."""
    num_samples = IntegerField('Sample count', [validators.NumberRange(min=1, max=12)], default=4)
    base_volume = DecimalField('Final per-well volume (µL)', [validators.NumberRange(min=20, max=300)], default=100)
    dilution_factors = StringField('Dilution factors (comma separated)', default='1,2,4,8')
    sample_ids = StringField('Sample IDs (comma separated)', default='')
    pai_sequences = StringField('PAI sequences (Sample:Sequence per line)', default='')
    best_dilution = DecimalField(
        'Selected dilution factor (leave blank until after NanoDrop)',
        [validators.Optional(), validators.NumberRange(min=1)],
        default=None
    )
    best_concentration = DecimalField(
        'NanoDrop concentration (ng/µL)',
        [validators.Optional(), validators.NumberRange(min=0)],
        default=None
    )

    def validate_base_volume(form, field):
        if field.data and not (20 <= field.data <= 300):
            raise ValidationError('Must be 20-300 µL (P300 pipette range)')

    def validate_dilution_factors(form, field):
        try:
            factors = parse_factors(field.data)
            if len(factors) > 8:
                raise ValidationError('Maximum 8 dilution factors')
        except ValueError as exc:
            raise ValidationError(str(exc))

    def validate_best_dilution(form, field):
        if field.data is None:
            return
        try:
            factors = parse_factors(form.dilution_factors.data)
            if field.data not in factors:
                raise ValidationError(f'Must be one of: {factors}')
        except ValueError:
            pass  # Let dilution_factors validation catch it


class SangerProtocol(protocol.Protocol):
    """Generates OT-2 protocol for Sanger sequencing preparation."""
    short_name = NAME
    title = 'Sanger Sequencing Prep'
    description = 'Generate serial dilutions and NanoDrop prep for Sanger requests.'
    instructions = 'Generate four dilution tiers per sample, pause for NanoDrop, then transfer the winning dilution into final tubes.'

    def __init__(self, params: SangerParams):
        self._validate_params(params)
        self.params = params

    def _validate_params(self, params: SangerParams) -> None:
        if params.best_dilution is not None and params.best_dilution not in params.dilution_factors:
            raise ValueError(f'Best dilution {params.best_dilution} not in factors {params.dilution_factors}')
        if params.best_concentration is not None and params.best_concentration < 0:
            raise ValueError('Best concentration cannot be negative')
        if params.num_samples > len(params.sample_ids):
            raise ValueError(f'Requested {params.num_samples} samples but only {len(params.sample_ids)} IDs provided')

    def generate(self) -> str:
        return utils.protocol_from_template(
            self._template_params(),
            'protocols/sanger_template.ot2',
            robot_config='protocols/config_highvol.ot2',
        )

    def _template_params(self) -> Dict[str, Any]:
        return {
            'num_samples': self.params.num_samples,
            'base_volume': self.params.base_volume,
            'dilution_factors': self.params.dilution_factors,
            'sample_ids': self.params.selected_samples,
            'best_dilution': self.params.best_dilution,
            'best_concentration': self.params.best_concentration,
        }

    def to_dict(self) -> Dict[str, Any]:
        return self._template_params()


class SangerSubmissionBuilder:
    """Orchestrates protocol generation and order submission with dependency injection."""

    def __init__(
        self,
        params: SangerParams,
        benchling_client: Optional[BenchlingClient] = None,
        genewiz_client: Optional[GeneWizClient] = None,
    ):
        self.params = params
        self._benchling = benchling_client
        self._genewiz = genewiz_client

    @property
    def benchling_client(self) -> Optional[BenchlingClient]:
        return _get_client(BenchlingClient, 'BENCHLING_ENABLED', self._benchling)

    @property
    def genewiz_client(self) -> Optional[GeneWizClient]:
        return _get_client(GeneWizClient, 'GENEWIZ_ENABLED', self._genewiz)

    def build_sequence_map(self) -> Dict[str, str]:
        """Build sample -> sequence mapping from manual + Benchling sources."""
        manual = parse_pai_sequences(self.params.pai_text)
        benchling_data = self._fetch_benchling_sequences() if self.benchling_client else {}
        return {s: manual.get(s) or benchling_data.get(s, '') for s in self.params.selected_samples}

    def _fetch_benchling_sequences(self) -> Dict[str, str]:
        sequences = {}
        for sample in self.params.selected_samples:
            try:
                seq = self.benchling_client.fetch_sequence(sample)
                if seq:
                    sequences[sample] = seq
            except BenchlingError:
                continue
        return sequences

    def build_protocol(self) -> str:
        return SangerProtocol(self.params).generate()

    def build_complete_submission(self) -> Tuple[str, str, Dict[str, Any]]:
        """Generate protocol, CSV, and attempt order submission."""
        sequence_map = self.build_sequence_map()
        protocol_script = self.build_protocol()
        pai_csv = build_pai_csv(self.params, sequence_map)
        payload = build_order_payload(self.params, sequence_map, pai_csv)
        order_status = self._submit_order(payload)
        return protocol_script, pai_csv, order_status

    def _submit_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.genewiz_client is None:
            return {'status': 'disabled', 'message': 'GeneWiz not enabled'}
        try:
            response = self.genewiz_client.place_order(payload)
            return {'status': 'submitted', 'response': response, 'timestamp': datetime.now().isoformat()}
        except GeneWizError as exc:
            return {'status': 'failed', 'error': str(exc), 'type': 'genewiz_error'}


# --- Parsing helpers ---

def parse_factors(raw: str) -> List[float]:
    """Parse comma-separated dilution factors. Requires 4-8 positive values."""
    tokens = [t.strip() for t in raw.split(',') if t.strip()]
    if len(tokens) < 4:
        raise ValueError('Provide at least four dilution factors (e.g. 1,2,4,8).')
    if len(tokens) > 8:
        raise ValueError('Maximum 8 dilution factors (96-well plate row limit).')

    values = []
    for token in tokens:
        try:
            value = float(token)
        except ValueError:
            raise ValueError(f'Invalid dilution factor "{token}".')
        if value <= 0 or not (-1e308 < value < 1e308):
            raise ValueError(f'Dilution factor "{token}" must be a positive number.')
        values.append(value)
    return values


def _normalize_sample_ids(raw: str, min_count: int) -> List[str]:
    """Generate sample IDs from input or use defaults. Pads/truncates to min_count."""
    if not raw or not raw.strip():
        return [f'Sample{i + 1}' for i in range(min_count)]

    ids = []
    for sid in raw.split(','):
        sid = sid.strip()
        if not sid:
            raise ValueError('Sample IDs cannot be empty (found comma with no text)')
        if len(sid) > 50:
            raise ValueError(f'Sample ID "{sid[:20]}..." exceeds 50 character limit')
        ids.append(sid)

    while len(ids) < min_count:
        ids.append(f'Sample{len(ids) + 1}')
    return ids[:min_count]


def parse_pai_sequences(raw: str) -> Dict[str, str]:
    """Parse PAI sequences in 'Sample:Sequence' format (one per line)."""
    entries = {}
    for line in raw.splitlines():
        line = line.strip()
        if line and ':' in line:
            sample, sequence = line.split(':', 1)
            entries[sample.strip()] = sequence.strip()
    return entries


# --- Output builders ---

def build_pai_csv(params: SangerParams, sequence_map: Dict[str, str]) -> str:
    """Generate CSV with sample data for sequencing order."""
    if params.best_concentration is None and params.best_dilution is not None:
        raise ValueError('NanoDrop concentration required to generate final CSV.')

    final_mass = params.best_concentration * FINAL_VOLUME_UL if params.best_concentration else None

    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=['Sample', 'Sequence', 'Best Dilution', 'Selected Concentration (ng/µL)',
                   'Final Volume (µL)', 'Final Mass (ng)'],
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
            'Final Mass (ng)': final_mass or 'N/A',
        })
    return output.getvalue()


def build_order_payload(params: SangerParams, sequence_map: Dict[str, str], pai_csv: str) -> Dict[str, Any]:
    """Build GeneWiz order payload."""
    return {
        'service': 'Sanger Sequencing',
        'samples': [
            {'name': s, 'dilution_factors': params.dilution_factors, 'pai_sequence': sequence_map.get(s, '')}
            for s in params.selected_samples
        ],
        'instructions': f"{len(params.selected_samples)} samples prepared at {params.best_concentration} ng/µL",
        'pai_csv': pai_csv,
        'best_dilution': params.best_dilution,
        'best_concentration': params.best_concentration,
        'final_volume_ul': FINAL_VOLUME_UL,
        'final_mass_ng': TARGET_MASS_NG,
    }


# --- Form/route helpers ---

def _build_params(form: SangerForm) -> SangerParams:
    """Convert validated form to SangerParams."""
    factors = parse_factors(form.dilution_factors.data)
    best_dilution = float(form.best_dilution.data) if form.best_dilution.data else None

    if best_dilution is not None and best_dilution not in factors:
        raise ValueError(f'Selected dilution {best_dilution} must be one of: {factors}')

    return SangerParams(
        num_samples=form.num_samples.data,
        base_volume=float(form.base_volume.data),
        dilution_factors=factors,
        sample_ids=_normalize_sample_ids(form.sample_ids.data, form.num_samples.data),
        best_dilution=best_dilution,
        best_concentration=float(form.best_concentration.data) if form.best_concentration.data else None,
        pai_text=form.pai_sequences.data,
    )


def _render_form(form: SangerForm) -> str:
    return render_template(
        'html/protocol_generator.html',
        title=SangerProtocol.title,
        description=SangerProtocol.description,
        instructions=SangerProtocol.instructions,
        form_action=NAME,
        input_fields=[form.num_samples, form.base_volume, form.dilution_factors,
                      form.sample_ids, form.pai_sequences, form.best_dilution, form.best_concentration],
    )


def _handle_submission(form: SangerForm) -> Tuple[Optional[str], Optional[str], int]:
    """Process form submission, returning (script, error_field, status_code)."""
    try:
        params = _build_params(form)
        script, _, _ = SangerSubmissionBuilder(params).build_complete_submission()
        return script, None, 200
    except ValueError as exc:
        return None, str(exc), 400


# --- Routes ---

@bp.route(f'/protocols/{NAME}', methods=['GET', 'POST'])
def view():
    """Handle Sanger protocol form requests."""
    form = SangerForm(request.form)

    if request.method == 'GET':
        return _render_form(form)

    if not form.validate():
        return _render_form(form), 400

    script, error, status = _handle_submission(form)
    if error:
        form.best_dilution.errors.append(error)
        return _render_form(form), status

    return Response(script, mimetype='text', headers={'Content-disposition': 'attachment; filename=sanger.ot2'})


@bp.route(f'/api/protocols/{NAME}', methods=['POST'])
def api():
    """API endpoint for Sanger protocol generation."""
    form = SangerForm(MultiDict(mapping=request.json))

    if not form.validate():
        return jsonify({'errors': form.errors}), 400

    try:
        params = _build_params(form)
        protocol_str, pai_csv, order_status = SangerSubmissionBuilder(params).build_complete_submission()
    except ValueError as exc:
        return jsonify({'errors': {'validation': [str(exc)]}}), 400

    return jsonify({
        'protocol_string': protocol_str,
        'order': order_status,
        'pai_csv': pai_csv,
        'best_dilution': params.best_dilution,
        'best_concentration': params.best_concentration,
    })
