"""Sanger sequencing workflow + external integrations."""

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Sequence

from flask import Blueprint, Response, jsonify, render_template, request
from wtforms import Form, DecimalField, IntegerField, StringField, validators
from werkzeug.datastructures import MultiDict

from ot2protocols import protocol, utils
from ot2protocols.integrations import BenchlingClient, BenchlingError, GeneWizClient, GeneWizError

NAME = 'sanger'
FINAL_VOLUME_UL = 10
TARGET_MASS_NG = 1000
bp = Blueprint(NAME, __name__)


@dataclass
class SangerParams:
    num_samples: int
    base_volume: float
    target_concentration: float
    dilution_factors: List[float]
    sample_ids: List[str]
    best_dilution: float
    best_concentration: float
    pai_text: str

    @property
    def selected_samples(self) -> List[str]:
        return self.sample_ids[:self.num_samples]


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
        'Guidance concentration (µM) — optional until NanoDrop is done',
        [validators.Optional(), validators.NumberRange(min=0)],
        default=None
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


class SangerProtocol(protocol.Protocol):
    short_name = NAME
    title = 'Sanger Sequencing Prep'
    description = 'Generate serial dilutions and NanoDrop prep for Sanger requests.'
    instructions = (
        'Generate four dilution tiers per sample, pause for NanoDrop, then transfer the winning dilution into final tubes.'
    )

    def __init__(self, params: SangerParams):
        self.params = params

    def generate(self) -> str:
        parameters = {
            'num_samples': self.params.num_samples,
            'base_volume': self.params.base_volume,
            'target_concentration': self.params.target_concentration,
            'dilution_factors': self.params.dilution_factors,
            'sample_ids': self.params.selected_samples,
            'best_dilution': self.params.best_dilution,
            'best_concentration': self.params.best_concentration,
        }
        return utils.protocol_from_template(
            parameters,
            'protocols/sanger_template.ot2',
            robot_config='protocols/config_highvol.ot2',
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            'num_samples': self.params.num_samples,
            'base_volume': self.params.base_volume,
            'target_concentration': self.params.target_concentration,
            'dilution_factors': self.params.dilution_factors,
            'sample_ids': self.params.selected_samples,
            'best_dilution': self.params.best_dilution,
            'best_concentration': self.params.best_concentration,
        }


def parse_factors(raw: str) -> List[float]:
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


def _normalize_sample_ids(raw: str, min_count: int) -> List[str]:
    ids = [sid.strip() for sid in raw.split(',') if sid.strip()]
    while len(ids) < min_count:
        ids.append(f'Sample{len(ids) + 1}')
    return ids


def _validate_best_dilution(best: float, factors: Sequence[float]) -> None:
    if best not in factors:
        raise ValueError('Selected dilution must be one of the provided factors.')


def parse_pai_sequences(raw: str) -> Dict[str, str]:
    entries = {}
    for line in raw.splitlines():
        if not line.strip() or ':' not in line:
            continue
        sample, sequence = line.split(':', 1)
        entries[sample.strip()] = sequence.strip()
    return entries


def _benchling_sequences(sample_ids: Sequence[str]) -> Dict[str, str]:
    enabled = os.environ.get('BENCHLING_ENABLED', 'false').lower() in ('1', 'true', 'yes')
    if not enabled:
        return {}
    try:
        client = BenchlingClient()
    except BenchlingError:
        return {}
    sequences: Dict[str, str] = {}
    for sample in sample_ids:
        try:
            seq = client.fetch_sequence(sample)
        except BenchlingError:
            continue
        if seq:
            sequences[sample] = seq
    return sequences


def build_pai_map(params: SangerParams) -> Dict[str, str]:
    manual = parse_pai_sequences(params.pai_text)
    bench_data = _benchling_sequences(params.selected_samples)
    result: Dict[str, str] = {}
    for sample in params.selected_samples:
        sequence = manual.get(sample) or bench_data.get(sample, '')
        result[sample] = sequence
    return result


