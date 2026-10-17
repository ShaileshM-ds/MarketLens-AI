from pathlib import Path
import re,json
from xml.sax.saxutils import escape
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak,Image,KeepTogether,Table,TableStyle
from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.utils import ImageReader

root=Path(__file__).resolve().parents[1];out=root/'submission'/'MarketLens-AI-Report.pdf'
styles=getSampleStyleSheet()
styles.add(ParagraphStyle(name='MLBody',fontName='Helvetica',fontSize=10,leading=16,textColor=colors.HexColor('#263d54'),spaceAfter=10))
styles.add(ParagraphStyle(name='MLH1',fontName='Helvetica-Bold',fontSize=24,leading=29,textColor=colors.HexColor('#153d62'),spaceAfter=18))
styles.add(ParagraphStyle(name='MLH2',fontName='Helvetica-Bold',fontSize=16,leading=22,textColor=colors.HexColor('#175579'),spaceBefore=18,spaceAfter=12,keepWithNext=True))
styles.add(ParagraphStyle(name='MLSmall',fontName='Helvetica',fontSize=8,leading=12,textColor=colors.HexColor('#536d85'),spaceAfter=8))
story=[]
def p(text,style='MLBody'):
    text=text.replace('—','-').replace('–','-').replace('’',"'").replace('“','"').replace('”','"').replace('×','x').replace('−','-')
    text=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',r'\1 (\2)',text)
    text=escape(text)
    text=re.sub(r'\*\*([^*]+)\*\*',r'<b>\1</b>',text).replace('`','')
    return Paragraph(text,styles[style])
story += [Spacer(1,95),p('MarketLens AI','MLH1'),p('Explainable Marketplace Intelligence<br/>and Historical Price Auditing'.replace('<br/>',' - '),'MLH2'),Spacer(1,35),p('College minor project | Editable source: REPORT.md'),Spacer(1,35),p('Student(s): [Student names]'),p('Institution: [Institution]'),p('Supervisor: [Supervisor]'),p('Academic year: [Academic year]'),Spacer(1,45),p('Implemented local prototype. Demonstration records are synthetic. External collection and public deployment are not claimed.','MLSmall'),PageBreak()]
source=(root/'submission'/'REPORT.md').read_text(encoding='utf-8')
started=False;paragraph=[]
def flush():
    if paragraph: story.append(p(' '.join(paragraph)));paragraph.clear()
for line in source.splitlines():
    if line.startswith('## 1. '): started=True
    if not started: continue
    if line.startswith('## '): flush();story.append(p(line[3:],'MLH2'))
    elif re.match(r'^\d+\. ',line): flush();story.append(p(line))
    elif not line.strip(): flush()
    else: paragraph.append(line.strip())
flush()
story += [PageBreak(),p('Measured synthetic evaluation','MLH1'),p('Exact results are in evaluation.json. All quantities below are computed by the implemented services, not manually assigned UI metrics.')]
data=json.loads((root/'submission'/'evaluation.json').read_text())
rows=[[p(x,'MLSmall') for x in ['Scenario product','Score','Analog matches','Flagged pairs','Forecast MAE (INR)']]]
for r in data['results'][:12]:
    row=[r['title'],str(r['discount_score']) if r['discount_score'] is not None else 'Insufficient',str(r['analog_count']),str(len(r['flagged_pairs'])),f"{r['forecast']['mae']/100:.2f}" if r['forecast'].get('mae') is not None else 'Insufficient']
    rows.append([p(x,'MLSmall') for x in row])
table=Table(rows,colWidths=[190,65,70,65,95],repeatRows=1,hAlign='LEFT');table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e7eff6')),('LINEBELOW',(0,0),(-1,-1),.4,colors.HexColor('#d7e2eb')),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),6)]));story.append(table)
story.append(p('The genuine-discount forecast achieved 66.7% measured interval coverage over 12 held-out origins. This regime-change example is a limitation, not suppressed evidence.','MLSmall'))
for name,title in [('overview-desktop.png','Actual application: overview'),('product-intelligence.png','Actual application: product evidence'),('time-machine.png','Actual application: historical comparison'),('mobile-sparse.png','Actual application: mobile sparse-history state')]:
    path=root/'submission'/'screenshots'/name
    if path.exists():
        story += [PageBreak(),p(title,'MLH2'),p('Synthetic demonstration data. Actual Playwright browser capture.','MLSmall')]
        w,h=ImageReader(str(path)).getSize();scale=min(480/w,630/h);story.append(Image(str(path),width=w*scale,height=h*scale))
def footer(canvas,doc):
    canvas.setStrokeColor(colors.HexColor('#d4dfe9'));canvas.line(52,42,543,42);canvas.setFont('Helvetica',8);canvas.setFillColor(colors.HexColor('#637b91'));canvas.drawString(52,28,'MarketLens AI | Synthetic demonstration and local implementation');canvas.drawRightString(543,28,str(doc.page))
SimpleDocTemplate(str(out),pagesize=(595.28,841.89),rightMargin=55,leftMargin=55,topMargin=52,bottomMargin=58,title='MarketLens AI - Project Report',author='[Student names]').build(story,onFirstPage=footer,onLaterPages=footer)
print(out)
