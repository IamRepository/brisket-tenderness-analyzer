from __future__ import annotations
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path
import numpy as np
import pandas as pd

BAND_LOWER_C=np.array([60,65.5555555556,71.1111111111,76.6666666667,82.2222222222,87.7777777778,90.5555555556,93.3333333333,96.1111111111,98.8888888889])
ZONE_RATES=np.array([.0145,.0245,.039,.068,.1305,.208,.29,.43,.63,.75])
BAND_LABELS=['60.0 to <65.6°C','65.6 to <71.1°C','71.1 to <76.7°C','76.7 to <82.2°C','82.2 to <87.8°C','87.8 to <90.6°C','90.6 to <93.3°C','93.3 to <96.1°C','96.1 to <98.9°C','98.9°C and above']

@dataclass(frozen=True)
class ParseReport:
    source_rows:int; valid_rows:int; invalid_rows:int; duplicate_timestamps:int
    first_timestamp:pd.Timestamp|None; last_timestamp:pd.Timestamp|None
@dataclass(frozen=True)
class Detection:
    session_type:str; confidence:int; pull_timestamp:pd.Timestamp|None
    peak_temperature:float; average_temperature:float; minimum_temperature:float; reason:str
@dataclass(frozen=True)
class Result:
    summary:pd.DataFrame; timeline:pd.DataFrame; report:ParseReport; detection:Detection
    excluded_gaps:int; excluded_gap_hours:float; below_model_hours:float; analysed_hours:float
    cook:float; hold:float; total:float; assessment:str; complete:bool

# ---------------------------------------------------------------------------
# Reading timestamps
#
# Every timestamp leaves this section as a time-zone-aware value in one zone
# (normally the viewer's), so files from different loggers line up and the
# clock change at the end of summer time cannot lose or duplicate an hour.
# ---------------------------------------------------------------------------
LOCAL_TZ = 'Europe/Berlin'
# Zone names some loggers append to a time, as UTC offsets.
ZONE_WORDS = {'CEST': '+02:00', 'MESZ': '+02:00', 'CET': '+01:00', 'MEZ': '+01:00',
              'BST': '+01:00', 'UTC': '+00:00', 'GMT': '+00:00', 'Z': '+00:00'}
_ZONE_RE = r'\s*\b(CEST|MESZ|CET|MEZ|BST|UTC|GMT)\b\s*(?:([+-])(\d{1,2})(?::?(\d{2}))?)?$'
_OFFSET_RE = r'(?:[+-]\d{2}:?\d{2}|Z)$'
_ISO_RE = r'^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}'
_DMY_RE = r'^(\d{1,2})([./-])(\d{1,2})[./-](\d{2,4})'
_TIME_ONLY_RE = r'^\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?\s*(?:[AaPp]\.?[Mm]\.?)?$'
_DATE_ONLY_RE = r'^(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4})$'


def _valid_zone(tz):
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(str(tz))
        return str(tz)
    except Exception:
        return 'UTC'


def _zone_word_to_offset(text):
    """'18:00 CEST' -> '18:00+02:00'; 'GMT+2' style suffixes become their offset."""
    def replace(match):
        word, sign, hours, minutes = match.group(1).upper(), match.group(2), match.group(3), match.group(4)
        if sign:
            return f'{sign}{int(hours):02d}:{int(minutes or 0):02d}'
        return ZONE_WORDS[word]
    out = text.str.replace(_ZONE_RE, replace, regex=True, case=False)
    return out.str.replace(r'(?<=\d)\s*Z$', '+00:00', regex=True)


def _date_order(text):
    """Decide how to read d/m/y-style dates: 'dayfirst', 'monthfirst' or 'iso'.

    A first field above 12 proves day-first, a second field above 12 proves
    month-first. When every date fits both ways, the reading whose readings
    follow each other most closely wins (a cook from 1 to 2 June is a day long,
    not a month), with day-first as the tie-break.
    """
    sample = text.dropna()
    if sample.empty:
        return 'dayfirst'
    if sample.str.match(_ISO_RE).mean() > 0.5:
        return 'iso'
    parts = sample.str.extract(_DMY_RE)
    if parts[0].isna().all():
        return 'dayfirst'
    first, second = pd.to_numeric(parts[0], errors='coerce'), pd.to_numeric(parts[2], errors='coerce')
    if (first > 12).any():
        return 'dayfirst'
    if (second > 12).any():
        return 'monthfirst'
    if len(sample) > 2000:  # an even sample keeps this quick on long logs
        sample = sample.iloc[np.linspace(0, len(sample) - 1, 2000).astype(int)]
    spans = {}
    for order in ('dayfirst', 'monthfirst'):
        parsed = _parse_text(sample, order, utc=False)
        steps = parsed.diff().dt.total_seconds().abs()
        spans[order] = steps.sum() if parsed.notna().mean() > 0.9 else np.inf
    return 'monthfirst' if spans['monthfirst'] < spans['dayfirst'] else 'dayfirst'


