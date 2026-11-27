"""Small XLSX writer for downloadable, single-sheet admin reports."""

import io
import math
import re
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape

WORKSHEET_ROW_END = "</row>"


def _text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Có" if value else "Không"
    value = str(value)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", value)[:32767]


def _column(index):
    result = ""
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(65 + digit) + result
    return result


def _cell(address, value, style):
    if isinstance(value, datetime):
        serial = (value - datetime(1899, 12, 30)).total_seconds() / 86400
        date_style = 6 if style == 4 else 7
        return f'<c r="{address}" s="{date_style}"><v>{serial:.10f}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return f'<c r="{address}" s="{style}"><v>{value}</v></c>'
    return f'<c r="{address}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(_text(value))}</t></is></c>'


STYLES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="dd/mm/yyyy hh:mm:ss"/></numFmts>
<fonts count="4">
<font><sz val="11"/><name val="Aptos"/><color rgb="FF172334"/></font>
<font><b/><sz val="17"/><name val="Aptos Display"/><color rgb="FFFFFFFF"/></font>
<font><sz val="10"/><name val="Aptos"/><color rgb="FFB8D9EA"/></font>
<font><b/><sz val="11"/><name val="Aptos"/><color rgb="FFFFFFFF"/></font>
</fonts>
<fills count="5">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF10283D"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF126E98"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFF1F6FA"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left/><right/><top/><bottom style="hair"><color rgb="FFDCE6EE"/></bottom><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="8">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center" indent="1"/></xf>
<xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center" indent="2"/></xf>
<xf numFmtId="0" fontId="3" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center" wrapText="1" indent="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"><alignment vertical="center" wrapText="1" indent="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="4" borderId="1" xfId="0" applyFill="1" applyBorder="1"><alignment vertical="center" wrapText="1" indent="1"/></xf>
<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"><alignment vertical="center" indent="1"/></xf>
<xf numFmtId="164" fontId="0" fillId="4" borderId="1" xfId="0" applyNumberFormat="1" applyFill="1" applyBorder="1"><alignment vertical="center" indent="1"/></xf>
</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def make_workbook(title, subtitle, headers, rows):
    """Return an Excel workbook with readable columns and no executable formulas."""
    output = io.BytesIO()
    last_column = _column(len(headers))
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>''')
        archive.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''')
        archive.writestr("xl/workbook.xml", '''<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Du lieu" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        archive.writestr("xl/_rels/workbook.xml.rels", '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>''')
        archive.writestr("xl/styles.xml", STYLES)
        with archive.open("xl/worksheets/sheet1.xml", "w") as sheet:
            def write(chunk):
                sheet.write(chunk.encode("utf-8"))

            write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">')
            write('<sheetViews><sheetView workbookViewId="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
            write('<sheetFormatPr defaultRowHeight="18"/><cols>')
            widths = []
            for index, header in enumerate(headers, 1):
                width = min(54, max(19, len(str(header)) * 1.5 + 6))
                if any(word in str(header) for word in ("Nội dung", "Sự kiện", "Lý do", "Giá trị", "Google Maps")):
                    width = 54
                elif any(word in str(header) for word in ("Thời gian", "Cập nhật", "Bắt đầu", "Kết thúc", "Ngày tạo", "Hết hạn")):
                    width = max(width, 24)
                widths.append(width)
                write(f'<col min="{index}" max="{index}" width="{width:.1f}" customWidth="1"/>')
            write('</cols><sheetData>')
            for number, value, style, height in ((1, title, 1, 34), (2, subtitle, 2, 25)):
                write(f'<row r="{number}" ht="{height}" customHeight="1">')
                for index in range(1, len(headers) + 1):
                    write(_cell(f'{_column(index)}{number}', value if index == 1 else "", style))
                write(WORKSHEET_ROW_END)
            write('<row r="4" ht="30" customHeight="1">')
            for index, header in enumerate(headers, 1):
                write(_cell(f'{_column(index)}4', header, 3))
            write(WORKSHEET_ROW_END)
            last_row = 4
            for last_row, values in enumerate(rows, 5):
                style = 4 if last_row % 2 else 5
                lines = max((sum(max(1, math.ceil(len(part) / max(1, int(width) - 3))) for part in _text(value).splitlines() or [""]) for value, width in zip(values, widths)), default=1)
                height = min(84, max(22, lines * 16 + 6))
                write(f'<row r="{last_row}" ht="{height}" customHeight="1">')
                for index, value in enumerate(values, 1):
                    write(_cell(f'{_column(index)}{last_row}', value, style))
                write(WORKSHEET_ROW_END)
            write('</sheetData>')
            write(f'<autoFilter ref="A4:{last_column}{last_row}"/>')
            if len(headers) > 1:
                write(f'<mergeCells count="2"><mergeCell ref="A1:{last_column}1"/><mergeCell ref="A2:{last_column}2"/></mergeCells>')
            write('</worksheet>')
    return output.getvalue()
