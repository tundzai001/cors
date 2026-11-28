"""Independent current-PRN/status cross-check from Japan's Cabinet Office."""
from datetime import datetime, timezone
from html.parser import HTMLParser
import re

QZSS_STATUS_URL = "https://qzss.go.jp/en/technical/satellites/index.html"


class _StatusTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.system = ""
        self.rows = []
        self.cells = None
        self.cell = None
        self.status = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("name") in {"GPS", "GLONASS", "BeiDou", "Galileo", "QZSS", "IRNSS"}:
            self.system = attrs["name"]
        elif tag == "tr":
            self.cells = []
            self.status = attrs.get("class", "")
        elif tag == "td" and self.cells is not None:
            self.cell = []

    def handle_data(self, text):
        if self.cell is not None:
            self.cell.append(text)

    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None:
            self.cells.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.cells is not None:
            if len(self.cells) == 7:
                self.rows.append((self.system, self.status, self.cells))
            self.cells = None


def _qzss_publication_date(html):
    date = re.search(r"\b([A-Z][a-z]{2}\.\s*\d{1,2},\s*\d{4})\b", html)
    if not date:
        raise ValueError("Cabinet Office status publication date is missing")
    published = datetime.strptime(re.sub(r"\s+", "", date[1]), "%b.%d,%Y").replace(tzinfo=timezone.utc)
    if published > datetime.now(timezone.utc):
        raise ValueError("Cabinet Office status date is in the future")
    return published


def _qzss_norad_by_svn(identifiers):
    block = identifiers.split("+SATELLITE/IDENTIFIER", 1)[1].split("-SATELLITE/IDENTIFIER", 1)[0]
    return {
        fields[0]: fields[2].lstrip("0")
        for line in block.splitlines()
        if len(fields := line.split()) >= 3 and re.fullmatch(r"[GRECJ]\d{3}", fields[0])
    }


def _qzss_record(system, status, cells, norad_by_svn):
    prefixes = {"GPS": "G", "BeiDou": "C", "Galileo": "E", "QZSS": "J"}
    prefix = prefixes.get(system)
    if not prefix:
        return None
    prn, svn = cells[:2]
    svn = svn if svn.startswith(prefix) else prefix + svn.zfill(3)
    norad = norad_by_svn.get(svn)
    if not norad:
        return None
    match = re.fullmatch(r"[GECJ]?(\d{1,3})", prn)
    if match and prefix == "J":
        code = f"{prefix}{int(match[1]):03d}"
    else:
        code = f"{prefix}{int(match[1]):02d}" if match else None
    if not code and status != "retired":
        return None
    record = {
        "code": code,
        "status": ("unusable" if code else "retired") if status == "retired" else status or "operational",
    }
    return norad, record


def parse_qzss_status(html: str, identifiers: str) -> dict:
    published = _qzss_publication_date(html)
    norad_by_svn = _qzss_norad_by_svn(identifiers)
    parser = _StatusTable()
    parser.feed(html)
    records = {}
    for system, status, cells in parser.rows:
        record = _qzss_record(system, status, cells, norad_by_svn)
        if record:
            norad, value = record
            records[norad] = value
    if len(records) < 10:
        raise ValueError("Cabinet Office status table is incomplete")
    return {"source": QZSS_STATUS_URL, "published_at_utc": published.isoformat(), "records": records}
