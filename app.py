from datetime import datetime
from io import BytesIO
import pandas as pd
import plotly.express as px
import streamlit as st
from brisket_engine import *

st.set_page_config(page_title='Brisket Session Analyser 2.1',page_icon='🔥',layout='wide')
st.markdown('''<style>.block-container{padding-top:1.2rem}.hero{background:linear-gradient(135deg,#17242d,#29404b);color:#fff;padding:24px;border-radius:18px}.hero p{color:#eadfd3}div[data-testid="stMetric"]{background:#fff7ed;border:1px solid #ead7c2;padding:13px;border-radius:13px}.status{padding:13px;border-left:5px solid #5f7d5a;background:#f4f7f1;border-radius:8px}</style><div class="hero"><h1>🔥 Brisket Session Analyser 2.1</h1><p>Upload one probe file. The app classifies the session and detects the pull point automatically.</p></div>''',unsafe_allow_html=True)

st.sidebar.header('Analysis settings')
max_gap=st.sidebar.number_input('Maximum accepted gap (seconds)',1.,3600.,10.,1.,help='Use 10 for second-by-second data. Use 70 for the one-minute reference profile.')
source=st.segmented_control('Data source',['Upload probe file','Try reference brisket'],default='Upload probe file')

@st.cache_data
def load_bytes(data,name):
    f=BytesIO(data); return read_file(f,name)

if source=='Try reference brisket':
    df=demo_data(); sheet='Reference brisket'; tcol='timestamp'; probes=['Average Probe Temperature (°C)']; max_gap=max(max_gap,70.)
    st.info('Reference profile uses one-minute readings. The gap threshold is automatically treated as at least 70 seconds.')
else:
    upload=st.file_uploader('Upload Excel or CSV probe data',type=['xlsx','xlsm','xls','csv'])
    if not upload: st.info('Upload a probe file to begin.'); st.stop()
    sheets=load_bytes(upload.getvalue(),upload.name); sheet=st.selectbox('Worksheet',list(sheets)); df=sheets[sheet]
    detected_t,detected_p=detect_columns(df); cols=list(df.columns)
    c1,c2=st.columns([1,2]); tcol=c1.selectbox('Timestamp column',cols,index=cols.index(detected_t)); probes=c2.multiselect('Meat probe column(s)',cols,default=detected_p)
    if not probes: st.warning('Select at least one meat probe.'); st.stop()

prepared={}; detections={}
for p in probes:
    v,rep=prepare(df,tcol,p); prepared[p]=(v,rep); detections[p]=classify_session(v)

st.subheader('Automatic detection')
drows=[]
for p,d in detections.items(): drows.append({'Probe':p,'Session type':d.session_type,'Confidence':f'{d.confidence}%','Detected pull':d.pull_timestamp,'Peak °C':round(d.peak_temperature,1),'Average °C':round(d.average_temperature,1),'Minimum °C':round(d.minimum_temperature,1)})
st.dataframe(pd.DataFrame(drows),hide_index=True,use_container_width=True)

primary=detections[probes[0]]
override=None
if primary.session_type=='Cook + Hold' and primary.pull_timestamp is not None:
    st.markdown(f'<div class="status"><b>Detected pull:</b> {primary.pull_timestamp:%d/%m/%Y %H:%M:%S} &nbsp; <b>Confidence:</b> {primary.confidence}%<br>{primary.reason}</div>',unsafe_allow_html=True)
    with st.expander('Advanced: override detected pull time'):
        use_override=st.checkbox('Override detected pull time')
        if use_override:
            c1,c2=st.columns(2); od=c1.date_input('Pull date',primary.pull_timestamp.date()); ot=c2.time_input('Pull time',primary.pull_timestamp.time(),step=60); override=datetime.combine(od,ot)
else:
    st.info(f'Detected session: {primary.session_type}. {primary.reason}')

if not st.button('Analyse session',type='primary',use_container_width=True): st.stop()
results={}
for p,(v,rep) in prepared.items():
    d=detections[p]; local_override=override if override is not None and d.session_type=='Cook + Hold' else None
    results[str(p)]=analyse(v,rep,d,local_override,float(max_gap))

st.subheader('Results')
for name,r in results.items():
    with st.expander(name,expanded=len(results)==1):
        if not r.complete: st.warning('Partial session detected. The displayed percentage is the recorded phase contribution, not a complete final-tenderness assessment.')
        a,b,c,d=st.columns(4); a.metric('Cook contribution',f'{r.cook:.1%}'); b.metric('Hold contribution',f'{r.hold:.1%}'); c.metric('Recorded total',f'{r.total:.1%}'); d.metric('Assessment',r.assessment if r.complete else 'Partial session')
        tabs=st.tabs(['Temperature','Accumulated rendering','Band calculation','Data quality'])
        with tabs[0]:
            x=r.timeline[r.timeline.Status=='Analysed']; fig=px.line(x,x='Timestamp',y='Temperature °C',color='Phase' if x.Phase.nunique()>1 else None,color_discrete_map={'Cook':'#d7652a','Hold / cooldown':'#5f7d5a'}); st.plotly_chart(fig,use_container_width=True)
        with tabs[1]:
            fig=px.line(r.timeline,x='Timestamp',y='Accumulated rendering'); fig.update_yaxes(tickformat='.0%'); st.plotly_chart(fig,use_container_width=True)
        with tabs[2]:
            s=r.summary[r.summary['Duration hours']>0].copy(); s['Duration hours']=s['Duration hours'].round(3); s['Rate per hour']=s['Rate per hour'].map(lambda x:f'{x:.1%}'); s['Tenderness contribution']=s['Tenderness contribution'].map(lambda x:f'{x:.1%}'); st.dataframe(s,hide_index=True,use_container_width=True)
        with tabs[3]:
            q1,q2,q3=st.columns(3); q1.metric('Analysed hours',f'{r.analysed_hours:.3f}'); q2.metric('Excluded gap hours',f'{r.excluded_gap_hours:.3f}'); q3.metric('Below 60°C hours',f'{r.below_model_hours:.3f}'); st.write('Valid / invalid / duplicates:',r.report.valid_rows,r.report.invalid_rows,r.report.duplicate_timestamps)

st.download_button('Download analysis workbook',to_excel(results),'brisket_session_analysis_v2.1.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',use_container_width=True)
st.caption('Automatic classification and pull detection are estimates. Review the detected pull point and use probe tenderness plus safe food handling alongside the model.')
