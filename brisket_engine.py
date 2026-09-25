from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
import re
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
    if pd.api.types.is_numeric_dtype(s): return pd.to_numeric(s,errors='coerce')
    text=s.astype('string').str.replace('°C','',regex=False,case=False).str.replace('Celsius','',regex=False,case=False).str.strip()
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
    probes=[c for c in columns if c!=timestamp and any(x in lowered[c] for x in ('probe','temperature','temp','flat','point','celsius','°c')) and not any(x in lowered[c] for x in ('pit','ambient','target','setpoint'))]
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
    mask=raw.timestamp.notna()&raw.temperature_c.between(-20,150)
    valid=raw[mask].copy().sort_values('timestamp',kind='stable')
    duplicates=int(valid.duplicated('timestamp',keep='last').sum()); valid=valid.drop_duplicates('timestamp',keep='last').reset_index(drop=True)
    report=ParseReport(len(raw),len(valid),int((~mask).sum()),duplicates,valid.timestamp.min() if len(valid) else None,valid.timestamp.max() if len(valid) else None)
    return valid,report

def _smooth(valid):
    if len(valid)<7: return valid.temperature_c.astype(float)
    span=max(5,min(61,len(valid)//30*2+1)); return valid.temperature_c.astype(float).rolling(span,center=True,min_periods=1).median()

def classify_session(valid):
    if len(valid)<5: raise ValueError('At least five valid readings are required for automatic classification.')
    smooth=_smooth(valid).reset_index(drop=True); peak_i=int(smooth.idxmax()); peak=float(smooth.iloc[peak_i]); minimum=float(smooth.min()); average=float(smooth.mean())
    edge=max(2,len(smooth)//20); start=float(smooth.iloc[:edge].median()); end=float(smooth.iloc[-edge:].median()); rise=peak-start; fall=peak-end; peak_fraction=peak_i/max(len(smooth)-1,1)
    post=max(len(smooth)-peak_i-1,1); cooling_share=float((smooth.iloc[peak_i:].diff().fillna(0)<0).sum()/post); pull=pd.Timestamp(valid.timestamp.iloc[peak_i])
    if rise>=25 and fall>=8 and .15<=peak_fraction<=.9:
        confidence=int(min(99,65+min(rise,40)*.45+min(fall,25)*.7+cooling_share*8)); return Detection('Cook + Hold',confidence,pull,peak,average,minimum,'A substantial temperature rise is followed by a sustained cooling phase.')
    if rise<15 and fall>=8 and peak_fraction<=.25:
        duration=(valid.timestamp.iloc[-1]-valid.timestamp.iloc[0]).total_seconds()/3600; label='Calibration / Hold Test' if duration>=8 and end>=55 else 'Hold Only'; confidence=int(min(99,72+min(fall,25)*.7+cooling_share*10)); return Detection(label,confidence,None,peak,average,minimum,'No clear cooking rise was detected; the profile begins hot and cools over time.')
    if rise>=20 and fall<8:
        confidence=int(min(99,72+min(rise,45)*.5)); return Detection('Cook Only',confidence,None,peak,average,minimum,'The profile rises substantially and recording ends without a sustained cooling phase.')
    return Detection('Uncertain',55,pull if fall>=8 else None,peak,average,minimum,'The profile does not clearly match Cook Only, Hold Only, or Cook + Hold.')

def band(temp): return -1 if temp<60 else min(int(np.searchsorted(BAND_LOWER_C,temp,side='right')-1),9)
def assess(value):
    percent=value*100
    return 'Underdone and tight' if percent<80 else 'Slightly tight but sliceable' if percent<95 else 'Ideal tenderness' if percent<=105 else 'Very soft and potentially overdone' if percent<=120 else 'Increased risk of mushy or over-rendered texture'

def analyse(valid,report,detection,pull_override=None,max_gap=10):
    pull=pd.Timestamp(pull_override) if pull_override is not None else detection.pull_timestamp; mode=detection.session_type; seconds=np.zeros((2,10)); below=gap_seconds=0.; gaps=0; cumulative=0.; rows=[]
    for i in range(len(valid)-1):
        start=pd.Timestamp(valid.timestamp.iloc[i]); end=pd.Timestamp(valid.timestamp.iloc[i+1]); elapsed=(end-start).total_seconds(); temp=float(valid.temperature_c.iloc[i]); status='Analysed'; phase=''; increment=0.
        if elapsed<=0: status='Excluded: non-positive interval'
        elif elapsed>max_gap: status='Excluded: recording gap'; gaps+=1; gap_seconds+=elapsed
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
                        cook_seconds=max((pull-start).total_seconds(),0); parts=[(0,cook_seconds,'Cook'),(1,elapsed-cook_seconds,'Hold / cooldown')]
                else: parts=[]; status='Unclassified interval'
                for phase_i,duration,label in parts:
                    if duration>0: seconds[phase_i,bi]+=duration; increment+=duration/3600*ZONE_RATES[bi]
                phase=' → '.join(dict.fromkeys(x[2] for x in parts))
        cumulative+=increment; rows.append({'Timestamp':start,'Temperature °C':temp,'Next timestamp':end,'Elapsed seconds':elapsed,'Phase':phase,'Status':status,'Incremental rendering':increment,'Accumulated rendering':cumulative})
    records=[]
    for phase_i,phase_name in enumerate(('Cook','Hold / cooldown')):
        for bi in range(10):
            hours=seconds[phase_i,bi]/3600; records.append({'Phase':phase_name,'Band':bi+1,'Temperature range':BAND_LABELS[bi],'Duration hours':hours,'Rate per hour':ZONE_RATES[bi],'Tenderness contribution':hours*ZONE_RATES[bi]})
    summary=pd.DataFrame(records); cook=float(summary[summary.Phase=='Cook']['Tenderness contribution'].sum()); hold=float(summary[summary.Phase=='Hold / cooldown']['Tenderness contribution'].sum()); total=cook+hold; complete=mode=='Cook + Hold' and pull is not None
    return Result(summary,pd.DataFrame(rows),report,detection,gaps,gap_seconds/3600,below/3600,float(seconds.sum()/3600),cook,hold,total,assess(total),complete)

def demo_data():
    start=datetime(2026,5,23); minutes=np.arange(0,23*60+1); hours=minutes/60; base=np.interp(hours,np.array([0,1,2,4,6,7.5,9,10,12,16,20,22.75,23]),np.array([8,32,48,62,72,82,90,95,94,91,86,67,65.5]))
    return pd.DataFrame({'timestamp':[start+timedelta(minutes=int(m)) for m in minutes],'Average Probe Temperature (°C)':np.round(base+.15*np.sin(minutes/40),2)})

def to_excel(results):
    output=BytesIO()
    with pd.ExcelWriter(output,engine='openpyxl') as writer:
        overview=[]
        for name,result in results.items():
            detection=result.detection; overview.append({'Probe':name,'Session type':detection.session_type,'Confidence':detection.confidence/100,'Detected pull':detection.pull_timestamp,'Peak °C':detection.peak_temperature,'Average °C':detection.average_temperature,'Minimum °C':detection.minimum_temperature,'Analysed hours':result.analysed_hours,'Cook rendering':result.cook,'Hold rendering':result.hold,'Total rendering':result.total,'Assessment':result.assessment if result.complete else 'Partial session'})
            safe=re.sub(r'[^A-Za-z0-9 _-]','',name)[:18] or 'Probe'; result.summary.to_excel(writer,sheet_name=(safe+' Summary')[:31],index=False); result.timeline.to_excel(writer,sheet_name=(safe+' Timeline')[:31],index=False)
        pd.DataFrame(overview).to_excel(writer,sheet_name='Overview',index=False)
        for ws in writer.book.worksheets:
            ws.freeze_panes='A2'
            for col in ws.columns: ws.column_dimensions[col[0].column_letter].width=min(max(len(str(cell.value or '')) for cell in col)+2,42)
    return output.getvalue()
