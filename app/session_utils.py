from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

SESSION_COLUMNS = [
    "canonical_symbol", "exchange_trading_date", "exchange_timezone",
    "regular_open_utc", "regular_close_utc", "extended_open_utc",
    "extended_close_utc", "is_holiday", "is_shortened_session", "source",
    "metadata_json",
]


def _calendar(instrument: dict[str, Any]):
    name = instrument.get("session_calendar")
    if not name:
        return None
    try:
        import pandas_market_calendars as mcal
    except ImportError as exc:
        raise RuntimeError("Session enrichment requires pandas_market_calendars from requirements.lock") from exc
    return mcal.get_calendar(name)


def _schedule(instrument: dict[str, Any], start: datetime | pd.Timestamp, end: datetime | pd.Timestamp) -> pd.DataFrame:
    calendar = _calendar(instrument)
    if calendar is None:
        return pd.DataFrame()
    start_date = (pd.Timestamp(start).date() - timedelta(days=3)).isoformat()
    end_date = (pd.Timestamp(end).date() + timedelta(days=3)).isoformat()
    schedule = calendar.schedule(start_date=start_date, end_date=end_date, interruptions=True)
    if schedule.empty:
        return schedule
    for col in schedule.columns:
        schedule[col] = pd.to_datetime(schedule[col], utc=True, errors="coerce")
    schedule.index = pd.to_datetime(schedule.index).date
    return schedule


def build_market_sessions(instrument: dict[str, Any], start: datetime, end: datetime) -> pd.DataFrame:
    schedule = _schedule(instrument, start, end)
    if schedule.empty:
        return pd.DataFrame(columns=SESSION_COLUMNS)
    start_utc = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
    end_utc = pd.Timestamp(end).tz_convert("UTC") if pd.Timestamp(end).tzinfo else pd.Timestamp(end, tz="UTC")
    schedule = schedule[(schedule["market_close"] > start_utc) & (schedule["market_open"] < end_utc)]
    if schedule.empty:
        return pd.DataFrame(columns=SESSION_COLUMNS)
    duration = schedule["market_close"] - schedule["market_open"]
    typical = duration.mode().iloc[0] if not duration.mode().empty else duration.median()
    rows: list[dict[str, Any]] = []
    calendar_name = instrument.get("session_calendar")
    for label, row in schedule.iterrows():
        metadata = {
            "calendar": calendar_name,
            "break_start_utc": row.get("break_start").isoformat() if pd.notna(row.get("break_start")) else None,
            "break_end_utc": row.get("break_end").isoformat() if pd.notna(row.get("break_end")) else None,
            "calendar_role": instrument.get("session_calendar_role", "normal_session"),
            "limitations": instrument.get("session_calendar_limitations"),
        }
        rows.append({
            "canonical_symbol": instrument["canonical_symbol"],
            "exchange_trading_date": label,
            "exchange_timezone": instrument.get("exchange_timezone", "UTC"),
            "regular_open_utc": row["market_open"],
            "regular_close_utc": row["market_close"],
            "extended_open_utc": None,
            "extended_close_utc": None,
            "is_holiday": False,
            "is_shortened_session": bool(pd.notna(typical) and duration.loc[label] < typical - pd.Timedelta(minutes=1)),
            "source": f"pandas_market_calendars:{calendar_name}",
            "metadata_json": metadata,
        })
    return pd.DataFrame(rows, columns=SESSION_COLUMNS)


def _candidate_trading_dates(timestamps: pd.Series, instrument: dict[str, Any]) -> pd.Series:
    timezone_name = instrument.get("exchange_timezone", "UTC")
    local = timestamps.dt.tz_convert(ZoneInfo(timezone_name))
    dates = pd.Series(local.dt.date, index=timestamps.index, dtype="object")
    roll_text = instrument.get("trading_date_roll_time_local")
    if roll_text:
        hour, minute = (int(x) for x in str(roll_text).split(":", 1))
        after_roll = (local.dt.hour > hour) | ((local.dt.hour == hour) & (local.dt.minute >= minute))
        dates.loc[after_roll] = dates.loc[after_roll].map(lambda d: d + timedelta(days=1))
    return dates


