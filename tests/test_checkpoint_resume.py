from datetime import datetime, timedelta, timezone

from app.pipeline import checkpoint_resume_start

UTC = timezone.utc


def test_resume_only_exact_same_historical_request():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 4, 1, tzinfo=UTC)
    checkpoint = {
        "last_complete_timestamp_utc": datetime(2026, 2, 1, tzinfo=UTC),
        "metadata": {
            "job_type": "historical_backfill",
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
        },
    }
    assert checkpoint_resume_start(start, end, checkpoint, "historical_backfill", timedelta(minutes=30)) == datetime(2026, 1, 31, 23, 30, tzinfo=UTC)


def test_mismatched_or_incremental_checkpoint_cannot_skip_history():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 4, 1, tzinfo=UTC)
    newer = {
        "last_complete_timestamp_utc": datetime(2026, 7, 1, tzinfo=UTC),
        "metadata": {
            "job_type": "incremental",
            "requested_start": datetime(2026, 6, 29, tzinfo=UTC).isoformat(),
            "requested_end": datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
        },
    }
    assert checkpoint_resume_start(start, end, newer, "historical_backfill", timedelta(minutes=30)) == start
    assert checkpoint_resume_start(start, end, newer, "incremental", timedelta(minutes=30)) == start
