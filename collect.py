"""Read TMD's printed daily forecast labels. No LLM/API key required."""
import argparse
import csv
import io
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image, ImageOps
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

ROOT = Path(__file__).resolve().parent
BASE = 'https://ozone.tmd.go.th/PM2.5/weather/Metgram/output/VR_daily/'
FIELDS = ['date', 'province', 'value_m2_s', 'level', 'gfs_init_utc',
          'status', 'retrieved_at', 'source_url', 'note']
HEADERS = ['วันที่ (ค.ศ.)', 'จังหวัด', 'ค่า (m²/s)', 'ระดับตามภาพ',
           'รอบ GFS (UTC)', 'สถานะ', 'ดึงข้อมูลเมื่อ (เวลาไทย)', 'ภาพต้นทาง', 'หมายเหตุ']
MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']

def ocr(image, psm=6, digits=False):
    pixels = np.array(image.convert('RGB'))
    colorful = pixels.astype(int).max(axis=2)-pixels.astype(int).min(axis=2) > 35
    pixels[colorful] = 255  # Remove colored threshold dashes crossing black labels.
    image = Image.fromarray(pixels)
    image = ImageOps.grayscale(image).resize((image.width * 4, image.height * 4))
    image = ImageOps.expand(image, border=20, fill=255)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'input.png'
        image.save(path)
        cmd = ['tesseract', str(path), 'stdout', '--psm', str(psm), '-l', 'eng']
        if digits:
            cmd += ['-c', 'tessedit_char_whitelist=0123456789()']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=25,
                                env=dict(os.environ, OMP_THREAD_LIMIT='1'), check=True)
        return result.stdout.strip()

def read_chart(image, target):
    # Refuse unknown layouts rather than silently reading the wrong date.
    if image.size != (1000, 600):
        raise ValueError(f'layout_changed: {image.size}')
    header = ocr(image.crop((245, 42, 765, 65)), psm=7)
    match = re.search(r'(20\d{2})[-/](\d{2})[-/](\d{2})\s+(\d{2})[Zz]', header)
    if not match:
        raise ValueError('cannot_read_forecast_init: ' + header)
    yyyy, mm, dd, hh = match.groups()
    init = f'{yyyy}-{mm}-{dd} {hh}Z'
    start = date(int(yyyy), int(mm), int(dd))
    offset = (target - start).days
    if not 0 <= offset < 10:
        raise ValueError('target_date_not_in_image: ' + init)
    x = round(161 + offset * 75)
    label = ocr(image.crop((x-32, 539, x+33, 565)), psm=7)
    normalized = re.sub('[^a-z0-9]', '', label.lower())
    expected = f'{target.day}{MONTHS[target.month-1]}'.lower()
    if normalized.lstrip('0') != expected:
        raise ValueError(f'date_label_mismatch: {label} != {expected}')
    rgb = np.array(image.convert('RGB'))
    # The colored bar is wide and continuous; dashed threshold lines are not.
    column = rgb[68:537, x-4:x+5, :].astype(int)
    colorful = ((column.max(axis=2) - column.min(axis=2)) > 45).all(axis=1)
    runs, beginning = [], None
    for i, yes in enumerate(list(colorful) + [False]):
        if yes and beginning is None:
            beginning = i
        elif not yes and beginning is not None:
            if i - beginning >= 3:
                runs.append((beginning+68, i+68))
            beginning = None
    zero_bar = not runs
    if runs and runs[-1][1] < 530:
        raise ValueError('cannot_locate_bar')
    top = 537 if zero_bar else runs[-1][0]
    # Crop the printed number immediately above the bar, not its estimated height.
    roi = image.crop((x-31, top-12 if zero_bar else top-16, x+32, top+1 if zero_bar else top))
    first = ocr(roi, psm=7, digits=True)
    second = ocr(roi, psm=6, digits=True)
    # Include the complete glyphs: tight crops turn (1875) into 14875).
    # Both OCR passes and the plotted geometry must agree.
    height_value = (537-top) * 12000 / (537-68)
    def candidates(text):
        clean = text.strip()
        options = {clean.strip('()').strip()}
        return {int(v) for v in options if re.fullmatch(r'\d{1,5}', v)
                and 0 <= int(v) <= 12000 and abs(height_value-int(v)) <= 250}
    agreed = candidates(first) & candidates(second)
    if len(agreed) != 1:
        raise ValueError(f'number_ocr_uncertain: {first!r}/{second!r}')
    v1 = agreed.pop()
    if zero_bar and v1 != 0:
        raise ValueError('cannot_confirm_zero_bar')
    level = ocr(image.crop((x-32, top-32, x+33, top-12)), psm=7)
    levels = {'poor':'Poor', 'fair':'Fair', 'good':'Good', 'verygood':'Very Good'}
    clean = re.sub('[^a-z]', '', level.lower())
    if clean not in levels:
        raise ValueError('level_ocr_uncertain: ' + level)
    # Independent, deliberately loose check against the plotted bar. Not the data source.
    height_value = (537-top) * 12000 / (537-68)
    if abs(height_value-v1) > 250:
        raise ValueError(f'number_and_bar_disagree: {v1}')
    return v1, levels[clean], init

