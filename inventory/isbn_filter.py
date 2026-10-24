"""Read ISBN lists from XLSX without loading formulas or executing workbook content."""
from io import BytesIO
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET
from .metadata import find_isbns
from .titles import canonical_isbn

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def read_isbn_sheet(data):
    try:
        with ZipFile(BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 30_000_000:
                raise ValueError('Excel file is too large.')
            strings=[]
            if 'xl/sharedStrings.xml' in archive.namelist():
                strings=[''.join(node.itertext()) for node in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si',NS)]
            sheets=sorted(name for name in archive.namelist() if name.startswith('xl/worksheets/sheet') and name.endswith('.xml'))
            if not sheets:
                raise ValueError('No worksheets found.')
            values=[]
            # First worksheet is the uploaded ISBN list; other report tabs are ignored.
            sheet=ET.fromstring(archive.read(sheets[0]))
            rows=[]
            for row in sheet.findall('s:sheetData/s:row',NS):
                cells={}
                for cell in row.findall('s:c',NS):
                    column=''.join(c for c in cell.get('r','') if c.isalpha())
                    value=cell.find('s:v',NS)
                    text=value.text if value is not None and value.text else ''
                    if cell.get('t')=='s':
                        text=strings[int(text)]
                    elif cell.get('t')=='inlineStr':
                        text=''.join(node.text or '' for node in cell.findall('.//s:t',NS))
                    cells[column]=text
                rows.append(cells)
            isbn_columns={col for row in rows[:5] for col,value in row.items() if value.strip().upper().replace('_',' ').replace('-',' ') in ('ISBN','ISBN 10','ISBN 13','ISBN NUMBER')}
            for row in rows:
                for col,value in row.items():
                    if isbn_columns and col not in isbn_columns:
                        continue
                    # Excel numeric cells can contain exponent notation.
                    try:
                        if 'e' in value.lower() or value.endswith('.0'):
                            from decimal import Decimal
                            value=format(Decimal(value),'f').split('.')[0]
                    except Exception:
                        pass
                    for isbn in find_isbns(value):
                        key=canonical_isbn(isbn)
                        if key and key not in values:
                            values.append(key)
            if not values:
                raise ValueError('No valid ISBN numbers found in the first worksheet. Use an ISBN column.')
            return values
    except (BadZipFile,ET.ParseError,KeyError,IndexError) as exc:
        raise ValueError('Upload a valid .xlsx Excel file.') from exc


def select_books(books, requested):
    requested=set(requested)
    selected,found=[],set()
    for book in books:
        keys=set()
        for path in book.files:
            # Filename first; book-folder names support generic PDF filenames.
            for name in (path.stem,*[parent.name for parent in path.parents]):
                keys.update(filter(None,(canonical_isbn(isbn) for isbn in find_isbns(name))))
        matches=keys & requested
        if matches:
            selected.append(book)
            found.update(matches)
    return selected,sorted(requested-found)
