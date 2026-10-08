"""Where registrations are kept: a Google Sheet (production) or memory (tests/local)."""

import json
import threading

HEADERS = [
    "Registration date", "Reference", "Full name", "Email", "WhatsApp / phone", "Country",
    "Experience level", "Program", "Payment option", "Currency", "Amount due", "Amount paid",
    "Payment status", "Paid at", "Channel", "Notes",
]
COL = {h: i for i, h in enumerate(HEADERS)}


def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


LAST_COL = _col_letter(len(HEADERS) - 1)


def _to_record(values: list) -> dict:
    values = list(values) + [""] * (len(HEADERS) - len(values))
    return {h: values[i] for i, h in enumerate(HEADERS)}


class MemoryStore:
    def __init__(self):
        self.rows: list[dict] = []
        self._lock = threading.Lock()

    def append(self, record: dict) -> None:
        with self._lock:
            self.rows.append({h: record.get(h, "") for h in HEADERS})

    def find(self, reference: str):
        for i, r in enumerate(self.rows):
            if r["Reference"] == reference:
                return i, dict(r)
        return None, None

    def update(self, row_id, fields: dict) -> None:
        with self._lock:
            self.rows[row_id].update(fields)

    def all(self):
        return [(i, dict(r)) for i, r in enumerate(self.rows)]


class SheetStore:
    """Google Sheets via the REST API and a service account. Row ids are 1-based sheet row numbers."""

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    def __init__(self, service_account_json: str, sheet_id: str, tab: str = "Registrations"):
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession

        if not service_account_json or not sheet_id:
            raise RuntimeError("Registration sheet is not configured.")
        info = json.loads(service_account_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=self.SCOPES)
        self.session = AuthorizedSession(creds)
        self.sheet_id = sheet_id
        self.tab = tab
        self.base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
        self._ready = False

    def _rng(self, a1: str) -> str:
        return f"'{self.tab}'!{a1}"

    def _call(self, method: str, url: str, **kw) -> dict:
        r = self.session.request(method, url, timeout=20, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"Google Sheets error {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    def ensure_ready(self) -> None:
        if self._ready:
            return
        meta = self._call("GET", self.base, params={"fields": "sheets.properties"})
        tabs = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}
        requests = []
        if self.tab not in tabs:
            res = self._call("POST", self.base + ":batchUpdate",
                             json={"requests": [{"addSheet": {"properties": {"title": self.tab}}}]})
            tab_id = res["replies"][0]["addSheet"]["properties"]["sheetId"]
        else:
            tab_id = tabs[self.tab]
        head = self._call("GET", f"{self.base}/values/{self._rng(f'A1:{LAST_COL}1')}")
        if (head.get("values") or [[]])[0] != HEADERS:
            self._call("PUT", f"{self.base}/values/{self._rng(f'A1:{LAST_COL}1')}",
                       params={"valueInputOption": "RAW"}, json={"values": [HEADERS]})
            requests += [
                {"repeatCell": {
                    "range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {"userEnteredFormat": {
                        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                        "backgroundColor": {"red": 0.04, "green": 0.1, "blue": 0.23}}},
                    "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
                {"updateSheetProperties": {
                    "properties": {"sheetId": tab_id, "gridProperties": {"frozenRowCount": 1}},
                    "fields": "gridProperties.frozenRowCount"}},
            ]
            for text, bg in [("Paid", (0.9, 0.96, 0.94)), ("Pending", (1, 0.95, 0.86)),
                             ("Plan", (0.92, 0.95, 1)), ("Needs review", (0.99, 0.93, 0.92)),
                             ("Unpaid", (0.95, 0.95, 0.96))]:
                requests.append({"addConditionalFormatRule": {"index": 0, "rule": {
                    "ranges": [{"sheetId": tab_id, "startRowIndex": 1,
                                "startColumnIndex": COL["Payment status"], "endColumnIndex": COL["Payment status"] + 1}],
                    "booleanRule": {"condition": {"type": "TEXT_STARTS_WITH", "values": [{"userEnteredValue": text}]},
                                    "format": {"backgroundColor": {"red": bg[0], "green": bg[1], "blue": bg[2]}}}}}})
            self._call("POST", self.base + ":batchUpdate", json={"requests": requests})
        self._ready = True

    def append(self, record: dict) -> None:
        self.ensure_ready()
        row = [record.get(h, "") for h in HEADERS]
        # RAW: values are stored as typed, never parsed as formulas.
        self._call("POST", f"{self.base}/values/{self._rng(f'A:{LAST_COL}')}:append",
                   params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                   json={"values": [row]})

    def find(self, reference: str):
        self.ensure_ready()
        col = _col_letter(COL["Reference"])
        res = self._call("GET", f"{self.base}/values/{self._rng(f'{col}:{col}')}")
        for i, v in enumerate(res.get("values", [])):
            if v and v[0] == reference:
                row = i + 1
                data = self._call("GET", f"{self.base}/values/{self._rng(f'A{row}:{LAST_COL}{row}')}",
                                  params={"valueRenderOption": "UNFORMATTED_VALUE"})
                return row, _to_record((data.get("values") or [[]])[0])
        return None, None

    def update(self, row_id, fields: dict) -> None:
        data = [{"range": self._rng(f"{_col_letter(COL[h])}{row_id}"), "values": [[v]]} for h, v in fields.items()]
        self._call("POST", f"{self.base}/values:batchUpdate",
                   json={"valueInputOption": "RAW", "data": data})

    def all(self):
        self.ensure_ready()
        res = self._call("GET", f"{self.base}/values/{self._rng(f'A2:{LAST_COL}')}",
                         params={"valueRenderOption": "UNFORMATTED_VALUE"})
        return [(i + 2, _to_record(v)) for i, v in enumerate(res.get("values", []))]