def _parse_text(text, order, utc):
    """Parse strings with one consistent rule for the whole column."""
    from pandas.tseries.api import guess_datetime_format
    if order == 'iso':
        return pd.to_datetime(text, format='ISO8601', errors='coerce', utc=utc)
    dayfirst = order == 'dayfirst'
    first = text.dropna().iloc[0] if text.notna().any() else None
    fmt = guess_datetime_format(first, dayfirst=dayfirst) if first is not None else None
    if fmt:
        parsed = pd.to_datetime(text, format=fmt, errors='coerce', utc=utc)
        if parsed.notna().sum() >= 0.98 * text.notna().sum():
            return parsed
    return pd.to_datetime(text, format='mixed', dayfirst=dayfirst, errors='coerce', utc=utc)


def _localise(naive, tz, notes):
    """Attach the local zone to wall-clock times, resolving the clock change.

    In autumn the hour before 03:00 happens twice. A logger writing local time
    then repeats 02:00-03:00; the first pass is summer time and the second is
    winter time. They are told apart by the order of the rows in the file.
    Times skipped in spring are moved forward.
    """
    if naive.isna().all():
        return naive.dt.tz_localize(tz)
    trial = naive.dt.tz_localize(tz, ambiguous='NaT', nonexistent='NaT')
    unresolved = trial.isna() & naive.notna()
    if not unresolved.any():
        return trial
    flags = np.zeros(len(naive), dtype=bool)
    order = naive.to_numpy()
    descending = (pd.Series(order).diff().dt.total_seconds() < 0).mean() > 0.5
    positions = range(len(order) - 1, -1, -1) if descending else range(len(order))
    summer, seen, previous = True, set(), None
    for i in positions:
        if not unresolved.iloc[i]:
            continue
        value = order[i]
        if previous is not None and (value < previous or value in seen):
            summer = False
        flags[i] = summer
        seen.add(value)
        previous = value
    result = naive.dt.tz_localize(tz, ambiguous=flags, nonexistent='shift_forward')
    moment = naive[unresolved].iloc[0]
    notes.append(f'Clock change on {moment:%d/%m/%Y} handled: readings are kept in their real order, so no hour is lost or counted twice.')
    return result


def parse_timestamps(s, tz=LOCAL_TZ):
    """Timestamps as time-zone-aware values in tz, plus notes on how they were read.

    Handles ISO dates (2026-06-01), day-first and month-first dates (decided
    per column, never per value), offsets (+02:00, Z), zone names (CEST, CET,
    MEZ, UTC), Excel serial dates and native spreadsheet date-times. Times
    without dates roll over to the next day at midnight.
    """
    tz = _valid_zone(tz)
    notes = []
    if pd.api.types.is_datetime64_any_dtype(s):
        parsed = pd.to_datetime(s, errors='coerce')
        if parsed.dt.tz is not None:
            return parsed.dt.tz_convert(tz), notes
        return _localise(parsed, tz, notes), notes

    numeric = pd.to_numeric(s, errors='coerce') if not pd.api.types.is_numeric_dtype(s) else s.astype(float)
    if numeric.notna().mean() > 0.9 and numeric.dropna().between(20000, 100000).mean() > 0.9:
        parsed = pd.to_datetime(numeric, unit='D', origin='1899-12-30', errors='coerce').dt.round('s')
        notes.append('Times read as spreadsheet date numbers.')
        return _localise(parsed, tz, notes), notes
    if pd.api.types.is_numeric_dtype(s):
        return pd.Series(pd.NaT, index=s.index, dtype=f'datetime64[ns, {tz}]'), notes

    text = s.astype('string').str.strip().replace('', pd.NA)
    if text.dropna().str.match(_TIME_ONLY_RE).mean() > 0.9:
        times = pd.to_datetime(text, format='mixed', errors='coerce')
        clock = times - times.dt.normalize()
        rollover = (clock.diff() < -pd.Timedelta(hours=12)).cumsum()
        base = pd.Timestamp.now(tz=tz).normalize().tz_localize(None)
        parsed = base + clock + pd.to_timedelta(rollover, unit='D')
        notes.append('The file has times but no dates. The first reading is placed on today\'s date and each midnight starts a new day.')
        return _localise(parsed, tz, notes), notes

    text = _zone_word_to_offset(text)
    has_offset = text.dropna().str.contains(_OFFSET_RE, regex=True).mean() > 0.5
    order = _date_order(text)
    parsed = _parse_text(text, order, utc=has_offset)
    if order == 'monthfirst':
        notes.append('Dates read as month/day/year.')
    if has_offset:
        notes.append(f'Times carry a time zone and are shown in {tz}.' if tz != 'UTC' else 'Times carry a time zone and are shown in UTC.')
        return parsed.dt.tz_convert(tz), notes
    return _localise(parsed, tz, notes), notes


