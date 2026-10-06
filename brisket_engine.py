from __future__ import annotations
from dataclasses import dataclass
from io import BytesIO
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

def parse_ts(s):
    if pd.api.types.is_datetime64_any_dtype(s): return pd.to_datetime(s,errors='coerce')
    text=s.astype('string').str.strip().str.replace(r'\s+(CEST|CET|UTC|GMT)$','',regex=True,case=False).str.replace('T',' ',regex=False)
    parsed=pd.to_datetime(text,errors='coerce',dayfirst=True)
    numeric=pd.to_numeric(s,errors='coerce'); mask=parsed.isna()&numeric.between(20000,100000)
    if mask.any(): parsed.loc[mask]=pd.to_datetime(numeric.loc[mask],unit='D',origin='1899-12-30',errors='coerce')
    return parsed

def parse_temp(s):
    """Temperatures as floats. Accepts 23.5, 23,5, '23,5 °C' and 1.023,5 / 1,023.5."""
    if pd.api.types.is_numeric_dtype(s): return pd.to_numeric(s,errors='coerce')
    text=s.astype('string').str.replace(r'°\s*C|celsius','',regex=True,case=False).str.replace(r'\s+','',regex=True)
    comma=text.str.rfind(','); dot=text.str.rfind('.')
    comma_decimal=(comma>dot).fillna(False)  # the last separator is the decimal mark
    text=text.where(~comma_decimal,text.str.replace('.','',regex=False).str.replace(',','.',regex=False))
    text=text.where(comma_decimal,text.str.replace(',','',regex=False))
    return pd.to_numeric(text,errors='coerce')

def read_file(file_obj,name):
    data=BytesIO(file_obj.read()); file_obj.seek(0); ext=Path(name).suffix.lower()
    if ext=='.csv': return {'CSV':pd.read_csv(data,sep=None,engine='python')}
    if ext in {'.xlsx','.xlsm','.xls'}: return pd.read_excel(data,sheet_name=None)
    raise ValueError('Supported formats are XLSX, XLSM, XLS and CSV.')

def detect_columns(df):
    columns=list(df.columns); lowered={c:str(c).lower() for c in columns}
    timestamp=next((c for c in columns if any(x in lowered[c] for x in ('timestamp','datetime','date time'))),None)
    if timestamp is None:
        score,timestamp=max((parse_ts(df[c].head(300)).notna().mean(),c) for c in columns)
        if score<.25: raise ValueError('Timestamp column not detected.')
    probes=[c for c in columns if c!=timestamp and any(x in lowered[c] for x in ('probe','meat','internal','flat','point','food','brisket')) and not any(x in lowered[c] for x in ('pit','ambient','grate','controller','target','setpoint'))]
    if not probes:
        scored=[]
        for c in columns:
            if c==timestamp: continue
            values=parse_temp(df[c].head(300)); scored.append((values.between(-20,150).mean(),c))
        probes=[c for score,c in sorted(scored,reverse=True) if score>=.25]
    if not probes: raise ValueError('Meat-temperature column not detected.')
    return timestamp,probes

def prepare(df,timestamp_col,probe_col):
    raw=pd.DataFrame({'timestamp':parse_ts(df[timestamp_col]),'temperature_c':parse_temp(df[probe_col])})
    mask=raw.timestamp.notna()&raw.temperature_c.between(-20,150); valid=raw[mask].copy().sort_values('timestamp',kind='stable')
    duplicates=int(valid.duplicated('timestamp',keep='last').sum()); valid=valid.drop_duplicates('timestamp',keep='last').reset_index(drop=True)
    return valid,ParseReport(len(raw),len(valid),int((~mask).sum()),duplicates,valid.timestamp.min() if len(valid) else None,valid.timestamp.max() if len(valid) else None)

