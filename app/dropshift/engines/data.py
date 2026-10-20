"""CSV and JSON, back and forth."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ..jobs import Job, UserError


def _read_text(src: Path) -> str:
    raw = src.read_bytes()
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1')


def csv_to_json(job: Job, src: Path, target: str = 'json') -> None:
    text = _read_text(src)
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel_tab if src.suffix.lower() == '.tsv' else csv.excel
    rows = list(csv.DictReader(text.splitlines(), dialect=dialect))
    out = job.output_for(src, 'json')
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding='utf-8')
    job.add(out)


def json_to_csv(job: Job, src: Path, target: str = 'csv') -> None:
    try:
        data = json.loads(_read_text(src))
    except json.JSONDecodeError as error:
        raise UserError(f"{src.name} isn't valid JSON (line {error.lineno})") from None
    if isinstance(data, dict):
        # {"items": [...]} is common; otherwise the object is one row.
        lists = [v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        data = lists[0] if len(lists) == 1 else [data]
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise UserError(f'{src.name} is not a list of records, so it has no columns to make')
    columns: list[str] = []
    for row in data:
        columns += [key for key in row if key not in columns]
    out = job.output_for(src, 'csv')
    with open(out, 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in data:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                             for k, v in row.items()})
    job.add(out)
