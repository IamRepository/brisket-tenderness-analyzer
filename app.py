from datetime import datetime,time
from io import BytesIO
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from brisket_engine import *

st.set_page_config(page_title='Brisket Session Analyser 2.0',page_icon='🔥',layout='wide')
st.markdown('''<style>.block-container{padding-top:1.2rem}div[data-testid="stMetric"]{background:#fff7ed;border:1px solid #ead7c2;padding:14px;border-radius:14px}.hero{background:linear-gradient(135deg,#17242d,#29404b);color:white;padding:24px;border-radius:18px}.hero p{color:#eadfd3}.status{padding:13px;border-left:5px solid #5f7d5a;background:#f4f7f1;border-radius:8px}</style><div class="hero"><h1>🔥 Brisket Session Analyser 2.0</h1><p>Analyse complete cooks, separate Cook and Hold files, or try the built-in reference brisket.</p></div>''',unsafe_allow_html=True)

workflow=st.segmented_control('Workflow',['Single/combined file','Separate Cook + Hold files','Try reference brisket'],default='Single/combined file')
max_gap=st.sidebar.number_input('Maximum accepted gap (seconds)',1.,3600.,70.,1.,help='The demo has one-minute readings, so 70 seconds is suitable. Use 10 seconds for second-by-second exports.')

@st.cache_data
def load_bytes(data,name):
 class F(BytesIO): pass
 f=F(data); return read_file(f,name)

def configuration(upload,key,default_mode='Split at pull timestamp'):
 sheets=load_bytes(upload.getvalue(),upload.name); sn=st.selectbox('Worksheet',list(sheets),key='sheet'+key); df=sheets[sn]; tc,probes=detect(df); cols=list(df.columns)
 c1,c2=st.columns([1,2]); tcol=c1.selectbox('Timestamp column',cols,index=cols.index(tc),key='time'+key); pcols=c2.multiselect('Meat probe column(s)',cols,default=probes,key='probes'+key)
 mode=st.radio('Session classification',['Cook only','Hold / cooldown only','Split at pull timestamp'],index=['Cook only','Hold / cooldown only','Split at pull timestamp'].index(default_mode),horizontal=True,key='mode'+key)
 pull=None
 if mode=='Split at pull timestamp':
  d,t=st.columns(2); pdte=d.date_input('Pull date',datetime(2026,5,23).date(),key='date'+key); pt=t.time_input('Pull time',time(10,0),step=60,key='ptime'+key); pull=datetime.combine(pdte,pt)
 return df,tcol,pcols,mode,pull

def run(df,tcol,pcols,mode,pull,prefix=''):
 ans={}
 for p in pcols:
  v,rep=prepare(df,tcol,p); ans[prefix+str(p)]=analyse(v,rep,mode,pull,float(max_gap))
 return ans

results={}; combined=None; incomplete=False
if workflow=='Try reference brisket':
 st.markdown('<div class="status"><b>Reference brisket:</b> approximate minute-by-minute reconstruction from the uploaded Typhur screenshot. Pull timestamp: 23/05/2026 10:00.</div>',unsafe_allow_html=True)
 df=demo_data(); results=run(df,'timestamp',[f'Probe {i}' for i in range(1,6)],'Split at pull timestamp',datetime(2026,5,23,10,0))
 combined=None
elif workflow=='Single/combined file':
 up=st.file_uploader('Upload probe export',type=['xlsx','xlsm','xls','csv'])
 if not up: st.info('Upload a file or use Try reference brisket.'); st.stop()
 df,tcol,pcols,mode,pull=configuration(up,'single')
 if not pcols: st.warning('Select at least one meat probe.'); st.stop()
 if st.button('Analyse session',type='primary',use_container_width=True): results=run(df,tcol,pcols,mode,pull); incomplete=mode!='Split at pull timestamp'
 else: st.stop()
else:
 c,h=st.columns(2); cu=c.file_uploader('Upload Cook file',type=['xlsx','xlsm','xls','csv']); hu=h.file_uploader('Upload Hold / cooldown file',type=['xlsx','xlsm','xls','csv'])
 if not cu or not hu: st.info('Upload both files to calculate a complete session.'); st.stop()
 with c: cdf,ct,cp,_,_=configuration(cu,'cook','Cook only')
 with h: hdf,ht,hp,_,_=configuration(hu,'hold','Hold / cooldown only')
 if st.button('Analyse complete session',type='primary',use_container_width=True):
  cr=run(cdf,ct,cp,'Cook only',None,'Cook - '); hr=run(hdf,ht,hp,'Hold / cooldown only',None,'Hold - '); results={**cr,**hr}
  combined=combine(next(iter(cr.values())),next(iter(hr.values())))
 else: st.stop()

