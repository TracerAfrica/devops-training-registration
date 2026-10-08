"""SheetStore against a fake Google Sheets REST API (no network)."""

import os
import re
import sys
import unittest
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracer.store import HEADERS, SheetStore  # noqa: E402


class Resp:
    def __init__(self, data):
        import json
        self.status_code, self._d = 200, data
        self.content = json.dumps(data).encode()
        self.text = self.content.decode()

    def json(self):
        return self._d


class FakeSheets:
    """Single-tab grid; supports the calls SheetStore makes."""

    def __init__(self):
        self.grid, self.tabs, self.batch = [], {"Sheet1": 0}, []

    @staticmethod
    def _cell(a1):
        m = re.match(r"([A-Z]+)(\d*)", a1)
        col = 0
        for ch in m.group(1):
            col = col * 26 + ord(ch) - 64
        return col - 1, (int(m.group(2)) - 1 if m.group(2) else None)

    def _range(self, rng):
        a1 = unquote(rng).split("!")[1]
        a, b = (a1.split(":") + [a1])[:2]
        return self._cell(a), self._cell(b)

    def _get(self, rng):
        (c0, r0), (c1, r1) = self._range(rng)
        r0 = r0 or 0
        r1 = len(self.grid) - 1 if r1 is None else r1
        out = [row[c0:c1 + 1] for row in self.grid[r0:r1 + 1]]
        while out and not any(out[-1]):
            out.pop()
        return out

    def _put(self, rng, values):
        (c0, r0), _ = self._range(rng)
        for i, vals in enumerate(values):
            while len(self.grid) <= r0 + i:
                self.grid.append([""] * len(HEADERS))
            for j, v in enumerate(vals):
                self.grid[r0 + i][c0 + j] = v

    def request(self, method, url, timeout=None, params=None, json=None):
        path = url.split("/spreadsheets/sid", 1)[1]
        if path == "" and method == "GET":
            return Resp({"sheets": [{"properties": {"title": t, "sheetId": i}} for t, i in self.tabs.items()]})
        if path == ":batchUpdate":
            self.batch.append(json)
            for req in json["requests"]:
                if "addSheet" in req:
                    self.tabs[req["addSheet"]["properties"]["title"]] = 7
                    return Resp({"replies": [{"addSheet": {"properties": {"sheetId": 7}}}]})
            return Resp({})
        if path == "/values:batchUpdate":
            for d in json["data"]:
                self._put(d["range"], d["values"])
            return Resp({})
        m = re.match(r"/values/(.+?)(:append)?$", path)
        rng, append = m.group(1), m.group(2)
        if append:
            self.grid.append(list(json["values"][0]))
            return Resp({})
        if method == "PUT":
            self._put(rng, json["values"])
            return Resp({})
        return Resp({"values": self._get(rng)})


class SheetStoreTests(unittest.TestCase):
    def test_round_trip(self):
        st = SheetStore.__new__(SheetStore)
        st.session, st.sheet_id, st.tab, st._ready = FakeSheets(), "sid", "Registrations", False
        st.base = "https://sheets.googleapis.com/v4/spreadsheets/sid"
        fake = st.session

        st.append({"Reference": "TA-1", "Full name": "Ada Okafor", "Amount due": 300, "Payment status": "Pending payment"})
        st.append({"Reference": "TA-2", "Full name": "Bayo Ade", "Amount due": 600, "Payment status": "Pending payment"})
        self.assertIn("Registrations", fake.tabs)                 # tab created
        self.assertEqual(fake.grid[0], HEADERS)                    # header written
        self.assertTrue(any("addConditionalFormatRule" in r for b in fake.batch for r in b["requests"]))

        row, rec = st.find("TA-2")
        self.assertEqual((row, rec["Full name"], rec["Amount due"]), (3, "Bayo Ade", 600))
        self.assertEqual(st.find("TA-9"), (None, None))

        st.update(row, {"Payment status": "Paid", "Amount paid": 600, "Channel": "card"})
        rec = st.find("TA-2")[1]
        self.assertEqual((rec["Payment status"], rec["Amount paid"], rec["Channel"]), ("Paid", 600, "card"))
        self.assertEqual(st.find("TA-1")[1]["Payment status"], "Pending payment")   # other row untouched

        rows = st.all()
        self.assertEqual([r for r, _ in rows], [2, 3])

        # Second store instance on an existing sheet doesn't rewrite the header.
        n = len(fake.batch)
        st2 = SheetStore.__new__(SheetStore)
        st2.session, st2.sheet_id, st2.tab, st2._ready, st2.base = fake, "sid", "Registrations", False, st.base
        st2.ensure_ready()
        self.assertEqual(len(fake.batch), n)


if __name__ == "__main__":
    unittest.main()
