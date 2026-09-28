from datetime import datetime
from io import BytesIO
import pandas as pd
import plotly.express as px
import streamlit as st
import brisket_engine as engine
import pit_engine as pit

st.set_page_config(page_title='Brisket Session Analyser 2.4',page_icon='🔥',layout='wide')
st.title('🔥 Brisket Session Analyser 2.4')
st.caption("Based on Steve Gow's brisket rendering and hot-hold methodology. Independent implementation; not affiliated with or endorsed by Steve Gow.")
st.sidebar.header('Settings')
max_gap=st.sidebar.number_input('Maximum accepted gap (seconds)',1.,3600.,10.,1.)
source=st.segmented_control('Data source',['Upload file','Try reference brisket'],default='Upload file')

@st.cache_data
def load(data,name): return engine.read_file(BytesIO(data),name)

if source=='Try reference brisket':
    df=engine.demo_data(); timestamp_col='timestamp'; meat_cols=['Average Probe Temperature (°C)']; classifications=pd.DataFrame([{'Column':meat_cols[0],'Suggested role':'Meat temperature','Confidence':99}]); effective_gap=max(max_gap,70.)
else:
    primary=st.file_uploader('Upload meat or combined temperature file',type=['xlsx','xlsm','xls','csv'],key='primary')
    optional=st.file_uploader('Optional second temperature file',type=['xlsx','xlsm','xls','csv'],key='optional')
    if not primary: st.info('Upload a meat or combined temperature file.'); st.stop()
    sheets=load(primary.getvalue(),primary.name); sheet=st.selectbox('Primary worksheet',list(sheets)); df=sheets[sheet]
    timestamp_col,_=engine.detect_columns(df); extra_df=None; extra_timestamp=None
    classifications=pit.classify_columns(df,timestamp_col); classifications['File']='Primary'
    if optional:
        osheets=load(optional.getvalue(),optional.name); osheet=st.selectbox('Optional worksheet',list(osheets)); extra_df=osheets[osheet]
        extra_timestamp,_=engine.detect_columns(extra_df); extra=pit.classify_columns(extra_df,extra_timestamp); extra['File']='Optional'; classifications=pd.concat([classifications,extra],ignore_index=True)
    effective_gap=float(max_gap)

st.subheader('Review column classifications')
roles={}
for i,row in classifications.iterrows():
    key=f"{row['File']}_{row['Column']}" if 'File' in row else str(row['Column'])
    roles[key]=st.selectbox(f"{row.get('File','Reference')} • {row['Column']} ({row['Confidence']}% suggested confidence)",pit.ROLES,index=pit.ROLES.index(row['Suggested role']),key='role_'+key)

if not st.button('Analyse session',type='primary',use_container_width=True): st.stop()
meat_results={}; pit_results={}
for i,row in classifications.iterrows():
    filename=row.get('File','Reference'); col=row['Column']; key=f'{filename}_{col}' if 'File' in row else str(col); role=roles[key]
    active_df=df if filename in ('Primary','Reference') else extra_df; active_time=timestamp_col if filename in ('Primary','Reference') else extra_timestamp
    prepared=pit.prepare(active_df,active_time,col)
    if role=='Meat temperature':
        valid,report=engine.prepare(active_df,active_time,col); detection=engine.classify_session(valid); meat_results[str(col)]=engine.analyse(valid,report,detection,max_gap=effective_gap)
    elif role in ('Grate temperature','Controller temperature','Target temperature'):
        pit_results[role]=pit.analyse(prepared,role)

if not meat_results: st.error('At least one column must be classified as Meat temperature.'); st.stop()
for name,result in meat_results.items():
    st.subheader(name); a,b,c,d=st.columns(4); a.metric('Session',result.detection.session_type); b.metric('Cook contribution',f'{result.cook:.1%}'); c.metric('Hold contribution',f'{result.hold:.1%}'); d.metric('Recorded total',f'{result.total:.1%}')
    chart=result.timeline[result.timeline.Status=='Analysed']; st.plotly_chart(px.line(chart,x='Timestamp',y='Temperature °C',color='Phase' if chart.Phase.nunique()>1 else None),use_container_width=True)

if pit_results:
    st.subheader('Cooking-environment analysis')
    for name,result in pit_results.items():
        with st.expander(name,expanded=True):
            a,b,c,d=st.columns(4); a.metric('Average',f'{result.average:.1f}°C'); b.metric('Minimum',f'{result.minimum:.1f}°C'); c.metric('Maximum',f'{result.maximum:.1f}°C'); d.metric('Stability',f'{result.stability_score:.0f}/100')
            st.plotly_chart(px.line(result.timeline,x='timestamp',y='temperature_c',labels={'temperature_c':f'{name} °C'}),use_container_width=True)
            if len(result.lid_events): st.write('Possible lid-open events'); st.dataframe(result.lid_events,hide_index=True,use_container_width=True)
    first_meat=next(iter(meat_results.values())); overlay=pit.align(first_meat.timeline,pit_results); melted=overlay.melt(id_vars='timestamp',var_name='Temperature source',value_name='Temperature °C'); st.subheader('Meat and cooking-environment overlay'); st.plotly_chart(px.line(melted,x='timestamp',y='Temperature °C',color='Temperature source'),use_container_width=True)

st.caption('Pit stability and event detections are analytical estimates, not manufacturer-provided Weber metrics.')
