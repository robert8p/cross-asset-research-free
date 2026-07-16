from pathlib import Path


def test_blueprint_has_separate_worker_and_python_pin():
    text = Path('render.yaml').read_text()
    assert 'type: worker' in text
    assert 'startCommand: python -m app.worker' in text
    assert 'value: 3.12.11' in text
    assert 'fromService:' in text


def test_dashboard_only_queues_jobs():
    text = Path('app/status_api.py').read_text()
    assert 'RUNNER.start' not in text
    assert 'create_job(db, "full_setup")' in text


def test_worker_is_lightweight_at_import_boundary():
    text = Path('app/worker.py').read_text()
    assert 'import pandas' not in text
    assert 'import pyarrow' not in text
    assert 'subprocess.Popen' in text
