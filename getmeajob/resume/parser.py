"""Conservative section/position parsing with verbatim evidence and visible uncertainty."""
import re
import unicodedata

SECTIONS = {
    'personal information':'personal', 'contact':'personal', 'contact information':'personal',
    'education':'education','academic background':'education','university':'education','program':'education',
    'experience':'employment','work experience':'employment','professional experience':'employment',
    'employment':'employment','employment history':'employment','work history':'employment',
    'skills':'skills','technical skills':'tools','technical proficiencies':'tools','tools':'tools',
    'projects':'projects','personal projects':'projects','academic projects':'projects',
    'certifications':'certifications','certificates':'certifications','awards':'achievements','achievements':'achievements',
    'coursework':'coursework','relevant coursework':'coursework','courses':'coursework',
}
GROUPS={'Personal Information':('personal',),'Education':('education','university','program'),
        'Experience':('employment',),'Skills':('skills','tools'),'Projects':('projects',),
        'Other Information':('coursework','certifications','achievements','other')}
BULLET=re.compile(r'^\s*[•●▪◦‣\-*]\s+')
DATE=r'(?:\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+)?(?:19|20)\d{2}\b'
RANGE=re.compile(r'('+DATE+r')\s*(?:–|—|-|\bto\b)\s*(Present|Current|Now|'+DATE+r')',re.I)
ROLE=re.compile(r'\b(?:engineer|developer|analyst|intern|assistant|manager|coordinator|specialist|associate|technician|consultant|designer|officer|lead|co-op|representative)\b',re.I)
CONTACT=re.compile(r'@|https?://|www\.|linkedin\.com|github\.com|(?:\+?\d[\d ().-]{7,}\d)',re.I)
LIGATURES=str.maketrans({'ﬀ':'ff','ﬁ':'fi','ﬂ':'fl','ﬃ':'ffi','ﬄ':'ffl','ﬅ':'st','ﬆ':'st','\u00a0':' '})


def clean(text):
    value=text.translate(LIGATURES)
    notes=[]
    if value!=text: notes.append('Normalized standard ligature/nonbreaking-space typography; compare original evidence.')
    # Only standalone private-use icon tokens adjacent to an actual URL/email are decorative.
    if CONTACT.search(value):
        replaced=re.sub(r'(?<!\S)[\ue000-\uf8ff]+(?=\s|$)','',value)
        if replaced!=value:
            notes.append('Removed standalone private-use contact icon; original evidence retained.')
            value=replaced
    unusual=sorted({c for c in value if c=='\ufffd' or unicodedata.category(c) in ('Co','Cs','Cc') and c not in '\n\t\r'})
    if unusual: notes.append('Uninterpreted character(s): '+', '.join('U+%04X'%ord(c) for c in unusual)+'. Correct against the source before verifying.')
    if re.search(r'\(cid:\d+\)',value): notes.append('Unresolved PDF character ID; original text retained for correction.')
    return value.strip(),notes


def render_position(record):
    lines=[record.get('title') or '',record.get('employer') or '',record.get('location') or '']
    dates=' – '.join(filter(None,[record.get('start_date'),record.get('end_date')]))
    if dates: lines.append(dates)
    if record.get('header_unassigned'): lines.append(record['header_unassigned'])
    lines.extend('• '+item for item in record.get('achievements',[]))
    return '\n'.join(line for line in lines if line)


