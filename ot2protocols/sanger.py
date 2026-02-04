"""Sanger sequencing protocol generation + GeneWiz ordering."""

import json
import os
from typing import Dict, List

from flask import Blueprint, Response, jsonify, render_template, request
from wtforms import Form, DecimalField, IntegerField, StringField, validators
from werkzeug.datastructures import MultiDict

from ot2protocols import protocol, utils
from ot2protocols.integrations import BenchlingClient, BenchlingError, GeneWizClient, GeneWizError

FINAL_VOLUME_UL = 10
TARGET_MASS_NG = 1000

NAME = 'sanger'
bp = Blueprint(NAME, __name__)


class SangerForm(Form):
    num_samples = IntegerField(
        'Sample count',
        [validators.NumberRange(min=1, max=12)],
        default=4
    )
    base_volume = DecimalField(
        'Final per-well volume (µL)',
        [validators.NumberRange(min=10, max=200)],
        default=100
    )
    target_concentration = DecimalField(
        'Target concentration for NanoDrop (µM)',
        [validators.NumberRange(min=0)],
        default=10
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
        'Selected dilution factor (1/2/4/8)',
        [validators.NumberRange(min=1)],
        default=1
    )
    best_concentration = DecimalField(
        'NanoDrop concentration (ng/µL) for selected dilution',
        [validators.NumberRange(min=0)],
        default=0
    )
    pai_sequences = StringField(
        'PAI sequences (Sample:Sequence per line)',
        default=''
    )


class SangerProtocol(protocol.Protocol):
    short_name = NAME
    title = 'Sanger Sequencing Prep'
    description = 'Generate serial dilutions and NanoDrop prep for Sanger requests.'
    instructions = (
        'This workflow produces four dilution tiers per sample, records NanoDrop targets, '
        'and optionally prepares a GeneWiz order payload.'
    )

    def __init__(
        self,
        num_samples: int,
        base_volume: float,
        target_concentration: float,
        dilution_factors: List[float],
        sample_ids: List[str],
        best_dilution: float,
        best_concentration: float,
    ):
        # Persist user selections so the template can access them.
        self.num_samples = num_samples
        self.base_volume = base_volume
        self.target_concentration = target_concentration
        self.dilution_factors = dilution_factors
        self.sample_ids = sample_ids
        self.best_dilution = best_dilution
        self.best_concentration = best_concentration

    def generate(self) -> str:
        parameters = {
            'num_samples': self.num_samples,
            'base_volume': self.base_volume,
            'target_concentration': self.target_concentration,
            'dilution_factors': self.dilution_factors,
            'sample_ids': self.sample_ids,
            'best_dilution': self.best_dilution,
            'best_concentration': self.best_concentration,
        }
        return utils.protocol_from_template(
            parameters,
            'protocols/sanger_template.ot2',
            robot_config='protocols/config_highvol.ot2',
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            'num_samples': self.num_samples,
            'base_volume': self.base_volume,
            'target_concentration': self.target_concentration,
            'dilution_factors': self.dilution_factors,
            'sample_ids': self.sample_ids,
            'best_dilution': self.best_dilution,
            'best_concentration': self.best_concentration,
        }


def parse_factors(raw: str) -> List[float]:
    """Sanitize the comma-separated dilution factors from the form."""
    tokens = [token.strip() for token in raw.split(',') if token.strip()]
    if len(tokens) < 4:
        raise ValueError('Provide at least four dilution factors (e.g. 1,2,4,8).')
    values = []
    for token in tokens:
        try:
            value = float(token)
        except ValueError as exc:
            raise ValueError(f'Invalid dilution factor "{token}".') from exc
        if value <= 0:
            raise ValueError('Dilution factors must be positive.')
        values.append(value)
    return values


def parse_pai_sequences(raw: str) -> Dict[str, str]:
    """Convert the user-provided PAI lines into a sample -> sequence map."""
    entries = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        if ':' not in line:
            continue
        sample, sequence = line.split(':', 1)
        entries[sample.strip()] = sequence.strip()
    return entries


def _benchling_sequences(sample_ids: List[str]) -> Dict[str, str]:
    enabled = os.environ.get('BENCHLING_ENABLED', 'false').lower() in ('1', 'true', 'yes')
    if not enabled:
        return {}
    try:
        client = BenchlingClient()
    except BenchlingError:
        return {}
    sequences = {}
    for sample in sample_ids:
        try:
            seq = client.fetch_sequence(sample)
        except BenchlingError:
            continue
        if seq:
            sequences[sample] = seq
    return sequences


def build_pai_map(sample_ids: List[str], raw: str) -> Dict[str, str]:
    parsed = parse_pai_sequences(raw)
    benchling_data = _benchling_sequences(sample_ids)
    for sample in sample_ids:
        if sample not in parsed or not parsed[sample]:
            parsed[sample] = benchling_data.get(sample, parsed.get(sample, ''))
        parsed.setdefault(sample, parsed.get(sample, ''))
    return parsed


def build_pai_csv(
    sample_ids: List[str],
    sequence_map: Dict[str, str],
    best_dilution: float,
    best_concentration: float,
) -> str:
    """Render the per-sample PAI map as a comma-separated file."""
    final_mass = best_concentration * FINAL_VOLUME_UL
    lines = [
        'Sample,Sequence,Best Dilution,Selected Concentration (ng/µL),Final Volume (µL),Final Mass (ng)'
    ]
    for sample in sample_ids:
        sequence = sequence_map.get(sample, '')
        lines.append(
            f'{sample},{sequence},{best_dilution},{best_concentration},{FINAL_VOLUME_UL},{final_mass}'
        )
    return '\n'.join(lines)


