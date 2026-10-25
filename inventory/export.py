"""Small dependency-free OOXML writer used by the installed application."""
from pathlib import Path
import re
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

HEADERS = ["ISBN", "TITLE", "LANGUAGE", "EDITABLE OR SCANNED", "PAGE COUNT", "IMAGE COUNT"]
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'


def column(number):
    result = ""
    while number:
        number, rest = divmod(number - 1, 26)
        result = chr(65 + rest) + result
    return result


def worksheet(rows, widths):
    cells = []
    for ri, row in enumerate(rows, 1):
        values = []
        for ci, value in enumerate(row, 1):
            ref = f"{column(ci)}{ri}"
            if isinstance(value, (int, float)):
                values.append(f'<c r="{ref}" s="{1 if ri == 1 else 0}"><v>{value}</v></c>')
            else:
                # Inline strings cannot become formulas, even if a book title starts '='.
                text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))[:32767]
                values.append(f'<c r="{ref}" s="{1 if ri == 1 else 0}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>')
        line_count = max(str(value).count('\n') + 1 for value in row)
        height = min(150, max(22, 16 * line_count))
        cells.append(f'<row r="{ri}" ht="{height}" customHeight="1">' + "".join(values) + '</row>')
    cols = ''.join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i, width in enumerate(widths, 1))
    last = f"{column(len(rows[0]))}{len(rows)}"
    return (XML + f'<worksheet xmlns="{NS}"><dimension ref="A1:{last}"/>'
            '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            '<sheetFormatPr defaultRowHeight="22"/><cols>' + cols + '</cols><sheetData>' + ''.join(cells)
            + f'</sheetData><autoFilter ref="A1:{last}"/></worksheet>')


def export_workbook(path: Path, results: list[dict], cancelled=False, run_status=None):
    inventory = [HEADERS]
    review = [["INVENTORY ROW", "SOURCE FILES", "REVIEW NOTES", "LANGUAGE CONFIDENCE", "IMAGE COUNT METHOD", "EXTERNAL IMAGE FILES", "RUN STATUS"]]
    for index, result in enumerate(results, 2):
        inventory.append([result[key] for key in ("isbn", "title", "language", "book_type", "page_count", "image_count")])
        review.append([index, "\n".join(result["sources"]), "\n".join(result["notes"]), result["language_confidence"],
                       result["image_method"], result["external_image_files"], run_status or ("PARTIAL - CANCELLED" if cancelled else "COMPLETE")])
    if not results:
        review.append(["", "", "No completed books", "", "", "", "PARTIAL - CANCELLED" if cancelled else "COMPLETE"])
    temporary = path.with_name(path.stem + ".tmp.xlsx")
    with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', XML + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr('_rels/.rels', XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', XML + f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Inventory" sheetId="1" r:id="rId1"/><sheet name="Review" sheetId="2" r:id="rId2"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        archive.writestr('xl/styles.xml', XML + f'<styleSheet xmlns="{NS}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF0F172A"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        archive.writestr('xl/worksheets/sheet1.xml', worksheet(inventory, [22, 65, 20, 26, 18, 18]))
        archive.writestr('xl/worksheets/sheet2.xml', worksheet(review, [20, 70, 90, 25, 75, 26, 28]))
    temporary.replace(path)