def position(header,bullets):
    record={k:'' for k in ('employer','title','location','start_date','end_date','header_unassigned')}
    record['achievements']=bullets
    remaining=[]
    for line in header:
        found=RANGE.search(line)
        if found:
            record['start_date'],record['end_date']=found.group(1),found.group(2)
            line=(line[:found.start()]+line[found.end():]).strip(' |,–—-\t')
        if line: remaining.extend(part.strip() for part in re.split(r'\s*\|\s*|\t+| {2,}',line) if part.strip())
    for piece in remaining:
        if piece.lower().startswith(('employer:','company:')): record['employer']=piece.split(':',1)[1].strip()
        elif piece.lower().startswith(('title:','role:')): record['title']=piece.split(':',1)[1].strip()
        elif piece.lower().startswith('location:'): record['location']=piece.split(':',1)[1].strip()
        elif ROLE.search(piece) and not record['title']: record['title']=piece
        elif not record['location'] and re.search(r',\s*(?:ON|Ontario|QC|BC|AB|Canada|US|USA|UK)\b|^(?:Remote|Hybrid)$',piece,re.I): record['location']=piece
        elif not record['employer']: record['employer']=piece
        else: record['header_unassigned']+='\n'+piece
    record['header_unassigned']=record['header_unassigned'].strip()
    return record


def parse(text):
    if len(text)>100000: raise ValueError('Resume text exceeds parser limit.')
    results=[]; section='personal'; block=[]
    def emit(category,entries,structure=None,extra=()):
        if not entries: return
        evidence='\n'.join(raw for raw,_ in entries)
        cleaned=[]; warnings=list(extra)
        for raw,line in entries:
            value,notes=clean(line); cleaned.append(value); warnings.extend(notes)
        value='\n'.join(cleaned)
        if structure is not None: value=render_position(structure)
        if value.strip(): results.append({'category':category,'value':value,'evidence':evidence,
            'structure':structure,'warnings':list(dict.fromkeys(warnings))})
    def flush():
        nonlocal block
        if not block: return
        if section=='employment':
            parse_experience(block,emit)
        else:
            current=[]
            for raw,line in block:
                is_contact=bool(CONTACT.search(line)) and not BULLET.match(line)
                if section=='personal' or is_contact:
                    if current: emit(section,current);current=[]
                    emit('personal',[(raw,line)])
                elif BULLET.match(line):
                    if current: emit(section,current)
                    current=[(raw,BULLET.sub('',line))]
                elif not line.strip():
                    if current: emit(section,current);current=[]
                elif current and (raw[:1].isspace() or line[:1].islower()): current.append((raw,line))
                else:
                    if current: emit(section,current)
                    current=[(raw,line)]
            if current: emit(section,current)
        block=[]
    for raw in text.splitlines():
        line=raw.strip(); heading=line.casefold().rstrip(':').strip()
        if heading in SECTIONS:
            flush(); section=SECTIONS[heading]; continue
        block.append((raw,line))
    flush()
    if len(results)>500: raise ValueError('Too many extracted records; use a shorter resume.')
    return results


def parse_experience(entries,emit):
    header=[]; bullets=[]; evidence=[]; warnings=[]; last_blank=False
    def finish():
        nonlocal header,bullets,evidence,warnings
        if evidence:
            clean_header=[]
            for line in header:
                value,notes=clean(line); clean_header.append(value); warnings.extend(notes)
            cleaned_bullets=[]
            for bullet in bullets:
                value,notes=clean(bullet);cleaned_bullets.append(value);warnings.extend(notes)
            record=position(clean_header,cleaned_bullets)
            if not record['title'] or not record['employer']: warnings.append('Position header is ambiguous; confirm employer and job title. Unknown fields were left empty.')
            emit('employment',evidence,record,warnings)
        header=[];bullets=[];evidence=[];warnings=[]
    for raw,line in entries:
        if not line:
            last_blank=True
            if evidence: evidence.append((raw,line))
            continue
        marked=BULLET.match(line)
        # A role/date header after achievements starts a new position, never a continuation.
        new_header=bullets and not marked and (RANGE.search(line) or ((not raw[:1].isspace() and not line[:1].islower()) and (ROLE.search(line) or last_blank)))
        if new_header: finish()
        evidence.append((raw,line))
        if marked: bullets.append(BULLET.sub('',line))
        elif not bullets: header.append(line)
        elif not last_blank and (raw[:1].isspace() or line[:1].islower()): bullets[-1]+=' '+line
        else:
            bullets.append(line); warnings.append('Unmarked achievement boundary: confirm that these are separate bullets.')
        last_blank=False
    finish()