def parse_ts(s, tz=LOCAL_TZ):
    """Timestamps as time-zone-aware values in tz (see parse_timestamps)."""
    return parse_timestamps(s, tz)[0]

def parse_temp(s):
    """Temperatures as floats. Accepts 23.5, 23,5, '23,5 °C' and 1.023,5 / 1,023.5."""
    if pd.api.types.is_numeric_dtype(s): return pd.to_numeric(s,errors='coerce').astype('float64')
    text=s.astype('string').str.replace(r'°\s*C|celsius','',regex=True,case=False).str.replace(r'\s+','',regex=True)
    comma=text.str.rfind(','); dot=text.str.rfind('.')
    comma_decimal=(comma>dot).fillna(False)  # the last separator is the decimal mark
    text=text.where(~comma_decimal,text.str.replace('.','',regex=False).str.replace(',','.',regex=False))
    text=text.where(comma_decimal,text.str.replace(',','',regex=False))
    return pd.to_numeric(text,errors='coerce').astype('float64')

# ---------------------------------------------------------------------------
# Reading files
# ---------------------------------------------------------------------------
COMBINED_TIME_COLUMN = 'Date time (combined)'


def _decode(raw):
    """Text of a CSV file: UTF-8 (with or without BOM), else Windows-1252, else Latin-1."""
    for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1', errors='replace')


def merge_date_time_columns(df):
    """Join a date-only column and a time-only column into one timestamp column.

    Some loggers write the date and the time in separate columns. On its own the
    time column wraps at midnight, so the two are combined first.
    """
    def kind(column):
        values = df[column].dropna()
        if values.empty:
            return None
        if pd.api.types.is_datetime64_any_dtype(values):
            stamps = pd.to_datetime(values)
            return 'date' if (stamps == stamps.dt.normalize()).all() else None
        if values.map(lambda v: hasattr(v, 'hour') and not hasattr(v, 'year')).mean() > 0.9:
            return 'time'
        text = values.astype(str).str.strip()
        if text.str.match(_TIME_ONLY_RE).mean() > 0.9:
            return 'time'
        if text.str.match(_DATE_ONLY_RE).mean() > 0.9:
            return 'date'
        return None
    kinds = {column: kind(column) for column in df.columns}
    dates = [c for c, k in kinds.items() if k == 'date']
    times = [c for c, k in kinds.items() if k == 'time']
    if not dates or not times or COMBINED_TIME_COLUMN in df.columns:
        return df
    date_values = df[dates[0]]
    if pd.api.types.is_datetime64_any_dtype(date_values):
        date_text = pd.to_datetime(date_values).dt.strftime('%Y-%m-%d')
    else:
        date_text = date_values.astype('string').str.strip()
    time_text = df[times[0]].map(lambda v: v.strftime('%H:%M:%S') if hasattr(v, 'strftime') else v).astype('string').str.strip()
    combined = df.copy()
    combined.insert(0, COMBINED_TIME_COLUMN, date_text + ' ' + time_text)
    return combined.drop(columns=[dates[0], times[0]])


