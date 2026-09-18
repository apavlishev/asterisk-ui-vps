import os
import sys
import time
import subprocess
from flask import Blueprint, request, jsonify, Response, session
from werkzeug.utils import secure_filename

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from autodialer_engine import AutoDialerEngine

autodialer_bp = Blueprint('autodialer_bp', __name__)
engine = AutoDialerEngine()

SOUNDS_DIR = "/var/lib/asterisk/sounds/custom"

@autodialer_bp.route('/api/autodialer/campaigns', methods=['GET'])
def list_campaigns():
    return jsonify({
        'success': True,
        'campaigns': engine.get_all_campaigns()
    })

@autodialer_bp.route('/api/autodialer/campaigns', methods=['POST'])
def create_campaign():
    data = request.get_json(silent=True) or {}
    if not data.get('numbers'):
        return jsonify({'success': False, 'error': 'Список номеров пуст'}), 400

    campaign = engine.create_campaign(data)
    return jsonify({
        'success': True,
        'campaign': campaign,
        'message': f"Кампания '{campaign['name']}' успешно создана ({campaign['stats']['total']} уникальных номеров)"
    })

@autodialer_bp.route('/api/autodialer/campaigns/<camp_id>', methods=['GET'])
def get_campaign(camp_id):
    camp = engine.get_campaign(camp_id)
    if not camp:
        return jsonify({'success': False, 'error': 'Кампания не найдена'}), 404
    return jsonify({'success': True, 'campaign': camp})

@autodialer_bp.route('/api/autodialer/campaigns/<camp_id>', methods=['DELETE'])
def delete_campaign(camp_id):
    ok = engine.delete_campaign(camp_id)
    return jsonify({'success': ok})

@autodialer_bp.route('/api/autodialer/campaigns/<camp_id>/start', methods=['POST'])
def start_campaign(camp_id):
    ok, msg = engine.start_campaign(camp_id)
    return jsonify({'success': ok, 'message': msg})

@autodialer_bp.route('/api/autodialer/campaigns/<camp_id>/pause', methods=['POST'])
def pause_campaign(camp_id):
    ok, msg = engine.pause_campaign(camp_id)
    return jsonify({'success': ok, 'message': msg})

@autodialer_bp.route('/api/autodialer/campaigns/<camp_id>/resume', methods=['POST'])
def resume_campaign(camp_id):
    ok, msg = engine.resume_campaign(camp_id)
    return jsonify({'success': ok, 'message': msg})

@autodialer_bp.route('/api/autodialer/campaigns/<camp_id>/stop', methods=['POST'])
def stop_campaign(camp_id):
    ok, msg = engine.stop_campaign(camp_id)
    return jsonify({'success': ok, 'message': msg})

@autodialer_bp.route('/api/autodialer/campaigns/<camp_id>/export', methods=['GET'])
def export_campaign(camp_id):
    category = request.args.get('category', 'clean')
    content = engine.export_numbers(camp_id, category)
    camp = engine.get_campaign(camp_id)
    name = (camp.get('name') if camp else camp_id).replace(' ', '_')

    ext = "csv" if category == 'all' else "txt"
    mimetype = "text/csv" if category == 'all' else "text/plain"
    filename = f"autodial_{category}_{name}_{int(time.time())}.{ext}"

    return Response(
        content,
        mimetype=mimetype,
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )

