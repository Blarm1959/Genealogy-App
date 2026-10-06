"""Loss-aware GEDCOM reader. Raw records are retained alongside interpreted fields."""
from dataclasses import dataclass, field
import io
import re
import zipfile
from pathlib import PurePosixPath

MAX_FILE = 25 * 1024 * 1024
MAX_EXPANDED = 50 * 1024 * 1024

@dataclass
class Node:
    tag: str
    value: str = ''
    ref: str = ''
    children: list = field(default_factory=list)

    def all(self, tag):
        return [n for n in self.children if n.tag == tag]

    def one(self, tag):
        return next(iter(self.all(tag)), Node(tag))

    def text(self):
        text = self.value
        for n in self.children:
            if n.tag == 'CONT': text += '\n' + n.text()
            elif n.tag == 'CONC': text += n.text()
        return text

    def raw(self):
        return {'tag': self.tag, 'value': self.value, 'ref': self.ref,
                'children': [n.raw() for n in self.children]}


def gedcom_bytes(data: bytes, name: str):
    if len(data) > MAX_FILE: raise ValueError('Upload exceeds 25 MiB.')
    if name.lower().endswith('.zip'):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if len(z.infolist()) > 5000: raise ValueError('ZIP has too many entries.')
                if sum(i.file_size for i in z.infolist()) > MAX_EXPANDED:
                    raise ValueError('ZIP expands beyond 50 MiB.')
                for i in z.infolist():
                    path = PurePosixPath(i.filename.replace('\\', '/'))
                    if path.is_absolute() or '..' in path.parts or (path.parts and ':' in path.parts[0]):
                        raise ValueError('ZIP contains an unsafe path.')
                    if i.flag_bits & 1: raise ValueError('Encrypted ZIP files are unsupported.')
                candidates = [i for i in z.infolist() if not i.is_dir() and i.filename.lower().endswith(('.ged', '.gedcom'))]
                if len(candidates) != 1:
                    raise ValueError('ZIP must contain exactly one GEDCOM; upload trees separately.')
                item = candidates[0]
                if item.file_size > MAX_FILE: raise ValueError('GEDCOM exceeds 25 MiB.')
                other = [i.filename for i in z.infolist() if not i.is_dir() and i != item]
                return z.read(item), item.filename, other
        except (zipfile.BadZipFile, RuntimeError) as e:
            raise ValueError('Unable to read ZIP.') from e
    if not name.lower().endswith(('.ged', '.gedcom')): raise ValueError('Choose a GEDCOM or ZIP file.')
    return data, name, []


def decode(data):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')): return data.decode('utf-16'), 'UTF-16'
    if re.search(rb'(?im)^1 CHAR ANSEL\s*$', data):
        raise ValueError('ANSEL encoding is not supported yet. Re-export as UTF-8; original data has not been imported.')
    try: return data.decode('utf-8-sig'), 'UTF-8'
    except UnicodeDecodeError:
        if re.search(rb'(?im)^1 CHAR (ANSI|ASCII)\s*$', data):
            return data.decode('cp1252'), 'Windows-1252'
        raise ValueError('Unknown encoding. Re-export the GEDCOM as UTF-8.')


