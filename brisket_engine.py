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
    peak_temperature:float; average_temperature:float; minimum_temperature:float
    reason:str

@dataclass(frozen=True)
class Result:
    summary:pd.DataFrame; timeline:pd.DataFrame; report:ParseReport; detection:Detection
    excluded_gaps:int; excluded_gap_hours:float; below_model_hours:float; analysed_hours:float
    cook:float; hold:float; total:float; assessment:str; complete:bool

def parse_ts(s):
    if pd.api.types.is_datetime64_any_dtype(s): return pd.to_datetime(s,errors='coerce')
    text=s.astype('string').str.strip().str.replace(r'\s+(CEST|CET|UTC|GMT)$','',regex=True,case=False).str.replace('T',' ',regex=False)
    p=pd.to_datetime(text,errors='coerce',dayfirst=True)
    n=pd.to_numeric(s,errors='coerce'); m=p.isna()&n.between(20000,100000)
    if m.any(): p.loc[m]=pd.to_datetime(n.loc[m],unit='D',origin='1899-12-30',errors='coerce')
    return p

def parse_temp(s):
    if pd.api.types.is_numeric_dtype(s): return pd.to_numeric(s,errors='coerce')
    text=s.astype('string').str.replace('°C','',regex=False,case=False).str.replace('Celsius','',regex=False,case=False).str.strip()
    return pd.to_numeric(text,errors='coerce')

def read_file(f,name):
    data=BytesIO(f.read()); f.seek(0); ext=Path(name).suffix.lower()
    if ext=='.csv': return {'CSV':pd.read_csv(data,sep=None,engine='python')}
    if ext in {'.xlsx','.xlsm','.xls'}: return pd.read_excel(data,sheet_name=None)
    raise ValueError('Supported formats are XLSX, XLSM, XLS and CSV.')

def detect_columns(df):
    cols=list(df.columns); low={c:str(c).lower() for c in cols}
    t=next((c for c in cols if any(x in low[c] for x in ('timestamp','datetime','date time'))),None)
    if t is None:
        scored=[(parse_ts(df[c].head(300)).notna().mean(),c) for c in cols]; score,t=max(scored)
        if score<.25: raise ValueError('Timestamp column not detected.')
    probes=[c for c in cols if c!=t and any(x in low[c] for x in ('probe','temperature','temp','flat','point','celsius','°c')) and not any(x in low[c] for x in ('pit','ambient','target','setpoint'))]
    if not probes:
        scored=[]
        for c in cols:
            if c==t: continue
            v=parse_temp(df[c].head(300)); scored.append((v.between(-20,150).mean(),c))
        probes=[c for score,c in sorted(scored,reverse=True) if score>=.25]
    if not probes: raise ValueError('Meat-temperature column not detected.')
    return t,probes

def prepare(df,tcol,pcol):
    raw=pd.DataFrame({'timestamp':parse_ts(df[tcol]),'temperature_c':parse_temp(df[pcol])})
    mask=raw.timestamp.notna()&raw.temperature_c.between(-20,150)
    v=raw[mask].copy().sort_values('timestamp',kind='stable')
    dup=int(v.duplicated('timestamp',keep='last').sum()); v=v.drop_duplicates('timestamp',keep='last').reset_index(drop=True)
    report=ParseReport(len(raw),len(v),int((~mask).sum()),dup,v.timestamp.min() if len(v) else None,v.timestamp.max() if len(v) else None)
    return v,report

