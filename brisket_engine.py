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
 source_rows:int; valid_rows:int; invalid_rows:int; duplicate_timestamps:int; first_timestamp:pd.Timestamp|None; last_timestamp:pd.Timestamp|None
@dataclass(frozen=True)
class Result:
 summary:pd.DataFrame; timeline:pd.DataFrame; report:ParseReport; excluded_gaps:int; excluded_gap_hours:float; below_model_hours:float; analysed_hours:float; cook:float; hold:float; total:float; assessment:str; mode:str

def parse_ts(s):
 if pd.api.types.is_datetime64_any_dtype(s): return pd.to_datetime(s,errors='coerce')
 text=s.astype('string').str.strip().str.replace(r'\s+(CEST|CET|UTC|GMT)$','',regex=True,case=False).str.replace('T',' ',regex=False)
 p=pd.to_datetime(text,errors='coerce',dayfirst=True)
 n=pd.to_numeric(s,errors='coerce'); m=p.isna()&n.between(20000,100000)
 if m.any(): p.loc[m]=pd.to_datetime(n.loc[m],unit='D',origin='1899-12-30',errors='coerce')
 return p

def parse_temp(s):
 if pd.api.types.is_numeric_dtype(s): return pd.to_numeric(s,errors='coerce')
 return pd.to_numeric(s.astype('string').str.replace('°C','',regex=False,case=False).str.replace('Celsius','',regex=False,case=False).str.strip(),errors='coerce')

def read_file(f,name):
 b=BytesIO(f.read()); f.seek(0); ext=Path(name).suffix.lower()
 if ext=='.csv': return {'CSV':pd.read_csv(b,sep=None,engine='python')}
 if ext in {'.xlsx','.xlsm','.xls'}: return pd.read_excel(b,sheet_name=None)
 raise ValueError('Use XLSX, XLSM, XLS or CSV.')

def detect(df):
 cols=list(df.columns); low={c:str(c).lower() for c in cols}
 t=next((c for c in cols if any(x in low[c] for x in ('timestamp','datetime','date time'))),None)
 if t is None:
  scores=[(parse_ts(df[c].head(300)).notna().mean(),c) for c in cols]; score,t=max(scores)
  if score<.25: raise ValueError('Timestamp column not detected.')
 temps=[c for c in cols if c!=t and any(x in low[c] for x in ('probe','temperature','temp','flat','point','celsius','°c')) and not any(x in low[c] for x in ('pit','ambient','target'))]
 if not temps:
  scored=[]
  for c in cols:
   if c==t: continue
   v=parse_temp(df[c].head(300)); scored.append((v.between(-20,150).mean(),c))
  temps=[c for score,c in sorted(scored,reverse=True) if score>=.25]
 if not temps: raise ValueError('Meat-temperature column not detected.')
 return t,temps

def prepare(df,tcol,pcol):
 raw=pd.DataFrame({'timestamp':parse_ts(df[tcol]),'temperature_c':parse_temp(df[pcol])})
 validmask=raw.timestamp.notna()&raw.temperature_c.between(-20,150); v=raw[validmask].copy().sort_values('timestamp',kind='stable')
 dup=int(v.duplicated('timestamp',keep='last').sum()); v=v.drop_duplicates('timestamp',keep='last').reset_index(drop=True)
 return v,ParseReport(len(raw),len(v),int((~validmask).sum()),dup,v.timestamp.min() if len(v) else None,v.timestamp.max() if len(v) else None)

def band(t): return -1 if t<60 else min(int(np.searchsorted(BAND_LOWER_C,t,side='right')-1),9)
def classify(x):
 p=x*100
 return 'Underdone and tight' if p<80 else 'Slightly tight but sliceable' if p<95 else 'Ideal tenderness' if p<=105 else 'Very soft and potentially overdone' if p<=120 else 'Increased risk of mushy or over-rendered texture'

