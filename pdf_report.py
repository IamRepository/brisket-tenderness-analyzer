from __future__ import annotations
from datetime import datetime
from io import BytesIO
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, CondPageBreak
from reportlab.graphics.shapes import Drawing, String
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.widgets.markers import makeMarker

DARK=colors.HexColor('#27343B'); ACCENT=colors.HexColor('#A94722'); LIGHT=colors.HexColor('#F3F5F6'); GRID=colors.HexColor('#D6DCE0')
COLORS=[colors.HexColor(x) for x in ('#C6532B','#2F6B9A','#5B7F4B','#8A5A9B','#C18B2F')]
CONTENT=A4[0]-28*mm
S=getSampleStyleSheet(); S.add(ParagraphStyle(name='TitleX',parent=S['Title'],fontSize=21,leading=25,textColor=DARK)); S.add(ParagraphStyle(name='SectionX',parent=S['Heading2'],fontSize=14,leading=17,textColor=ACCENT,spaceBefore=8,spaceAfter=6)); S.add(ParagraphStyle(name='SmallX',parent=S['BodyText'],fontSize=7.4,leading=9)); S.add(ParagraphStyle(name='CellX',parent=S['BodyText'],fontSize=7.1,leading=8.6))

def tx(v):
    if v is None or (isinstance(v,float) and pd.isna(v)): return 'N/A'
    if isinstance(v,pd.Timestamp): return v.strftime('%d/%m/%Y %H:%M:%S')
    t=str(v)
    for x in ('🥩','🔥','🌡','♨','🚫','🟦','🟩'): t=t.replace(x,'')
    return t.strip()

def short(v):
    t=tx(v); return t.split(' — ',1)[0] if ' — ' in t else t

def source(v):
    t=tx(v)
    if '/' not in t:return t
    a,b=[x.strip() for x in t.split('/',1)]; low=b.lower()
    if low.startswith('poin') and set(low[4:])<={'t'}:b='Point'
    if low=='enviroment':b='Environment'
    return f'{a} / {b}'

def stage(label,r):
    role=short(label); k=r.detection.session_type
    if k=='Cook + Hold':return f'{role} (Cook + Hold)'
    if k in ('Hold Only','Calibration / Hold Test'):return f'{role} (Hold only)'
    if k=='Cook Only':return f'{role} (Cook only)'
    return f'{role} ({k})'