def build_pai_csv(params: SangerParams, sequence_map: Dict[str, str]) -> str:
    final_mass = params.best_concentration * FINAL_VOLUME_UL
    header = 'Sample,Sequence,Best Dilution,Selected Concentration (ng/µL),Final Volume (µL),Final Mass (ng)'
    lines = [header]
    for sample in params.selected_samples:
        sequence = sequence_map.get(sample, '')
        lines.append(
            f'{sample},{sequence},{params.best_dilution},{params.best_concentration},{FINAL_VOLUME_UL},{final_mass}'
        )
    return '\n'.join(lines)


def build_order_payload(
    params: SangerParams,
    sequence_map: Dict[str, str],
    pai_csv: str,
) -> Dict[str, object]:
    return {
        'service': 'Sanger Sequencing',
        'samples': [
            {
                'name': sample,
                'dilution_factors': params.dilution_factors,
                'target_concentration': params.target_concentration,
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


def _maybe_place_order(payload: Dict[str, object]) -> Dict[str, object]:
    enabled = os.environ.get('GENEWIZ_ENABLED', 'false').lower() in ('1', 'true', 'yes')
    if not enabled:
        return {'status': 'disabled'}
    try:
        client = GeneWizClient()
        response = client.place_order(payload)
        return {'status': 'submitted', 'response': response}
    except GeneWizError as exc:
        return {'status': 'failed', 'error': str(exc)}


def _input_fields(form: SangerForm) -> List:
    return [
        form.num_samples,
        form.base_volume,
        form.target_concentration,
        form.dilution_factors,
        form.sample_ids,
        form.pai_sequences,
        form.best_dilution,
        form.best_concentration,
    ]


def _build_params(form: SangerForm) -> SangerParams:
    factors = parse_factors(form.dilution_factors.data)
    best_dilution = float(form.best_dilution.data or factors[0])
    best_concentration = float(form.best_concentration.data or 0)
    _validate_best_dilution(best_dilution, factors)
    sample_ids = _normalize_sample_ids(form.sample_ids.data, form.num_samples.data)
    return SangerParams(
        num_samples=form.num_samples.data,
        base_volume=float(form.base_volume.data),
    target_concentration=float(form.target_concentration.data or 0),
        dilution_factors=factors,
        sample_ids=sample_ids,
        best_dilution=best_dilution,
        best_concentration=best_concentration,
        pai_text=form.pai_sequences.data,
    )


def _prepare_submission(params: SangerParams):
    sequence_map = build_pai_map(params)
    pai_csv = build_pai_csv(params, sequence_map)
    protocol = SangerProtocol(params)
    payload = build_order_payload(params, sequence_map, pai_csv)
    order_status = _maybe_place_order(payload)
    return protocol.generate(), pai_csv, order_status


def _render_form(form: SangerForm):
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
    form = SangerForm(request.form)
    if request.method == 'POST' and form.validate():
        try:
            params = _build_params(form)
        except ValueError as exc:
            form.best_dilution.errors.append(str(exc))
            return _render_form(form)
        script, _, _ = _prepare_submission(params)
        headers = {'Content-disposition': 'attachment; filename=sanger.ot2'}
        return Response(script, mimetype='text', headers=headers)
    return _render_form(form)


@bp.route(f'/api/protocols/{NAME}', methods=['POST'])
def api():
    form = SangerForm(MultiDict(mapping=request.json))
    if not form.validate():
        return Response(json.dumps({'errors': form.errors}), mimetype='application/json', status=500)
    try:
        params = _build_params(form)
    except ValueError as exc:
        return Response(json.dumps({'errors': {'best_dilution': [str(exc)]}}), mimetype='application/json', status=500)
    protocol_str, pai_csv, order_status = _prepare_submission(params)
    return jsonify({
        'protocol_string': protocol_str,
        'order': order_status,
        'pai_csv': pai_csv,
        'best_dilution': params.best_dilution,
        'best_concentration': params.best_concentration,
    })