def build_order_payload(protocol_dict: Dict[str, object]) -> Dict[str, object]:
    # Build a lightweight representation of the GeneWiz order so the UI can show status.
    return {
        'service': 'Sanger Sequencing',
        'samples': [
            {
                'name': sample,
                'dilution_factors': protocol_dict['dilution_factors'],
                'target_concentration': protocol_dict['target_concentration'],
                'pai_sequence': protocol_dict['pai_sequences'].get(sample, ''),
            }
            for sample in protocol_dict['sample_ids']
        ],
        'instructions': f"{len(protocol_dict['sample_ids'])} samples prepared at {protocol_dict['target_concentration']} µM",
        'pai_csv': protocol_dict['pai_csv'],
        'best_dilution': protocol_dict['best_dilution'],
        'best_concentration': protocol_dict['best_concentration'],
    }


def _maybe_place_order(payload: Dict[str, object]) -> Dict[str, object]:
    enabled = os.environ.get('GENEWIZ_ENABLED', 'false').lower() in ('1', 'true', 'yes')
    if not enabled:
        return {'status': 'disabled'}
    # Contact GeneWiz only when the feature flag and API key are configured.
    try:
        client = GeneWizClient()
        response = client.place_order(payload)
        return {'status': 'submitted', 'response': response}
    except GeneWizError as exc:
        return {'status': 'failed', 'error': str(exc)}


@bp.route(f'/protocols/{NAME}', methods=['GET', 'POST'])
def view():
    """Render the web form for generating the Sanger sequencing protocol."""
    form = SangerForm(request.form)
    if request.method == 'POST' and form.validate():
        try:
            dilution_factors = parse_factors(form.dilution_factors.data)
        except ValueError as exc:
            form.dilution_factors.errors.append(str(exc))
            input_fields = [
                form.num_samples,
                form.base_volume,
                form.target_concentration,
                form.dilution_factors,
                form.sample_ids,
                form.pai_sequences,
                form.best_dilution,
                form.best_concentration,
            ]
            return render_template('html/protocol_generator.html',
                                   title=SangerProtocol.title,
                                   description=SangerProtocol.description,
                                   instructions=SangerProtocol.instructions,
                                   form_action=NAME,
                                   input_fields=input_fields)
        sample_ids = [sid.strip() for sid in form.sample_ids.data.split(',') if sid.strip()]
        if len(sample_ids) < form.num_samples.data:
            # Provide placeholder sample names when the user leaves the field blank.
            sample_ids = [f'Sample{i + 1}' for i in range(form.num_samples.data)]
        protocol = SangerProtocol(
            num_samples=form.num_samples.data,
            base_volume=float(form.base_volume.data),
            target_concentration=float(form.target_concentration.data),
            dilution_factors=dilution_factors,
            sample_ids=sample_ids[:form.num_samples.data],
            best_dilution=float(form.best_dilution.data),
            best_concentration=float(form.best_concentration.data),
        )
        headers = {'Content-disposition': 'attachment; filename=sanger.ot2'}
        return Response(protocol.generate(), mimetype='text', headers=headers)
    input_fields = [
        form.num_samples,
        form.base_volume,
        form.target_concentration,
        form.dilution_factors,
        form.sample_ids,
        form.pai_sequences,
        form.best_dilution,
        form.best_concentration,
    ]
    return render_template('html/protocol_generator.html',
                           title=SangerProtocol.title,
                           description=SangerProtocol.description,
                           instructions=SangerProtocol.instructions,
                           form_action=NAME,
                           input_fields=input_fields)


@bp.route(f'/api/protocols/{NAME}', methods=['POST'])
def api():
    """Return the generated protocol & order metadata for downstream automation."""
    form = SangerForm(MultiDict(mapping=request.json))
    if not form.validate():
        return Response(json.dumps({'errors': form.errors}), mimetype='application/json', status=500)
    try:
        dilution_factors = parse_factors(form.dilution_factors.data)
    except ValueError as exc:
        return Response(json.dumps({'errors': {'dilution_factors': [str(exc)]}}), mimetype='application/json', status=500)

    sample_ids = [sid.strip() for sid in form.sample_ids.data.split(',') if sid.strip()]
    if len(sample_ids) < form.num_samples.data:
        # Mirror the same fallback logic for API callers.
        sample_ids = [f'Sample{i + 1}' for i in range(form.num_samples.data)]
    selected_samples = sample_ids[:form.num_samples.data]
    protocol = SangerProtocol(
        num_samples=form.num_samples.data,
        base_volume=float(form.base_volume.data),
        target_concentration=float(form.target_concentration.data),
        dilution_factors=dilution_factors,
        sample_ids=selected_samples,
        best_dilution=float(form.best_dilution.data),
        best_concentration=float(form.best_concentration.data),
    )
    protocol_dict = protocol.to_dict()
    sequence_map = build_pai_map(selected_samples, form.pai_sequences.data)
    pai_csv = build_pai_csv(
        selected_samples,
        sequence_map,
        float(form.best_dilution.data),
        float(form.best_concentration.data),
    )
    protocol_dict['pai_sequences'] = sequence_map
    protocol_dict['pai_csv'] = pai_csv
    payload = build_order_payload(protocol_dict)
    order_status = _maybe_place_order(payload)
    response = {
        'protocol_string': protocol.generate(),
        'order': order_status,
        'pai_csv': pai_csv,
    }
    return jsonify(response)