def _smooth(v):
    if len(v)<7: return v.temperature_c.astype(float)
    # Time-aware rolling median suppresses brief probe noise while preserving the peak region.
    x=v.set_index('timestamp').temperature_c.astype(float)
    span=max(5,min(61,len(x)//30*2+1))
    return x.rolling(span,center=True,min_periods=1).median().reset_index(drop=True)

def classify_session(v):
    if len(v)<5: raise ValueError('At least five valid readings are required for automatic classification.')
    smooth=_smooth(v); peak_i=int(smooth.idxmax()); peak=float(smooth.iloc[peak_i]); minimum=float(smooth.min()); average=float(smooth.mean())
    start=float(smooth.iloc[:max(2,len(smooth)//20)].median()); end=float(smooth.iloc[-max(2,len(smooth)//20):].median())
    rise=peak-start; fall=peak-end; peak_fraction=peak_i/max(len(smooth)-1,1)
    cooling_points=int((smooth.iloc[peak_i:].diff().fillna(0)<0).sum()); post=max(len(smooth)-peak_i-1,1); cooling_share=cooling_points/post
    pull=pd.Timestamp(v.timestamp.iloc[peak_i])
    # Clear rise followed by a sustained fall: combined session.
    if rise>=25 and fall>=8 and .15<=peak_fraction<=.9:
        confidence=int(min(99,65+min(rise,40)*.45+min(fall,25)*.7+cooling_share*8))
        return Detection('Cook + Hold',confidence,pull,peak,average,minimum,'A substantial temperature rise is followed by a sustained cooling phase.')
    # Starts already hot and mostly trends down: hold/calibration. Distinguish labels conservatively.
    if rise<15 and fall>=8 and peak_fraction<=.25:
        duration_h=(v.timestamp.iloc[-1]-v.timestamp.iloc[0]).total_seconds()/3600
        label='Calibration / Hold Test' if duration_h>=8 and end>=55 else 'Hold Only'
        confidence=int(min(99,72+min(fall,25)*.7+cooling_share*10))
        return Detection(label,confidence,None,peak,average,minimum,'No clear cooking rise was detected; the profile begins hot and cools over time.')
    # Mostly rises and ends near peak: cook only.
    if rise>=20 and fall<8:
        confidence=int(min(99,72+min(rise,45)*.5))
        return Detection('Cook Only',confidence,None,peak,average,minimum,'The profile rises substantially and recording ends without a sustained cooling phase.')
    return Detection('Uncertain',55,pull if fall>=8 else None,peak,average,minimum,'The temperature shape does not clearly match Cook Only, Hold Only, or Cook + Hold.')

def band(t): return -1 if t<60 else min(int(np.searchsorted(BAND_LOWER_C,t,side='right')-1),9)
def assessment(x):
    p=x*100
    return 'Underdone and tight' if p<80 else 'Slightly tight but sliceable' if p<95 else 'Ideal tenderness' if p<=105 else 'Very soft and potentially overdone' if p<=120 else 'Increased risk of mushy or over-rendered texture'

def analyse(v,report,detection,pull_override=None,max_gap=10):
    if len(v)<2: raise ValueError('At least two valid readings are required.')
    pull=pd.Timestamp(pull_override) if pull_override is not None else detection.pull_timestamp
    mode=detection.session_type
    secs=np.zeros((2,10)); below=gap_seconds=0.; gaps=0; accumulated=0.; rows=[]
    for i in range(len(v)-1):
        a=pd.Timestamp(v.timestamp.iloc[i]); b=pd.Timestamp(v.timestamp.iloc[i+1]); dt=(b-a).total_seconds(); temp=float(v.temperature_c.iloc[i]); status='Analysed'; phase=''; inc=0.
        if dt<=0: status='Excluded: non-positive interval'
        elif dt>max_gap: status='Excluded: recording gap'; gaps+=1; gap_seconds+=dt
        else:
            bi=band(temp)
            if bi<0: status='Below model range'; below+=dt
            else:
                if mode=='Cook Only': parts=[(0,dt,'Cook')]
                elif mode in ('Hold Only','Calibration / Hold Test'): parts=[(1,dt,'Hold / cooldown')]
                elif pull is not None:
                    if b<=pull: parts=[(0,dt,'Cook')]
                    elif a>=pull: parts=[(1,dt,'Hold / cooldown')]
                    else:
                        cs=max((pull-a).total_seconds(),0); parts=[(0,cs,'Cook'),(1,dt-cs,'Hold / cooldown')]
                else: parts=[]; status='Unclassified interval'
                for pi,part,label in parts:
                    if part>0: secs[pi,bi]+=part; inc+=part/3600*ZONE_RATES[bi]
                phase=' → '.join(dict.fromkeys(p[2] for p in parts))
        accumulated+=inc
        rows.append({'Timestamp':a,'Temperature °C':temp,'Next timestamp':b,'Elapsed seconds':dt,'Phase':phase,'Status':status,'Incremental rendering':inc,'Accumulated rendering':accumulated})
    rec=[]
    for pi,ph in enumerate(('Cook','Hold / cooldown')):
        for bi in range(10):
            hours=secs[pi,bi]/3600; rec.append({'Phase':ph,'Band':bi+1,'Temperature range':BAND_LABELS[bi],'Duration hours':hours,'Rate per hour':ZONE_RATES[bi],'Tenderness contribution':hours*ZONE_RATES[bi]})
    summary=pd.DataFrame(rec); cook=float(summary[summary.Phase=='Cook']['Tenderness contribution'].sum()); hold=float(summary[summary.Phase=='Hold / cooldown']['Tenderness contribution'].sum()); total=cook+hold
    complete=mode=='Cook + Hold' and pull is not None
    return Result(summary,pd.DataFrame(rows),report,detection,gaps,gap_seconds/3600,below/3600,float(secs.sum()/3600),cook,hold,total,assessment(total),complete)

def demo_data():
    start=datetime(2026,5,23); mins=np.arange(0,23*60+1); h=mins/60
    ah=np.array([0,1,2,4,6,7.5,9,10,12,16,20,22.75,23]); at=np.array([8,32,48,62,72,82,90,95,94,91,86,67,65.5]); base=np.interp(h,ah,at)
    return pd.DataFrame({'timestamp':[start+timedelta(minutes=int(m)) for m in mins],'Average Probe Temperature (°C)':np.round(base+.15*np.sin(mins/40),2)})

def to_excel(results):
    out=BytesIO()
    with pd.ExcelWriter(out,engine='openpyxl') as w:
        overview=[]
        for name,r in results.items():
            d=r.detection; overview.append({'Probe':name,'Session type':d.session_type,'Confidence':d.confidence/100,'Detected pull':d.pull_timestamp,'Peak °C':d.peak_temperature,'Average °C':d.average_temperature,'Minimum °C':d.minimum_temperature,'Analysed hours':r.analysed_hours,'Cook rendering':r.cook,'Hold rendering':r.hold,'Total rendering':r.total,'Assessment':r.assessment if r.complete else 'Partial session'})
            safe=re.sub(r'[^A-Za-z0-9 _-]','',name)[:18] or 'Probe'; r.summary.to_excel(w,sheet_name=(safe+' Summary')[:31],index=False); r.timeline.to_excel(w,sheet_name=(safe+' Timeline')[:31],index=False)
        pd.DataFrame(overview).to_excel(w,sheet_name='Overview',index=False)
        for ws in w.book.worksheets:
            ws.freeze_panes='A2'
            for col in ws.columns: ws.column_dimensions[col[0].column_letter].width=min(max(len(str(c.value or '')) for c in col)+2,42)
    return out.getvalue()