if combined:
 st.subheader('Complete session'); a,b,c,d=st.columns(4); a.metric('Cook rendering',f"{combined['Cook rendering']:.1%}"); b.metric('Hold rendering',f"{combined['Hold rendering']:.1%}"); c.metric('Predicted final',f"{combined['Total rendering']:.1%}"); d.metric('Assessment',combined['Assessment'])

if not results: st.stop()
# Comparison dashboard
st.subheader('Probe dashboard')
dash=[]
for n,r in results.items(): dash.append({'Probe':n,'Cook':r.cook,'Hold':r.hold,'Total':r.total,'Assessment':r.assessment,'Hours':r.analysed_hours})
dd=pd.DataFrame(dash)
st.dataframe(dd,hide_index=True,use_container_width=True,column_config={'Cook':st.column_config.ProgressColumn(format='percent',min_value=0,max_value=max(1,float(dd.Cook.max()))),'Hold':st.column_config.ProgressColumn(format='percent',min_value=0,max_value=max(1,float(dd.Hold.max()))),'Total':st.column_config.ProgressColumn(format='percent',min_value=0,max_value=max(1,float(dd.Total.max()))),'Hours':st.column_config.NumberColumn(format='%.2f')})

for name,r in results.items():
 with st.expander(name,expanded=len(results)==1):
  if r.mode!='Split at pull timestamp':
   phase='Cook' if r.mode=='Cook only' else 'Hold / cooldown'
   st.info(f'{phase}-only analysis. The percentage below is the contribution from this phase; final tenderness requires both phases.')
  m1,m2,m3,m4=st.columns(4); m1.metric('Cook contribution',f'{r.cook:.1%}'); m2.metric('Hold contribution',f'{r.hold:.1%}'); m3.metric('Recorded contribution',f'{r.total:.1%}'); m4.metric('Assessment',r.assessment if r.mode=='Split at pull timestamp' else 'Partial session')
  tabs=st.tabs(['Temperature','Accumulated rendering','Band calculation','Data quality'])
  with tabs[0]:
   x=r.timeline[r.timeline.Status=='Analysed']; fig=px.line(x,x='Timestamp',y='Temperature °C',color='Phase' if x.Phase.nunique()>1 else None,color_discrete_map={'Cook':'#d7652a','Hold / cooldown':'#5f7d5a'}); st.plotly_chart(fig,use_container_width=True)
  with tabs[1]:
   fig=px.line(r.timeline,x='Timestamp',y='Accumulated rendering'); fig.update_yaxes(tickformat='.0%'); st.plotly_chart(fig,use_container_width=True)
  with tabs[2]:
   s=r.summary[r.summary['Duration hours']>0].copy(); s['Duration hours']=s['Duration hours'].round(3); s['Rate per hour']=s['Rate per hour'].map(lambda x:f'{x:.1%}'); s['Tenderness contribution']=s['Tenderness contribution'].map(lambda x:f'{x:.1%}'); st.dataframe(s,hide_index=True,use_container_width=True)
  with tabs[3]:
   q1,q2,q3=st.columns(3); q1.metric('Excluded gap hours',f'{r.excluded_gap_hours:.3f}'); q2.metric('Below 60°C hours',f'{r.below_model_hours:.3f}'); q3.metric('Analysed hours',f'{r.analysed_hours:.3f}'); st.write('First reading:',r.report.first_timestamp); st.write('Last reading:',r.report.last_timestamp); st.write('Valid / invalid / duplicates:',r.report.valid_rows,r.report.invalid_rows,r.report.duplicate_timestamps)

st.download_button('Download analysis workbook',to_excel(results,combined),'brisket_session_analysis_v2.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',use_container_width=True)
st.caption('Estimate only. Use probe tenderness and safe cooking, cooling and holding practices alongside the model. Screenshot-derived demo data are approximate, not original measurements.')