def read_file(file_obj, name):
    """All sheets of an uploaded file as DataFrames, keyed by sheet name ('CSV' for a CSV file)."""
    raw = file_obj.read(); file_obj.seek(0) if hasattr(file_obj, 'seek') else None
    ext = Path(name).suffix.lower()
    if ext == '.csv':
        text = _decode(raw)
        if not text.strip():
            raise ValueError('the file is empty.')
        try:
            frame = pd.read_csv(StringIO(text), sep=None, engine='python')
        except Exception:
            frame = pd.read_csv(StringIO(text))
        if frame.shape[1] < 2:
            raise ValueError('the file needs a time column and at least one temperature column, separated by commas, semicolons or tabs.')
        return {'CSV': merge_date_time_columns(frame)}
    if ext in {'.xlsx', '.xlsm', '.xls'}:
        return {sheet: merge_date_time_columns(frame) for sheet, frame in pd.read_excel(BytesIO(raw), sheet_name=None).items()}
    raise ValueError('Supported formats are XLSX, XLSM, XLS and CSV.')


TIME_NAMES = ('timestamp', 'datetime', 'date time', 'date/time', 'date_time', 'zeitstempel', 'datum/zeit', 'datum zeit')


def detect_columns(df):
    """The timestamp column and the likely meat-probe columns."""
    columns = list(df.columns); lowered = {c: str(c).lower() for c in columns}
    timestamp = next((c for c in columns if any(x in lowered[c] for x in TIME_NAMES)), None)
    if timestamp is None:
        scores = [(parse_ts(df[c].head(300)).notna().mean(), index, c) for index, c in enumerate(columns)]
        score, _, timestamp = max(scores, key=lambda x: (x[0], -x[1]))
        if score < .25: raise ValueError('no time column was found. The file needs a column of dates and times (or separate date and time columns).')
    probes = [c for c in columns if c != timestamp and any(x in lowered[c] for x in ('probe', 'meat', 'internal', 'flat', 'point', 'food', 'brisket', 'kern', 'fleisch')) and not any(x in lowered[c] for x in ('pit', 'ambient', 'grate', 'controller', 'target', 'setpoint', 'set point'))]
    if not probes:
        scored = []
        for c in columns:
            if c == timestamp: continue
            values = parse_temp(df[c].head(300)); scored.append((float(values.between(-20, 150).mean()), c))
        probes = [c for score, c in sorted(scored, key=lambda x: -x[0]) if score >= .25]
    if not probes: raise ValueError('no temperature column was found.')
    return timestamp, probes


def prepare(df, timestamp_col, probe_col, tz=LOCAL_TZ, timestamps=None):
    """Valid readings of one probe column. timestamps: the already-parsed time column, to avoid parsing it again."""
    stamps = timestamps if timestamps is not None else parse_ts(df[timestamp_col], tz)
    raw = pd.DataFrame({'timestamp': stamps, 'temperature_c': parse_temp(df[probe_col])})
    mask = raw.timestamp.notna() & raw.temperature_c.between(-20, 150); valid = raw[mask].copy().sort_values('timestamp', kind='stable')
    duplicates = int(valid.duplicated('timestamp', keep='last').sum()); valid = valid.drop_duplicates('timestamp', keep='last').reset_index(drop=True)
    return valid, ParseReport(len(raw), len(valid), int((~mask).sum()), duplicates, valid.timestamp.min() if len(valid) else None, valid.timestamp.max() if len(valid) else None)

MIN_HOLD_AFTER_PEAK_S = 30 * 60  # readings after the peak needed to call it a hold or rest
SMOOTH_WINDOW_S = 15 * 60        # rolling-median window used to find the peak
EDGE_WINDOW_S = 10 * 60          # first/last stretch used for the start and end temperatures