@autodialer_bp.route('/api/autodialer/campaigns/<camp_id>/blacklist', methods=['POST'])
def add_to_blacklist(camp_id):
    """Adds non-viable numbers or bots from campaign into PBX Anti-Spam Blacklist."""
    data = request.get_json(silent=True) or {}
    category = data.get('category', 'not_exist')  # 'bots', 'not_exist', 'unavailable'

    camp = engine.get_campaign(camp_id)
    if not camp:
        return jsonify({'success': False, 'error': 'Кампания не найдена'}), 404

    target_phones = []
    if category == 'bots':
        target_phones = [p for p, item in camp['numbers'].items() if item.get('status') == 'antispam_bot']
    elif category == 'unavailable':
        target_phones = [p for p, item in camp['numbers'].items() if item.get('status') in ('unavailable', 'no_answer')]
    elif category == 'not_exist':
        target_phones = [p for p, item in camp['numbers'].items() if item.get('status') == 'not_exist']
    elif category == 'all_bad':
        target_phones = [p for p, item in camp['numbers'].items() if item.get('status') in ('antispam_bot', 'unavailable', 'not_exist')]

    if not target_phones:
        return jsonify({'success': False, 'error': 'Нет номеров в выбранной категории для добавления'}), 400

    try:
        import app as core
        cfg = core.load_integrations()
        nl = cfg.setdefault('number_lists', {})
        bl = nl.setdefault('blacklist', [])
        added_count = 0
        for p in target_phones:
            if p not in bl:
                bl.append(p)
                added_count += 1
        core.save_integrations(cfg)
        core.generate_dialplan_from_tree()
        return jsonify({
            'success': True,
            'added': added_count,
            'message': f"Успешно добавлено {added_count} номеров в Черный список АТС"
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f"Ошибка сохранения в черный список: {e}"}), 500

@autodialer_bp.route('/api/autodialer/audio/upload', methods=['POST'])
def upload_audio():
    """Uploads a voice prompt with optional sox 8000Hz mono conversion."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'Файл не передан'}), 400
    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'success': False, 'error': 'Файл не выбран'}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.mp3', '.wav', '.gsm', '.ogg'):
        return jsonify({'success': False, 'error': 'Допустимы только файлы .mp3, .wav, .gsm'}), 400

    os.makedirs(SOUNDS_DIR, exist_ok=True)
    clean_name = secure_filename(file.filename) or f"autodial_{int(time.time())}{ext}"
    target_path = os.path.join(SOUNDS_DIR, clean_name)
    file.save(target_path)

    final_name = clean_name
    if ext == '.mp3':
        wav_name = os.path.splitext(clean_name)[0] + '.wav'
        wav_path = os.path.join(SOUNDS_DIR, wav_name)
        try:
            subprocess.run(['sox', target_path, '-r', '8000', '-c', '1', wav_path], capture_output=True, timeout=30)
            if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
                os.remove(target_path)
                final_name = wav_name
        except Exception:
            pass

    return jsonify({
        'success': True,
        'filename': final_name,
        'url': f"/api/sounds/{final_name}"
    })

@autodialer_bp.route('/api/autodialer/resources', methods=['GET'])
def get_resources():
    """Returns audio prompts, SIP trunks, extensions and queues for campaign builder."""
    trunks = []
    extens = []
    queues = []
    sounds = []

    try:
        import app as core
        cfg = core.load_integrations()
        # Trunks
        for t in cfg.get('sip_trunks', []):
            if t.get('enabled', True):
                trunks.append({'id': t['id'], 'name': t.get('name', t['id']), 'type': 'sip'})
        # GSM dongle
        trunks.append({'id': 'dongle0', 'name': 'GSM Модем (dongle0 / СИМ-карта)', 'type': 'gsm'})
        # Telegram trunk
        tg = cfg.get('telegram_trunk', {})
        if tg.get('enabled'):
            trunks.append({'id': 'telegram_trunk', 'name': 'Telegram SIP Trunk', 'type': 'telegram'})

        # Subscribers / Extensions
        accounts = core.load_sip_accounts()
        extens = [{'exten': a['exten'], 'name': a.get('callerid', a['exten'])} for a in accounts]

        # Queues
        queues = [{'exten': q.get('exten'), 'name': q.get('name', q.get('exten'))} for q in core.get_queues()]

        # Sounds
        sounds = [f['filename'] for f in core.list_sound_files()]
    except Exception as e:
        print(f"[autodialer] Error loading resources: {e}")

    return jsonify({
        'success': True,
        'trunks': trunks,
        'extensions': extens,
        'queues': queues,
        'sounds': sounds
    })
