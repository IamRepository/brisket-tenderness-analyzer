from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
import brisket_engine as meat

ROLES=['Meat temperature','Grate temperature','Controller temperature','Target temperature','Ignore']
@dataclass(frozen=True)
class PitResult:
    role:str; timeline:pd.DataFrame; average:float; minimum:float; maximum:float; stddev:float; stability_score:float; lid_events:pd.DataFrame

def classify_columns(df,timestamp_col):
    rows=[]
    for col in df.columns:
        if col==timestamp_col: continue
        values=meat.parse_temp(df[col]); valid=values.dropna()
        if len(valid)<2: continue
        name=str(col).lower()
        if any(x in name for x in ('target','setpoint','set point','desired')): role='Target temperature'; confidence=99
        elif any(x in name for x in ('grate','ambient','pit probe','chamber probe')): role='Grate temperature'; confidence=93
        elif any(x in name for x in ('controller','smoker','smoque','built-in','oven')): role='Controller temperature'; confidence=90
        elif any(x in name for x in ('meat','internal','flat','point','food','brisket','probe')): role='Meat temperature'; confidence=88
        else:
            smooth=valid.rolling(min(21,max(3,len(valid)//50)),center=True,min_periods=1).median(); rise=float(smooth.max()-smooth.iloc[:max(2,len(smooth)//20)].median()); fluct=float(valid.diff().abs().median())
            if rise>=20: role='Meat temperature'; confidence=72
            elif fluct>=.5: role='Grate temperature'; confidence=65
            else: role='Controller temperature'; confidence=60
        rows.append({'Column':col,'Suggested role':role,'Confidence':confidence})
    return pd.DataFrame(rows)

def prepare(df,timestamp_col,temp_col):
    out=pd.DataFrame({'timestamp':meat.parse_ts(df[timestamp_col]),'temperature_c':meat.parse_temp(df[temp_col])}).dropna()
    return out[out.temperature_c.between(-20,400)].sort_values('timestamp').drop_duplicates('timestamp',keep='last').reset_index(drop=True)

def analyse(prepared,role):
    values=prepared.temperature_c.astype(float); std=float(values.std(ddof=0)); change=float(values.diff().abs().median()) if len(values)>1 else 0.; score=float(np.clip(100-4*std-8*change,0,100)); events=pd.DataFrame()
    if role=='Grate temperature' and len(prepared)>4:
        diffs=values.diff(5); candidates=prepared.loc[diffs<=-12,['timestamp','temperature_c']].copy(); candidates['Drop °C']=(-diffs[diffs<=-12]).round(1).values; events=candidates.rename(columns={'timestamp':'Detected time','temperature_c':'Temperature °C'}).head(20)
    return PitResult(role,prepared,float(values.mean()),float(values.min()),float(values.max()),std,score,events)

def align(meat_timeline,pit_results):
    overlay=meat_timeline[['Timestamp','Temperature °C']].rename(columns={'Timestamp':'timestamp','Temperature °C':'Meat temperature'}).sort_values('timestamp')
    for label,result in pit_results.items():
        stream=result.timeline.rename(columns={'temperature_c':label}).sort_values('timestamp'); intervals=stream.timestamp.diff().dt.total_seconds().dropna(); tolerance=max(float(intervals.median())*2 if len(intervals) else 60,60)
        overlay=pd.merge_asof(overlay,stream[['timestamp',label]],on='timestamp',direction='nearest',tolerance=pd.Timedelta(seconds=tolerance))
    return overlay