def enrich_bar_sessions(frame: pd.DataFrame, instrument: dict[str, Any]) -> pd.DataFrame:
    """Add point-in-time session fields from a version-locked exchange calendar.

    The calendar's published schedule is treated as the instrument's configured normal
    session. Observed bars associated with a valid trading date but outside that window
    are labelled extended. Nothing is forward-filled and no artificial bars are created.
    """
    if frame.empty:
        return frame
    df = frame.copy()
    ts = pd.to_datetime(df["bar_open_timestamp_utc"], utc=True, errors="coerce")
    df["bar_open_timestamp_utc"] = ts
    calendar_name = instrument.get("session_calendar")

    if str(instrument.get("normal_session", "")).strip() == "24/7" and not calendar_name:
        calendar_name = "24/7"
        instrument = {**instrument, "session_calendar": calendar_name}

    if not calendar_name or ts.dropna().empty:
        local = ts.dt.tz_convert(ZoneInfo(instrument.get("exchange_timezone", "UTC")))
        df["exchange_trading_date"] = local.dt.date
        df["day_of_week"] = local.dt.dayofweek
        df["session_type"] = "unclassified_no_calendar"
        df["is_regular_session"] = pd.NA
        df["is_extended_session"] = pd.NA
        df["minutes_since_session_open"] = pd.NA
        df["minutes_until_session_close"] = pd.NA
        df["is_holiday"] = False
        df["is_shortened_session"] = False
        return df

    schedule = _schedule(instrument, ts.min(), ts.max())
    candidate_dates = _candidate_trading_dates(ts, instrument)
    valid_dates = set(schedule.index)

    trading_date = pd.Series(candidate_dates, index=df.index, dtype="object")
    regular = pd.Series(False, index=df.index, dtype="boolean")
    extended = pd.Series(False, index=df.index, dtype="boolean")
    session_type = pd.Series("outside_scheduled_market", index=df.index, dtype="object")
    since_open = pd.Series(pd.NA, index=df.index, dtype="Int64")
    until_close = pd.Series(pd.NA, index=df.index, dtype="Int64")
    shortened = pd.Series(False, index=df.index, dtype="boolean")

    durations = schedule["market_close"] - schedule["market_open"]
    typical = durations.mode().iloc[0] if not durations.mode().empty else durations.median()

    for label, row in schedule.iterrows():
        full = ts.ge(row["market_open"]) & ts.lt(row["market_close"])
        active = full.copy()
        break_start = row.get("break_start")
        break_end = row.get("break_end")
        if pd.notna(break_start) and pd.notna(break_end):
            in_break = ts.ge(break_start) & ts.lt(break_end)
            active &= ~in_break
            trading_date.loc[in_break] = label
            session_type.loc[in_break] = "scheduled_break_observation"
        trading_date.loc[full] = label
        regular.loc[active] = True
        session_type.loc[active] = "regular_session"
        since_open.loc[active] = ((ts.loc[active] - row["market_open"]) / pd.Timedelta(minutes=1)).astype("int64")
        until_close.loc[active] = ((row["market_close"] - ts.loc[active]) / pd.Timedelta(minutes=1)).astype("int64")
        if pd.notna(typical) and durations.loc[label] < typical - pd.Timedelta(minutes=1):
            shortened.loc[full] = True

    valid_candidate = candidate_dates.isin(valid_dates)
    outside_regular = (~regular.fillna(False)) & valid_candidate & session_type.ne("scheduled_break_observation")
    extended.loc[outside_regular] = True
    session_type.loc[outside_regular] = "extended_session"
    holiday = ~valid_candidate

    df["exchange_trading_date"] = trading_date
    df["day_of_week"] = pd.to_datetime(pd.Series(trading_date, index=df.index), errors="coerce").dt.dayofweek
    df["session_type"] = session_type
    df["is_regular_session"] = regular
    df["is_extended_session"] = extended
    df["minutes_since_session_open"] = since_open
    df["minutes_until_session_close"] = until_close
    df["is_holiday"] = holiday.astype(bool)
    df["is_shortened_session"] = shortened.fillna(False).astype(bool)
    return df