def table(rows,widths,size=7.1,header=True):
    t=Table([[Paragraph(tx(x),S['CellX']) for x in r] for r in rows],colWidths=widths,repeatRows=1 if header else 0,splitByRow=1,hAlign='LEFT')
    cmd=[('GRID',(0,0),(-1,-1),.35,GRID),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)]
    if header:cmd += [('BACKGROUND',(0,0),(-1,0),DARK),('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,LIGHT])]
    t.setStyle(TableStyle(cmd)); return t

def chart(series,title,origin):
    d=Drawing(CONTENT,91*mm); c=LinePlot(); c.x=15*mm;c.y=25*mm;c.width=CONTENT-27*mm;c.height=50*mm; data=[]; names=[]; xs=[];ys=[]
    for name,f,tc,vc in series:
        q=f[[tc,vc]].dropna().sort_values(tc)
        if q.empty:continue
        if len(q)>350:q=q.iloc[::max(len(q)//350,1)]
        xx=(q[tc]-(pd.Timestamp(origin) if origin is not None else q[tc].min())).dt.total_seconds()/3600; yy=pd.to_numeric(q[vc],errors='coerce'); pts=[(float(x),float(y)) for x,y in zip(xx,yy) if pd.notna(y)]
        if pts:data.append(pts);names.append(short(name));xs += [x for x,_ in pts];ys += [y for _,y in pts]
    if not data:return Paragraph('No chart data available.',S['BodyText'])
    c.data=data;c.xValueAxis.valueMin=min(xs);c.xValueAxis.valueMax=max(xs) if max(xs)>min(xs) else min(xs)+1;c.xValueAxis.labels.fontSize=7;c.yValueAxis.valueMin=min(ys)-2;c.yValueAxis.valueMax=max(ys)+2;c.yValueAxis.labels.fontSize=7
    for i in range(len(data)):c.lines[i].strokeColor=COLORS[i%len(COLORS)];c.lines[i].strokeWidth=1.2;c.lines[i].symbol=makeMarker('FilledCircle');c.lines[i].symbol.size=1.7
    l=Legend();l.x=15*mm;l.y=16*mm;l.fontSize=6.2;l.deltax=76;l.deltay=9;l.columnMaximum=2;l.colorNamePairs=[(COLORS[i%len(COLORS)],names[i]) for i in range(len(names))]
    d.add(c);d.add(l);d.add(String(15*mm,81*mm,title,fontName='Helvetica-Bold',fontSize=10.5,fillColor=DARK));d.add(String(15*mm,4*mm,'Elapsed time from master session start (hours)',fontSize=6.5));d.add(String(1*mm,46*mm,'Temperature (C)',fontSize=6.5));return d

def footer(canvas,doc):
    canvas.saveState();canvas.setFont('Helvetica',7);canvas.setFillColor(DARK);canvas.drawString(14*mm,8*mm,'Brisket Session Analyser - analytical report');canvas.drawRightString(A4[0]-14*mm,8*mm,f'Page {doc.page}');canvas.restoreState()

def interval(x):
    if x is None:return 'Unknown'
    return f'{x:.0f} sec' if x<60 else (f'{x/60:.1f} min' if x<3600 else f'{x/3600:.2f} h')

def duration(f): return 0 if f is None or len(f)<2 else (f.timestamp.iloc[-1]-f.timestamp.iloc[0]).total_seconds()/3600

def build_pdf_report(app_version,configuration,meat_results,stats,environment_results,transfer_time,environment_sources=None,master_start=None,cook_aggregate=None,hold_aggregate=None):
    environment_sources=environment_sources or {}
    if master_start is None:
        starts=[i['valid'].timestamp.min() for i in meat_results.values() if not i['valid'].empty]+[r.timeline.timestamp.min() for r in environment_results.values() if not r.timeline.empty]; master_start=min(starts) if starts else None
    out=BytesIO(); doc=SimpleDocTemplate(out,pagesize=A4,leftMargin=14*mm,rightMargin=14*mm,topMargin=13*mm,bottomMargin=15*mm); story=[]
    story += [Paragraph(f'Brisket Session Analyser {app_version}',S['TitleX']),Paragraph('Full session analysis report',S['Heading2']),Paragraph(f'Generated: {datetime.now():%d/%m/%Y %H:%M:%S}',S['SmallX']),Paragraph('Methodology',S['SectionX']),Paragraph("Independent implementation based on brisket time-temperature and hot-hold concepts shared by Steve Gow. Results are analytical estimates.",S['BodyText']),Paragraph('Configuration',S['SectionX']),table([['Role','Source']]+[[r.get('Role'),source(r.get('Source'))] for r in configuration],[78*mm,102*mm]),Spacer(1,3*mm),table([['Master timeline start',tx(master_start)],['Derived smoker-to-hold boundary',tx(transfer_time)]],[72*mm,108*mm],header=False)]
    timing=[['Profile','Source','Session','Conf.','Pull','Cook h','Hold h']]; temps=[['Profile','Peak','Avg','Min','Cook avg','Hold avg','Sampling','Gap']]
    for label,i in meat_results.items():
        r=i['result']; st=stats[label]; sa=i['sample']; n=stage(label,r); timing.append([n,source(i['source']),r.detection.session_type,f'{r.detection.confidence}%',tx(r.detection.pull_timestamp),f"{st['cook_h']:.2f}",f"{st['hold_h']:.2f}"]);temps.append([n,f'{r.detection.peak_temperature:.1f}',f'{r.detection.average_temperature:.1f}',f'{r.detection.minimum_temperature:.1f}',tx(st['cook_avg']),tx(st['hold_avg']),interval(sa['normal']),interval(sa['threshold'])])
    story += [PageBreak(),Paragraph('Session detection and timing',S['SectionX']),table(timing,[42*mm,31*mm,28*mm,14*mm,32*mm,16*mm,16*mm],6.8),Spacer(1,5*mm),Paragraph('Temperature and sampling summary',S['SectionX']),table(temps,[48*mm,17*mm,17*mm,17*mm,22*mm,22*mm,21*mm,21*mm],6.8)]
    comp=[['Profile','Source','Cook','Hold','Total','Assessment','h']]; ms=[]
    for label,i in meat_results.items():r=i['result'];n=stage(label,r);comp.append([n,source(i['source']),f'{r.cook:.1%}',f'{r.hold:.1%}',f'{r.total:.1%}',r.assessment if r.complete else 'Partial session',f'{r.analysed_hours:.2f}']);ms.append((n,i['valid'],'timestamp','temperature_c'))
    story += [PageBreak(),Paragraph('Brisket probe comparison',S['SectionX']),table(comp,[43*mm,31*mm,17*mm,17*mm,17*mm,42*mm,14*mm],6.8),PageBreak(),Paragraph('Brisket temperature profiles',S['SectionX']),chart(ms,'Brisket Point and Flat temperature profiles',master_start),PageBreak(),Paragraph('Environment analysis',S['SectionX'])]
    if environment_results:
        er=[['Stream','Source','Stage','Start','End','h','Avg','Min','Max','Stab.']]; es=[]
        for label,r in environment_results.items():f=r.timeline;er.append([short(label),source(environment_sources.get(label,'N/A')),'Hold' if 'Hold' in short(r.role) else 'Cook',tx(f.timestamp.min()),tx(f.timestamp.max()),f'{duration(f):.2f}',f'{r.average:.1f}',f'{r.minimum:.1f}',f'{r.maximum:.1f}',f'{r.stability_score:.0f}/100']);es.append((short(label),f,'timestamp','temperature_c'))
        story += [table(er,[27*mm,28*mm,15*mm,31*mm,31*mm,11*mm,11*mm,11*mm,11*mm,15*mm],6.2),Spacer(1,4*mm),chart(es,'Cook and Hold Environment profiles',master_start)]
    agg=[['Stage','Start','End','Readings','Average C','Sensors']]; cs=[]
    for name,f in [('Cook',cook_aggregate),('Hold',hold_aggregate)]:
        if f is not None and not f.empty:cs.append((f'Composite Environment ({name})',f,'timestamp','Environment aggregate °C'));agg.append([name,tx(f.timestamp.min()),tx(f.timestamp.max()),len(f),f"{f['Environment aggregate °C'].mean():.1f}",f"{f['Available sensors'].mean():.2f}"])
    if cs:story += [PageBreak(),Paragraph('Composite environment timeline',S['SectionX']),Paragraph('Cook and Hold segments share the master timeline. The transfer gap remains visible.',S['SmallX']),table(agg,[22*mm,38*mm,38*mm,20*mm,30*mm,32*mm]),Spacer(1,4*mm),chart(cs,'Composite environment temperature',master_start)]
    for label,i in meat_results.items():
        r=i['result'];st=stats[label];n=stage(label,r);metrics=[['Metric','Value','Metric','Value'],['Source',source(i['source']),'Session',r.detection.session_type],['Confidence',f'{r.detection.confidence}%','Detected pull',tx(r.detection.pull_timestamp)],['Cook duration',f"{st['cook_h']:.2f} h",'Hold duration',f"{st['hold_h']:.2f} h"],['Cook contribution',f'{r.cook:.1%}','Hold contribution',f'{r.hold:.1%}'],['Recorded total',f'{r.total:.1%}','Assessment',r.assessment if r.complete else 'Partial session']]
        bands=r.summary[r.summary['Duration hours']>0]; br=[['Phase','Band','Temperature range','Duration h','Rate/h','Contribution']]+[[x['Phase'],x['Band'],x['Temperature range'],f"{x['Duration hours']:.3f}",f"{x['Rate per hour']:.1%}",f"{x['Tenderness contribution']:.1%}"] for _,x in bands.iterrows()]
        story += [PageBreak(),Paragraph(n,S['SectionX']),Paragraph(f"Source: {source(i['source'])}",S['SmallX']),table(metrics,[29*mm,61*mm,29*mm,61*mm]),Spacer(1,4*mm),chart([(n,i['valid'],'timestamp','temperature_c')],f'Temperature profile - {n}',master_start)]
        if not bands.empty:story += [CondPageBreak(68*mm),Paragraph('Rendering band calculation',S['SectionX']),table(br,[27*mm,12*mm,49*mm,25*mm,24*mm,35*mm],6.8)]
    story += [PageBreak(),Paragraph('Interpretation notes',S['SectionX']),Paragraph('All report charts use one shared master session timeline. Missing values are not interpolated across the smoker-to-hold transfer.',S['BodyText'])]
    doc.build(story,onFirstPage=footer,onLaterPages=footer);return out.getvalue()