def classify_session(valid):
    if len(valid)<5: raise ValueError('At least five valid readings are required.')
    # Smoothing and the start/end levels are measured in time, not in readings, so the
    # result is the same at 2-second and 15-minute logging and a short rest is not averaged away.
    step=float(valid.timestamp.diff().dt.total_seconds().median() or 60)
    span=int(min(61,max(3,round(SMOOTH_WINDOW_S/step))))|1
    smooth=valid.temperature_c.astype(float).rolling(span,center=True,min_periods=1).median().reset_index(drop=True)
    edge=int(max(3,min(len(smooth)//4,round(EDGE_WINDOW_S/step))))
    peak_i=int(smooth.idxmax()); peak=float(smooth.iloc[peak_i]); raw=valid.temperature_c.astype(float).reset_index(drop=True); start=float(raw.iloc[:edge].median()); end=float(raw.iloc[-edge:].median()); rise=peak-start; fall=peak-end; fraction=peak_i/max(len(smooth)-1,1); pull=pd.Timestamp(valid.timestamp.iloc[peak_i])
    # A cook and hold needs a real rise, then at least MIN_HOLD_AFTER_PEAK of readings after the peak
    # with a clear fall. Time after the peak, not its position in the session, decides, so a long
    # cook with a one-hour rest is still a cook and hold.
    after_peak=(valid.timestamp.iloc[-1]-valid.timestamp.iloc[peak_i]).total_seconds()
    if rise>=25 and fall>=8 and fraction>=.15 and after_peak>=MIN_HOLD_AFTER_PEAK_S: return Detection('Cook + Hold',95,pull,peak,float(smooth.mean()),float(smooth.min()),'A substantial temperature rise is followed by a sustained cooling phase.')
    if rise<15 and fall>=8 and fraction<=.25:
        duration=(valid.timestamp.iloc[-1]-valid.timestamp.iloc[0]).total_seconds()/3600; label='Calibration / Hold Test' if duration>=8 and end>=55 else 'Hold Only'; return Detection(label,90,None,peak,float(smooth.mean()),float(smooth.min()),'No clear cooking rise was detected; the profile begins hot and cools over time.')
    if rise>=20 and fall<8: return Detection('Cook Only',90,None,peak,float(smooth.mean()),float(smooth.min()),'The profile rises substantially and ends without a sustained cooling phase.')
    return Detection('Uncertain',55,pull if fall>=8 else None,peak,float(smooth.mean()),float(smooth.min()),'The profile is ambiguous; review the classification.')

def band(temp): return -1 if temp<60 else min(int(np.searchsorted(BAND_LOWER_C,temp,side='right')-1),9)
def assess(value):
    p=value*100
    return 'Underdone and tight' if p<80 else 'Slightly tight but sliceable' if p<95 else 'Ideal tenderness' if p<=105 else 'Very soft and potentially overdone' if p<=120 else 'Increased risk of mushy or over-rendered texture'

HOLD_MODES = ('Hold Only', 'Calibration / Hold Test')


def analyse(valid, report, detection, pull_override=None, max_gap=10):
    """Rendering over every interval between consecutive readings, all at once.

    Each interval uses the temperature at its start. Intervals that are not
    positive or longer than max_gap (seconds) are excluded; below 60 C nothing
    is added. When a transfer time is known (pull_override, or the profile's
    own detected pull) every interval is split at it into Cook and Hold;
    without one, Cook Only and Hold Only profiles count wholly as that phase.
    """
    mode = detection.session_type
    pull = pd.Timestamp(pull_override) if pull_override is not None else detection.pull_timestamp
    stamps = valid['timestamp'].reset_index(drop=True)
    temps = valid['temperature_c'].to_numpy(dtype=float)
    if len(stamps) >= 2:
        first = stamps.iloc[0]
        offsets = (stamps - first).dt.total_seconds().to_numpy()
    else:
        first, offsets = (stamps.iloc[0] if len(stamps) else None), np.zeros(len(stamps))
    elapsed = np.diff(offsets)
    temp = temps[:-1] if len(temps) else temps
    starts = offsets[:-1] if len(offsets) else offsets

    not_positive = elapsed <= 0
    gap = ~not_positive & (elapsed > max_gap)
    counted = ~not_positive & ~gap
    band_index = np.where(temp < 60, -1, np.minimum(np.searchsorted(BAND_LOWER_C, temp, side='right') - 1, 9))
    below = counted & (band_index < 0)
    in_model = counted & (band_index >= 0)
    positive = np.maximum(elapsed, 0.0)

    unclassified = np.zeros(len(elapsed), dtype=bool)
    if pull is not None:
        cook_seconds = np.minimum(np.maximum((pull - first).total_seconds() - starts, 0.0), positive)
        hold_seconds = positive - cook_seconds
    elif mode == 'Cook Only':
        cook_seconds, hold_seconds = positive, np.zeros(len(elapsed))
    elif mode in HOLD_MODES:
        cook_seconds, hold_seconds = np.zeros(len(elapsed)), positive
    else:
        cook_seconds = hold_seconds = np.zeros(len(elapsed))
        unclassified = in_model
    cook_seconds = np.where(in_model, cook_seconds, 0.0)
    hold_seconds = np.where(in_model, hold_seconds, 0.0)

    rate = ZONE_RATES[np.clip(band_index, 0, 9)]
    increment = np.where(in_model, (cook_seconds + hold_seconds) / 3600 * rate, 0.0)
    seconds = np.zeros((2, 10))
    if in_model.any():
        seconds[0] = np.bincount(band_index[in_model], weights=cook_seconds[in_model], minlength=10)
        seconds[1] = np.bincount(band_index[in_model], weights=hold_seconds[in_model], minlength=10)

    status = np.select([not_positive, gap, below, unclassified],
                       ['Excluded: non-positive interval', 'Excluded: recording gap', 'Below model range', 'Unclassified interval'],
                       'Analysed')
    phase = np.select([in_model & (cook_seconds > 0) & (hold_seconds > 0), in_model & (cook_seconds > 0), in_model & (hold_seconds > 0)],
                      ['Cook → Hold / cooldown', 'Cook', 'Hold / cooldown'], '')
    timeline = pd.DataFrame({
        'Timestamp': stamps.iloc[:-1].reset_index(drop=True) if len(stamps) else stamps,
        'Temperature °C': temp,
        'Next timestamp': stamps.iloc[1:].reset_index(drop=True) if len(stamps) else stamps,
        'Elapsed seconds': elapsed,
        'Phase': phase,
        'Status': status,
        'Incremental rendering': increment,
        'Accumulated rendering': np.cumsum(increment),
    })

    records = []
    for pi, name in enumerate(('Cook', 'Hold / cooldown')):
        for bi in range(10):
            hours = seconds[pi, bi] / 3600
            records.append({'Phase': name, 'Band': bi + 1, 'Temperature range': BAND_LABELS[bi], 'Duration hours': hours,
                            'Rate per hour': ZONE_RATES[bi], 'Tenderness contribution': hours * ZONE_RATES[bi]})
    summary = pd.DataFrame(records)
    cook = float(summary[summary.Phase == 'Cook']['Tenderness contribution'].sum())
    hold = float(summary[summary.Phase == 'Hold / cooldown']['Tenderness contribution'].sum())
    total = cook + hold
    # Complete = a real cook and hold: readings on both sides of a transfer that was either
    # detected as Cook + Hold or set for the whole session (automatic or by hand).
    complete = bool(pull is not None and (mode == 'Cook + Hold' or pull_override is not None)
                    and len(stamps) >= 2 and stamps.iloc[0] < pull < stamps.iloc[-1])
    return Result(summary, timeline, report, detection, int(gap.sum()), float(elapsed[gap].sum() / 3600),
                  float(elapsed[below].sum() / 3600), float(seconds.sum() / 3600), cook, hold, total, assess(total), complete)

def combine_probes(streams):
    """Merge several probes at one physical location into one profile.

    streams: list of (frame with timestamp and temperature_c, tolerance in seconds).
    Every reading time of every probe becomes a point on a shared grid. At each
    point, each probe contributes its latest reading if that reading is no older
    than the probe's tolerance (its gap threshold); the contributions are averaged.
    Probes that sample at different seconds are therefore averaged instead of
    interleaved, and nothing is filled in across a gap that every probe shares.
    Returns timestamp, temperature_c and contributing_probes.
    """
    prepared=[]
    for i,(frame,tolerance) in enumerate(streams):
        f=frame[['timestamp','temperature_c']].dropna().copy()
        f['timestamp']=pd.to_datetime(f['timestamp']).dt.as_unit('ns')
        prepared.append((f.sort_values('timestamp').rename(columns={'temperature_c':f'p{i}'}),float(tolerance)))
    if not prepared: return pd.DataFrame(columns=['timestamp','temperature_c','contributing_probes'])
    grid=pd.DataFrame({'timestamp':pd.concat([f.timestamp for f,_ in prepared]).drop_duplicates().sort_values().reset_index(drop=True)})
    for f,tolerance in prepared:
        grid=pd.merge_asof(grid,f,on='timestamp',direction='backward',tolerance=pd.Timedelta(seconds=tolerance))
    values=grid[[f'p{i}' for i in range(len(prepared))]]
    out=pd.DataFrame({'timestamp':grid.timestamp,'temperature_c':values.mean(axis=1,skipna=True),'contributing_probes':values.notna().sum(axis=1)})
    return out[out.contributing_probes>0].reset_index(drop=True)
