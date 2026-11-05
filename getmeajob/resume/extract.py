"""Bounded document extraction in a disposable, resource-limited subprocess."""
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

MAX_FILE=8*1024*1024
MAX_TEXT=100000

def validate(filename,data):
    suffix=Path(filename).suffix.lower()
    if not data or len(data)>MAX_FILE or suffix not in ('.pdf','.docx'):
        raise ValueError('Upload a PDF or DOCX up to 8 MiB.')
    if suffix=='.pdf':
        if not data.startswith(b'%PDF-'): raise ValueError('Invalid PDF signature.')
    else:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members=archive.infolist()
                if len(members)>500 or sum(item.file_size for item in members)>20*1024*1024:
                    raise ValueError('DOCX exceeds safe expanded size.')
                if 'word/document.xml' not in archive.namelist(): raise ValueError('Invalid DOCX.')
                for item in members:
                    if '..' in Path(item.filename).parts or item.filename.startswith('/') or item.flag_bits & 1 or item.filename.endswith('vbaProject.bin'):
                        raise ValueError('Unsafe DOCX member.')
                    if item.filename.endswith('.rels') and b'TargetMode="External"' in archive.read(item):
                        raise ValueError('DOCX external relationships are not accepted. Save a copy without external links.')
        except (zipfile.BadZipFile,RuntimeError) as exc: raise ValueError('Invalid DOCX archive.') from exc
    return suffix


def extract(filename,data):
    suffix=validate(filename,data)
    try:
        result=subprocess.run([sys.executable,'-m','getmeajob.resume.extract',suffix],input=data,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15,check=False)
        if result.returncode: raise ValueError('Document could not be safely read. Encrypted, scanned or damaged files need a text-based copy.')
        text=json.loads(result.stdout)
    except (subprocess.TimeoutExpired,ValueError) as exc: raise ValueError('Document extraction failed or timed out. Use a text-based PDF/DOCX.') from exc
    if not isinstance(text,str) or not text.strip(): raise ValueError('No readable text found. OCR is not installed; use a text-based copy.')
    if len(text)>MAX_TEXT: raise ValueError('Resume text is too long.')
    return text


def candidates(text):
    aliases={'education':'education','university':'university','coursework':'coursework','relevant coursework':'coursework',
        'experience':'employment','employment history':'employment','skills':'skills','technical skills':'tools',
        'tools':'tools','projects':'projects','certifications':'certifications','achievements':'achievements','program':'program'}
    category='education'; results=[]
    for line in text.splitlines():
        line=line.strip().strip('•').strip()
        if line.casefold().rstrip(':') in aliases:
            category=aliases[line.casefold().rstrip(':')]; continue
        if line: results.append((category,line[:2000]))
        if len(results)>=150: break
    return results


def worker(suffix,data):
    import resource
    resource.setrlimit(resource.RLIMIT_CPU,(10,10))
    if sys.platform!='darwin': resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
    if suffix=='.pdf':
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(data),strict=True)
        if reader.is_encrypted or len(reader.pages)>30: raise ValueError('PDF is encrypted or too long.')
        text='\n'.join((page.extract_text() or '')[:MAX_TEXT] for page in reader.pages)
    else:
        from docx import Document
        document=Document(io.BytesIO(data))
        text='\n'.join([p.text for p in document.paragraphs]+[cell.text for table in document.tables for row in table.rows for cell in row.cells])
    if len(text)>MAX_TEXT: raise ValueError('Text too long.')
    return text

if __name__=='__main__':
    try: print(json.dumps(worker(sys.argv[1],sys.stdin.buffer.read(MAX_FILE+1))))
    except Exception: sys.exit(1)