def parse(data: bytes):
    text, encoding = decode(data)
    roots, stack, warnings = [], [], []
    pattern = re.compile(r'^(\d+)\s+(?:(@[^@\s]+@)\s+)?([A-Za-z0-9_]+)(?:\s(.*))?$')
    for num, line in enumerate(text.splitlines(), 1):
        if not line.strip(): continue
        m = pattern.match(line)
        if not m: raise ValueError(f'Invalid GEDCOM syntax on line {num}.')
        level = int(m[1])
        if level > 30: raise ValueError('GEDCOM nesting exceeds 30 levels.')
        if level > len(stack): raise ValueError(f'Missing parent level on line {num}.')
        node = Node(m[3].upper(), m[4] or '', m[2] or '')
        if level == 0: roots.append(node)
        else: stack[level - 1].children.append(node)
        stack = stack[:level] + [node]
    if not roots or roots[0].tag != 'HEAD': raise ValueError('GEDCOM must start with HEAD.')
    if not any(n.tag == 'TRLR' for n in roots): warnings.append('Missing TRLR end marker.')
    version = roots[0].one('GEDC').one('VERS').value or 'Unknown'
    if not version.startswith(('5.5', '7.')): warnings.append(f'Unrecognised GEDCOM version: {version}; review carefully.')
    if any(n.tag in ('INDI', 'FAM', 'SOUR') and not n.ref for n in roots):
        raise ValueError('Person, family and source records must have identifiers.')
    ids = [n.ref for n in roots if n.ref]
    if len(ids) != len(set(ids)): raise ValueError('Duplicate GEDCOM record identifiers.')
    by_id = {n.ref: n for n in roots if n.ref}
    persons, families, sources, media = [], [], [], []
    unresolved = set()
    def resolve_text(node):
        if node.value.startswith('@') and node.value.endswith('@'):
            found = by_id.get(node.value)
            if found: return found.text()
            unresolved.add(node.value)
        return node.text()
    def citations(node):
        result = []
        for s in node.all('SOUR'):
            target = by_id.get(s.value)
            result.append({'ref': s.value, 'title': target.one('TITL').text() if target else s.value,
                           'page': s.one('PAGE').text(), 'text': s.one('DATA').one('TEXT').text(),
                           'raw': s.raw()})
            if s.value.startswith('@') and not target: unresolved.add(s.value)
        return result
    event_tags = {'BIRT','DEAT','BURI','CREM','CHR','BAPM','ADOP','RESI','CENS','OCCU','EDUC','EMIG','IMMI','NATU','EVEN','FACT','RETI','PROB','WILL'}
    for n in roots:
        if n.tag == 'INDI':
            names = [x.text().replace('/', '').strip() for x in n.all('NAME')]
            events = [{'type': e.tag, 'date': e.one('DATE').text(), 'place': e.one('PLAC').text(),
                       'value': e.value, 'citations': citations(e), 'raw': e.raw()}
                      for e in n.children if e.tag in event_tags]
            persons.append({'xref': n.ref, 'name': names[0] if names else '(Unnamed)', 'names': names,
                            'sex': n.one('SEX').value, 'events': events,
                            'notes': [resolve_text(x) for x in n.all('NOTE')],
                            'citations': citations(n), 'raw': n.raw()})
        elif n.tag == 'FAM':
            families.append({'xref': n.ref, 'parents': [x.value for x in n.children if x.tag in ('HUSB','WIFE')],
                             'children': [x.value for x in n.all('CHIL')], 'raw': n.raw()})
        elif n.tag == 'SOUR': sources.append({'xref': n.ref, 'title': n.one('TITL').text(), 'raw': n.raw()})
        if n.tag == 'OBJE': media.append(n.raw())
    if not persons: raise ValueError('No person records were found.')
    if len(persons) > 50000: raise ValueError('More than 50,000 people; split the tree before importing.')
    references = []
    def visit(n):
        if n.value.startswith('@') and n.value.endswith('@') and n.value not in by_id: unresolved.add(n.value)
        if n.tag == 'FILE': references.append(n.value)
        for c in n.children: visit(c)
    for n in roots: visit(n)
    warnings += [f'Unresolved reference: {r}' for r in sorted(unresolved)[:100]]
    if references: warnings.append(f'{len(references)} media references retained; linked images are not fetched automatically.')
    warnings.append('Imported claims are unverified. An export alone cannot reveal every item omitted by the original service.')
    return {'persons': persons, 'families': families, 'sources': sources, 'media': media,
            'records': [n.raw() for n in roots], 'report': {
                'people': len(persons), 'families': len(families), 'sources': len(sources),
                'media_references': len(references), 'encoding': encoding, 'gedcom_version': version,
                'warnings': warnings, 'unresolved_references': sorted(unresolved),
                'preservation': 'Original upload, all raw tags, source records, citations, notes and media references retained. Unknown tags are retained but may not be displayed.'}}