def fetch(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent':'TMD-daily-collector/1.0',
                                                       'Cache-Control':'no-cache'})
            with urllib.request.urlopen(req, timeout=30) as response:
                data = response.read(5_000_001)
            if len(data) > 5_000_000:
                raise ValueError('image_too_large')
            return data
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt+1))

def collect(province, target, cache=None):
    code = province['station_id']
    url = BASE + f'daily_VR_{code}.png'
    record = dict(zip(FIELDS, [target.isoformat(), province['province'], None, '', '',
                              'รอตรวจสอบ', datetime.now(ZoneInfo('Asia/Bangkok')).isoformat(), url, '']))
    try:
        local = Path(cache) / f'daily_VR_{code}.png' if cache else None
        if local:
            image = Image.open(local)
        else:
            raw = fetch(url)
            # Preserve the exact source bytes, including charts OCR cannot read.
            archive = ROOT/'data'/'images'/target.isoformat()
            archive.mkdir(parents=True, exist_ok=True)
            source = archive/f'daily_VR_{code}.png'
            if not source.exists():
                source.write_bytes(raw)
            image = Image.open(io.BytesIO(raw))
        value, level, init = read_chart(image, target)
        record.update(value_m2_s=value, level=level, gfs_init_utc=init, status='อ่านได้')
    except Exception as exc:
        record['note'] = str(exc)
    return record