def analyse(v,report,mode,pull=None,max_gap=10):
 if len(v)<2: raise ValueError('At least two valid readings are required.')
 if mode=='Split at pull timestamp' and pull is None: raise ValueError('Pull timestamp required.')
 pull=pd.Timestamp(pull) if pull else None; secs=np.zeros((2,10)); below=gaph=0.; gaps=0; rows=[]; accumulated=0.
 for i in range(len(v)-1):
  a,b=pd.Timestamp(v.timestamp.iloc[i]),pd.Timestamp(v.timestamp.iloc[i+1]); dt=(b-a).total_seconds(); temp=float(v.temperature_c.iloc[i]); status='Analysed'; phase=''; cont=0.
  if dt<=0: status='Excluded: non-positive'
  elif dt>max_gap: status='Excluded: recording gap'; gaps+=1; gaph+=dt
  else:
   bi=band(temp)
   if bi<0: status='Below model range'; below+=dt
   else:
    if mode=='Cook only': secs[0,bi]+=dt; phase='Cook'; cont=dt/3600*ZONE_RATES[bi]
    elif mode=='Hold / cooldown only': secs[1,bi]+=dt; phase='Hold / cooldown'; cont=dt/3600*ZONE_RATES[bi]
    else:
     if b<=pull: secs[0,bi]+=dt; phase='Cook'; cont=dt/3600*ZONE_RATES[bi]
     elif a>=pull: secs[1,bi]+=dt; phase='Hold / cooldown'; cont=dt/3600*ZONE_RATES[bi]
     else:
      cs=max((pull-a).total_seconds(),0); hs=dt-cs; secs[0,bi]+=cs; secs[1,bi]+=hs; phase='Cook → Hold'; cont=dt/3600*ZONE_RATES[bi]
  accumulated+=cont
  rows.append({'Timestamp':a,'Temperature °C':temp,'Next timestamp':b,'Elapsed seconds':dt,'Phase':phase,'Status':status,'Incremental rendering':cont,'Accumulated rendering':accumulated})
 rec=[]
 for pi,ph in enumerate(('Cook','Hold / cooldown')):
  for bi in range(10):
   hours=secs[pi,bi]/3600; contribution=hours*ZONE_RATES[bi]
   rec.append({'Phase':ph,'Band':bi+1,'Temperature range':BAND_LABELS[bi],'Duration hours':hours,'Rate per hour':ZONE_RATES[bi],'Tenderness contribution':contribution})
 s=pd.DataFrame(rec); cook=float(s[s.Phase=='Cook']['Tenderness contribution'].sum()); hold=float(s[s.Phase=='Hold / cooldown']['Tenderness contribution'].sum())
 return Result(s,pd.DataFrame(rows),report,gaps,gaph/3600,below/3600,float(secs.sum()/3600),cook,hold,cook+hold,classify(cook+hold),mode)

def combine(cook_result=None,hold_result=None):
 c=cook_result.cook if cook_result else 0.; h=hold_result.hold if hold_result else 0.; total=c+h
 return {'Cook rendering':c,'Hold rendering':h,'Total rendering':total,'Assessment':classify(total),'Complete':cook_result is not None and hold_result is not None}

def demo_data():
 start=datetime(2026,5,23); mins=np.arange(0,23*60+1); h=mins/60
 ah=np.array([0,1,2,4,6,7.5,9,10,12,16,20,22.75,23]); at=np.array([8,32,48,62,72,82,90,95,94,91,86,67,65.5]); base=np.interp(h,ah,at)
 d={'timestamp':[start+timedelta(minutes=int(m)) for m in mins]}
 for i,o in enumerate([-2,-1,0,1,2],1): d[f'Probe {i}']=np.round(base+o+.3*np.sin(mins/45+i),2)
 return pd.DataFrame(d)

def to_excel(results,combined=None):
 out=BytesIO()
 with pd.ExcelWriter(out,engine='openpyxl') as w:
  rows=[]
  for name,r in results.items():
   rows.append({'Probe':name,'Mode':r.mode,'First timestamp':r.report.first_timestamp,'Last timestamp':r.report.last_timestamp,'Valid rows':r.report.valid_rows,'Invalid rows':r.report.invalid_rows,'Duplicates':r.report.duplicate_timestamps,'Excluded gaps':r.excluded_gaps,'Analysed hours':r.analysed_hours,'Cook rendering':r.cook,'Hold rendering':r.hold,'Total rendering':r.total,'Assessment':r.assessment})
   safe=re.sub(r'[^A-Za-z0-9 _-]','',name)[:18] or 'Probe'; r.summary.to_excel(w,sheet_name=(safe+' Summary')[:31],index=False); r.timeline.to_excel(w,sheet_name=(safe+' Timeline')[:31],index=False)
  pd.DataFrame(rows).to_excel(w,sheet_name='Overview',index=False)
  if combined: pd.DataFrame([combined]).to_excel(w,sheet_name='Combined session',index=False)
  for ws in w.book.worksheets:
   ws.freeze_panes='A2'
   for col in ws.columns: ws.column_dimensions[col[0].column_letter].width=min(max(len(str(c.value or '')) for c in col)+2,42)
 return out.getvalue()
