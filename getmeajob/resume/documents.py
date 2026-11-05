"""In-memory exports of approved package versions; no conversion services or external links."""
import io
from xml.sax.saxutils import escape


def export(text,kind):
    if len(text)>30000: raise ValueError('Letter is too long to export.')
    if kind=='txt': return text.encode('utf-8'),'text/plain; charset=utf-8'
    if kind=='docx':
        from docx import Document
        from docx.shared import Inches,Pt
        document=Document()
        section=document.sections[0]; section.top_margin=section.bottom_margin=Inches(.8)
        style=document.styles['Normal']; style.font.name='Calibri'; style.font.size=Pt(11)
        for paragraph in text.split('\n\n'): document.add_paragraph(paragraph)
        document.core_properties.author=''; document.core_properties.last_modified_by=''
        stream=io.BytesIO(); document.save(stream)
        return stream.getvalue(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    if kind=='pdf':
        from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.pagesizes import LETTER
        stream=io.BytesIO(); styles=getSampleStyleSheet()
        content=[]
        for paragraph in text.split('\n\n'):
            content.extend([Paragraph(escape(paragraph).replace('\n','<br/>'),styles['Normal']),Spacer(1,12)])
        def paper(canvas,document):
            canvas.saveState(); canvas.setFillColorRGB(1,1,1)
            canvas.rect(0,0,LETTER[0],LETTER[1],fill=1,stroke=0); canvas.restoreState()
        SimpleDocTemplate(stream,pagesize=LETTER,topMargin=54,bottomMargin=54).build(content,onFirstPage=paper,onLaterPages=paper)
        return stream.getvalue(),'application/pdf'
    raise ValueError('Unsupported export format.')