def excel(rows, latest_date, path):
    wb = Workbook()
    wb.remove(wb.active)
    latest = [r for r in rows if r['date'] == latest_date]
    for name, data in [('ล่าสุด', latest), ('ย้อนหลัง', rows),
                       ('รอตรวจสอบ', [r for r in rows if r['status'] != 'อ่านได้'])]:
        ws = wb.create_sheet(name)
        ws.append(HEADERS)
        for record in data:
            ws.append([record.get(key) for key in FIELDS])
            ws.cell(ws.max_row, 8).hyperlink = record['source_url']
            ws.cell(ws.max_row, 8).style = 'Hyperlink'
        ws.freeze_panes = 'C2'
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.font = Font(name='Calibri', bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='143B59')
            cell.alignment = Alignment(wrap_text=True)
        widths = [16,25,16,17,24,18,36,35,65]
        for i, width in enumerate(widths, 1):
            ws.column_dimensions[chr(64+i)].width = width
        for line in ws.iter_rows(min_row=2):
            if line[5].value != 'อ่านได้':
                for cell in line:
                    cell.fill = PatternFill('solid', fgColor='FFF1CC')
    info = wb.create_sheet('คำอธิบาย')
    for line in [
        ['ข้อมูล', 'ค่าพยากรณ์อัตราการระบายอากาศ ไม่ใช่ค่าตรวจวัด'],
        ['แหล่งข้อมูล', 'https://ozone.tmd.go.th/wi_daily.html'],
        ['วิธีอ่าน', 'อ่านตัวเลขและระดับที่พิมพ์บนภาพด้วย Tesseract OCR'],
        ['ความแม่นยำ', 'OCR อาจอ่านผิด ควรตรวจภาพต้นทางก่อนใช้รายงานสำคัญ'],
        ['สถานะรอตรวจสอบ', 'ไม่มีค่าตัวเลขเมื่ออ่านไม่ชัด ภาพเปลี่ยนรูปแบบ หรือดาวน์โหลดไม่ได้'],
        ['ย้อนหลัง', 'เริ่มสะสมจากวันที่ระบบทำงาน ไม่มีการเติมค่าจากวันในอนาคต'],
        ['ระดับ', 'ใช้คำระดับที่ระบุในภาพต้นทาง'],
        ['หมายเหตุ', 'การรันซ้ำวันเดียวกันอัปเดตแถวเดิม ไม่เพิ่มแถวซ้ำ'],
    ]: info.append(line)
    info.column_dimensions['A'].width = 24
    info.column_dimensions['B'].width = 110
    wb.save(path)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=datetime.now(ZoneInfo('Asia/Bangkok')).date().isoformat())
    parser.add_argument('--repair-history', action='store_true', help='Retry missing values from archived source images; never substitute a newer forecast')
    parser.add_argument('--cache-dir', help='Local images for offline verification only')
    args = parser.parse_args()
    target = date.fromisoformat(args.date)
    provinces = list(csv.DictReader((ROOT/'provinces.csv').open(encoding='utf-8')))
    if len(provinces) != 77 or len({p['station_id'] for p in provinces}) != 77:
        raise ValueError('province_mapping_not_77_unique_stations')
    with ThreadPoolExecutor(max_workers=4) as pool:
        current = list(pool.map(lambda p: collect(p, target, args.cache_dir), provinces))
    data = ROOT/'data'
    data.mkdir(exist_ok=True)
    history = data/'history.csv'
    previous = list(csv.DictReader(history.open(encoding='utf-8-sig'))) if history.exists() else []
    merged = {(r['date'],r['province']): r for r in previous}
    for row in current:
        key = (row['date'], row['province'])
        old = merged.get(key)
        if old and old['status'] == 'อ่านได้' and row['status'] != 'อ่านได้':
            continue  # A temporary retry failure must not erase a valid earlier observation.
        merged[key] = row
    if args.repair_history:
        mapping = {p['province']: p for p in provinces}
        for key, old in list(merged.items()):
            if old['status'] == 'อ่านได้':
                continue
            folder = data/'images'/old['date']
            image_path = folder/f"daily_VR_{mapping[old['province']]['station_id']}.png"
            if not image_path.exists():
                continue  # Old URLs are mutable; a new forecast is not the original source.
            repaired = collect(mapping[old['province']], date.fromisoformat(old['date']), folder)
            if repaired['status'] == 'อ่านได้' and (not old['gfs_init_utc'] or
                    repaired['gfs_init_utc'] == old['gfs_init_utc']):
                merged[key] = repaired
    rows = sorted(merged.values(), key=lambda r:(r['date'],r['province']))
    for row in rows:
        row['value_m2_s'] = int(row['value_m2_s']) if row['value_m2_s'] not in ('',None) else None
    with history.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    excel(rows, target.isoformat(), data/'ventilation.xlsx')
    ok = sum(r['status']=='อ่านได้' for r in current)
    print(f'{target}: read {ok}/77; review {77-ok}. Excel: data/ventilation.xlsx')
    (data/'last_run.json').write_text(json.dumps({'date':str(target), 'read':ok,
        'review':77-ok, 'forecast_not_observation':True}, ensure_ascii=False, indent=2),encoding='utf-8')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a',encoding='utf-8') as f:
            f.write(f'## {target}\nRead **{ok}/77**; review **{77-ok}**.\n\nDownload `data/ventilation.xlsx` from the repository.\n')
    # Review rows remain visible in Excel; errors never become fabricated numbers.
    if ok == 0:
        print('::warning::No images could be read. See review sheet and notes.')

if __name__ == '__main__':
    main()