def classify_session(valid):
    if len(valid)<5: raise ValueError('At least five valid readings are required.')
    span=max(5,min(61,len(valid)//30*2+1)); smooth=valid.temperature_c.astype(float).rolling(span,center=True,min_periods=1).median().reset_index(drop=True)
    peak_i=int(smooth.idxmax()); peak=float(smooth.iloc[peak_i]); edge=max(2,len(smooth)//20); start=float(smooth.iloc[:edge].median()); end=float(smooth.iloc[-edge:].median()); rise=peak-start; fall=peak-end; fraction=peak_i/max(len(smooth)-1,1); pull=pd.Timestamp(valid.timestamp.iloc[peak_i])
    if rise>=25 and fall>=8 and .15<=fraction<=.9: return Detection('Cook + Hold',95,pull,peak,float(smooth.mean()),float(smooth.min()),'A substantial temperature rise is followed by a sustained cooling phase.')
    if rise<15 and fall>=8 and fraction<=.25:
        duration=(valid.timestamp.iloc[-1]-valid.timestamp.iloc[0]).total_seconds()/3600; label='Calibration / Hold Test' if duration>=8 and end>=55 else 'Hold Only'; return Detection(label,90,None,peak,float(smooth.mean()),float(smooth.min()),'No clear cooking rise was detected; the profile begins hot and cools over time.')
    if rise>=20 and fall<8: return Detection('Cook Only',90,None,peak,float(smooth.mean()),float(smooth.min()),'The profile rises substantially and ends without a sustained cooling phase.')
    return Detection('Uncertain',55,pull if fall>=8 else None,peak,float(smooth.mean()),float(smooth.min()),'The profile is ambiguous; review the classification.')

def band(temp): return -1 if temp<60 else min(int(np.searchsorted(BAND_LOWER_C,temp,side='right')-1),9)
def assess(value):
    p=value*100
    return 'Underdone and tight' if p<80 else 'Slightly tight but sliceable' if p<95 else 'Ideal tenderness' if p<=105 else 'Very soft and potentially overdone' if p<=120 else 'Increased risk of mushy or over-rendered texture'

def analyse(valid,report,detection,pull_override=None,max_gap=10):
    pull=pd.Timestamp(pull_override) if pull_override is not None else detection.pull_timestamp; mode=detection.session_type; seconds=np.zeros((2,10)); below=gapseconds=0.; gaps=0; cumulative=0.; rows=[]
    for i in range(len(valid)-1):
        start=pd.Timestamp(valid.timestamp.iloc[i]); end=pd.Timestamp(valid.timestamp.iloc[i+1]); elapsed=(end-start).total_seconds(); temp=float(valid.temperature_c.iloc[i]); status='Analysed'; phase=''; increment=0.
        if elapsed<=0: status='Excluded: non-positive interval'
        elif elapsed>max_gap: status='Excluded: recording gap'; gaps+=1; gapseconds+=elapsed
        else:
            bi=band(temp)
            if bi<0: status='Below model range'; below+=elapsed
            else:
                if mode=='Cook Only': parts=[(0,elapsed,'Cook')]
                elif mode in ('Hold Only','Calibration / Hold Test'): parts=[(1,elapsed,'Hold / cooldown')]
                elif pull is not None:
                    if end<=pull: parts=[(0,elapsed,'Cook')]
                    elif start>=pull: parts=[(1,elapsed,'Hold / cooldown')]
                    else:
                        cs=max((pull-start).total_seconds(),0); parts=[(0,cs,'Cook'),(1,elapsed-cs,'Hold / cooldown')]
                else: parts=[]; status='Unclassified interval'
                for pi,dur,label in parts:
                    if dur>0: seconds[pi,bi]+=dur; increment+=dur/3600*ZONE_RATES[bi]
                phase=' → '.join(dict.fromkeys(x[2] for x in parts))
        cumulative+=increment; rows.append({'Timestamp':start,'Temperature °C':temp,'Next timestamp':end,'Elapsed seconds':elapsed,'Phase':phase,'Status':status,'Incremental rendering':increment,'Accumulated rendering':cumulative})
    records=[]
    for pi,name in enumerate(('Cook','Hold / cooldown')):
        for bi in range(10):
            hours=seconds[pi,bi]/3600; records.append({'Phase':name,'Band':bi+1,'Temperature range':BAND_LABELS[bi],'Duration hours':hours,'Rate per hour':ZONE_RATES[bi],'Tenderness contribution':hours*ZONE_RATES[bi]})
    summary=pd.DataFrame(records); cook=float(summary[summary.Phase=='Cook']['Tenderness contribution'].sum()); hold=float(summary[summary.Phase=='Hold / cooldown']['Tenderness contribution'].sum()); total=cook+hold
    return Result(summary,pd.DataFrame(rows),report,detection,gaps,gapseconds/3600,below/3600,float(seconds.sum()/3600),cook,hold,total,assess(total),mode=='Cook + Hold' and pull is not None)

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
        f['timestamp']=pd.to_datetime(f['timestamp']).astype('datetime64[ns]')
        prepared.append((f.sort_values('timestamp').rename(columns={'temperature_c':f'p{i}'}),float(tolerance)))
    if not prepared: return pd.DataFrame(columns=['timestamp','temperature_c','contributing_probes'])
    grid=pd.DataFrame({'timestamp':pd.concat([f.timestamp for f,_ in prepared]).drop_duplicates().sort_values().reset_index(drop=True)})
    for f,tolerance in prepared:
        grid=pd.merge_asof(grid,f,on='timestamp',direction='backward',tolerance=pd.Timedelta(seconds=tolerance))
    values=grid[[f'p{i}' for i in range(len(prepared))]]
    out=pd.DataFrame({'timestamp':grid.timestamp,'temperature_c':values.mean(axis=1,skipna=True),'contributing_probes':values.notna().sum(axis=1)})
    return out[out.contributing_probes>0].reset_index(drop=True)
