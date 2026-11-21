"""
pdf_pipeline_reference.py  —  참고 구현(reference implementation)

PDF 1개를 받아 다음 두 파일을 만든다.
  <stem>.md           : 레이아웃 분석 기반 본문 + 쪽 구분 표시 + 그림 링크 + 이미지 파일 목록
  <stem>_images.pdf   : 그림·표·(깨진)수식 영역을 렌더링한 이미지 모음 (한 쪽에 1개, 머리글 4줄)

설계 원칙
  - 텍스트: pymupdf4llm 레이아웃 분석 → 2단 읽기 순서, 공백, 아래첨자 문제 해결.
            쪽마다 <!-- page: N --> 삽입, 쪽 머리글·바닥글(다운로드 스탬프 등) 제거.
            수식은 텍스트로 MD에 남기고, 기호가 깨진 수식은 이미지도 함께 만든다.
  - 그림: "PDF 안의 이미지 객체 추출"이 아니라 "캡션 기준 영역 렌더링".
          벡터 그래프, 글자 윤곽선으로 된 표, 래스터+벡터 혼합 그림을 모두 잡는다.
  - 표: 캡션 아래 영역을 렌더링한다(MD의 텍스트 표와 값 대조용).

검증 환경: Python 3.13, pymupdf 2.1, pymupdf4llm 2.1, pymupdf_layout 2.1 (모두 AGPL-3.0)
Python 3.9 이상 문법만 사용. GUI와 독립적이며, GUI에서는 convert_pdf()만 호출하면 된다.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
import pymupdf4llm

# --------------------------------------------------------------------------------------
# 1. 캡션 인식
# --------------------------------------------------------------------------------------
FIG_RE = re.compile(r'^\s*(Fig\.?|FIG\.?|Figure|FIGURE)\s*((?:[A-Z]\.\s?)?\d+)\s*[\.:]')
TAB_RE = re.compile(r'^\s*(TABLE|Table)\s+((?:[A-Z]\.\s?)?\d+|[IVX]+)(?=\s*[\.:]|\s*$|\s+[A-Z][A-Z\s]{3,})')
# 중국어 캡션(NFKC 후): 图/圖/表 + 번호(1, 3-2, 3.2) 바로 뒤에 공백·마침표·쌍점·줄 끝.
# '如图1所示'처럼 번호 뒤에 한자가 붙는 본문 인용과 문장 중간의 인용은 제외된다.
ZH_CAP_RE = re.compile(r'^\s*(图|圖|表)\s*(\d+(?:[.\-]\d+)*)(?=[\s.:]|$)')
CAPTION_CLASSES = ('caption', 'text', 'list-item', 'picture', 'section-header')
SIDE_GAP = 35          # pt, 옆으로 붙은 부분그림/분할된 표를 합칠 때 허용 간격
# 깨진 수식 징후: (cid:N), '='→'¼' 같은 폰트 인코딩 오류, 사용자정의영역 문자, 대체문자(�)
GARBLED_MATH = re.compile(r'\(cid:\d+\)|¼|ð|Þ|[-�]')


def label_of(text: str) -> str | None:
    """캡션 첫머리에서 'Fig 3', 'Fig A.1', 'Table II' 형태의 라벨을 뽑는다. 본문 인용('Fig. 3 shows')은 제외."""
    t = (text or '').strip()
    m = FIG_RE.match(t)
    if m:
        return 'Fig ' + m.group(2).replace(' ', '')
    m = TAB_RE.match(t)
    if m:
        return 'Table ' + m.group(2).replace(' ', '')
    m = ZH_CAP_RE.match(unicodedata.normalize('NFKC', t))   # 영문 규칙이 먼저라 영문 논문 결과는 그대로
    if m:
        return ('Table ' if m.group(1) == '表' else 'Fig ') + m.group(2)
    return None


def _box_text(b: dict) -> str:
    if b.get('textlines'):
        return ' '.join(' '.join(s['text'] for s in l['spans']) for l in b['textlines'])
    return b.get('text') or ''


def _rect(b: dict) -> pymupdf.Rect:
    return pymupdf.Rect(b['x0'], b['y0'], b['x1'], b['y1'])


def _hov(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    return min(a.x1, b.x1) - max(a.x0, b.x0)


def _union(rects) -> pymupdf.Rect:
    rects = list(rects)
    u = pymupdf.Rect(rects[0])
    for r in rects[1:]:
        u |= r
    return u


def _is_zh_caption(text: str) -> bool:
    return bool(ZH_CAP_RE.match(unicodedata.normalize('NFKC', (text or '').strip())))


_HAN_CHAR = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff]')


def _body_sentence(text: str) -> bool:
    """본문 문장으로 보이는가: '。'가 있거나, '，'가 있고 한자가 50자를 넘음."""
    t = text or ''
    return '。' in t or ('，' in t and len(_HAN_CHAR.findall(t)) > 50)


def _merge_bilingual_captions(caps: list[dict]) -> list[dict]:
    """중국어 논문의 '图1 …' + 바로 붙은 'Fig. 1 …'(같은 번호)은 캡션 하나로 합쳐 그림을 한 번만 만든다.
    둘 중 하나가 중국어 캡션일 때만 합치므로 영문 논문에는 영향이 없다."""
    out: list[dict] = []
    for c in caps:
        prev = next((p for p in out if p['label'] == c['label']
                     and (_is_zh_caption(p['text']) or _is_zh_caption(c['text']))
                     and _hov(p['rect'], c['rect']) > 0
                     and -2 <= max(c['rect'].y0 - p['rect'].y1, p['rect'].y0 - c['rect'].y1) <= 15), None)
        if prev is not None:
            first, second = (prev, c) if prev['rect'].y0 <= c['rect'].y0 else (c, prev)
            prev['text'] = first['text'].rstrip() + ' ' + second['text'].strip()
            prev['rect'] = prev['rect'] | c['rect']
            continue
        out.append(c)
    return out


def _page_lines(page) -> list[tuple[pymupdf.Rect, str]]:
    out = []
    for b in page.get_text('dict')['blocks']:
        if b.get('type') != 0:
            continue
        for ln in b['lines']:
            t = ''.join(s['text'] for s in ln['spans']).strip()
            if t:
                out.append((pymupdf.Rect(ln['bbox']), t))
    return out


def _ink_rects(page, region: pymupdf.Rect, box: pymupdf.Rect, cache: dict) -> list[pymupdf.Rect]:
    """region과 겹치는 선(벡터)·이미지 사각형. 상자 전체를 두르는 테두리는 제외. 쪽당 1회만 읽는다."""
    if 'ink' not in cache:
        cache['ink'] = [d['rect'] for d in page.get_drawings()]
        cache['ink'] += [r for x in page.get_images(full=True) for r in page.get_image_rects(x[0])]
    out = []
    for r in cache['ink']:
        # 가로선·세로선은 높이·폭이 0인 사각형이라 intersects()가 False → 좌표로 직접 비교
        hits = r.x0 <= region.x1 and r.x1 >= region.x0 and r.y0 <= region.y1 and r.y1 >= region.y0
        if hits and not (r.y0 <= box.y0 + 2 and r.y1 >= box.y1 - 2):
            out.append(r)
    return out


def _ink_below(page, box: pymupdf.Rect, y: float, cache: dict | None = None) -> bool:
    """box 안, y 아래쪽에 선(벡터)이나 이미지가 있는가. 그림 전체를 두르는 테두리는 제외."""
    below = pymupdf.Rect(box.x0, y + 1, box.x1, box.y1)
    if below.is_empty:
        return False
    return bool(_ink_rects(page, below, box, {} if cache is None else cache))


def _caption_block(inside: list, i: int, stop_y: float) -> list:
    """i번째 캡션 줄부터 이어지는 캡션 줄들(같은 단, 줄 간격 ≤ 줄 높이, stop_y 위)."""
    lr = inside[i][0]
    block = [inside[i]]
    for r, t in inside[i + 1:]:
        cx = (r.x0 + r.x1) / 2
        if r.y1 > stop_y + 1 or not (lr.x0 - 50 <= cx <= lr.x1 + 50):
            continue
        prev = block[-1][0]
        if r.y0 - prev.y1 > max(prev.height, 6):
            break
        block.append((r, t))
    return block


def _split_merged_captions(page, caps: list[dict], pool: list[dict]) -> None:
    """레이아웃 엔진이 캡션 줄을 그림·표 상자에 합친 경우 분리한다(그 라벨의 캡션이 따로 없을 때만).
    - 그림 상자, 캡션 줄 아래에 선·이미지 없음: 그 줄을 경계로 그림 | 캡션
    - 그림 상자, 캡션 줄 위·아래 모두 선·이미지 있음(그림 두 개가 한 상자): 위쪽은 이 캡션의 그림,
      캡션 아래쪽은 새 그림 상자로 떼어 다음 캡션이 가져가게 함 (떼어 낸 상자도 다시 검사)
    - 표 상자: 위쪽 표와 다른 표의 캡션 줄이 나오면 그 줄을 경계로 두 표로 나눔"""
    known = {c['label'] for c in caps}
    lines = None
    cache: dict = {}
    k = 0
    while k < len(pool):
        v = pool[k]
        k += 1
        if v.get('formula') or v['cls'] not in ('picture', 'table'):
            continue
        box = v['rect']
        if lines is None:
            lines = _page_lines(page)
        inside = [(lr, t) for lr, t in lines
                  if box.contains(pymupdf.Point((lr.x0 + lr.x1) / 2, (lr.y0 + lr.y1) / 2))]
        inside.sort(key=lambda lt: lt[0].y0)
        for i, (lr, t) in enumerate(inside):
            lab = label_of(t)
            if not lab or lab in known or lr.y0 - box.y0 < 10:   # 상자 맨 위 줄은 그 상자 자신의 캡션일 수 있음
                continue
            if v['cls'] == 'picture' and lab.startswith('Fig'):
                below = [r for r in _ink_rects(page, pymupdf.Rect(box.x0, lr.y1 + 1, box.x1, box.y1), box, cache)
                         if r.y0 >= lr.y1 - 1]
                if not below:
                    cap_lines = inside[i:]
                    v['rect'] = pymupdf.Rect(box.x0, box.y0, box.x1, lr.y0 - 0.5)
                else:
                    above = _ink_rects(page, pymupdf.Rect(box.x0, box.y0, box.x1, lr.y0 - 1), box, cache)
                    if not above:
                        continue
                    cap_lines = _caption_block(inside, i, min(r.y0 for r in below))
                    cap_bottom = max(r.y1 for r, _ in cap_lines)
                    lower = pymupdf.Rect(box.x0, cap_bottom + 0.5, box.x1, box.y1)
                    if not _ink_rects(page, lower, box, cache):
                        continue
                    v['rect'] = pymupdf.Rect(box.x0, box.y0, box.x1, lr.y0 - 0.5)
                    pool.append(dict(rect=lower, cls='picture', used=False))
            elif v['cls'] == 'table' and lab.startswith('Table'):
                # 같은 높이로 쪼개진 줄('表6' '系统并网' '试验结果')과 바로 아래 영문 'Tab. 6 …' 줄을 함께 캡션으로
                same_row = lambda r0, r1: min(r0.y1, r1.y1) - max(r0.y0, r1.y0) > 0.5 * min(r0.height, r1.height)
                cap_lines = sorted([(r2, t2) for r2, t2 in inside if same_row(r2, lr)], key=lambda x: x[0].x0)
                rest = [x for x in inside if x not in cap_lines and x[0].y0 > lr.y0]
                en = next((x for x in rest if re.match(r'^\s*(Tab\.?|Table|TABLE)\s*\d', x[1])
                           and 0 < x[0].y0 - lr.y0 <= 1.8 * lr.height), None)
                if en is not None:
                    cap_lines += sorted([x for x in rest if same_row(x[0], en[0])], key=lambda x: x[0].x0)
                cap_bottom = max(r.y1 for r, _ in cap_lines)
                v['rect'] = pymupdf.Rect(box.x0, box.y0, box.x1, lr.y0 - 0.5)
                v['mixed_with'] = lab
                pool.append(dict(rect=pymupdf.Rect(box.x0, cap_bottom + 0.5, box.x1, box.y1), cls='table', used=False))
            else:
                continue
            caps.append(dict(label=lab, rect=_union(r for r, _ in cap_lines),
                             text=' '.join(t2 for _, t2 in cap_lines), split=True))
            known.add(lab)
            break


# --------------------------------------------------------------------------------------
# 2. 영역 찾기
# --------------------------------------------------------------------------------------
def find_regions(doc: pymupdf.Document, layout: dict, formula_images: str = 'garbled') -> list[dict]:
    """
    반환: dict(page, label, rect, how, caption, kind)
      kind: 'figure' | 'table' | 'formula' | 'orphan'
      how : 'layout'(레이아웃 상자로 확정) | 'band'(캡션 옆 띠 영역, 사람이 확인 필요) | 'none'(영역 못 찾음)
    formula_images: 'garbled'(깨진 수식만) | 'all' | 'none'
    """
    out: list[dict] = []
    for pg in layout['pages']:
        page = doc[pg['page_number'] - 1]
        W, H = pg['width'], pg['height']
        caps, pool, texts, formulas = [], [], [], []
        for b in pg['boxes']:
            cls, t, r = b['boxclass'], _box_text(b), _rect(b)
            lab = label_of(t) if cls in CAPTION_CLASSES and len(t) < 1500 else None
            if lab and cls != 'caption' and _body_sentence(t):   # '图 10 对比了…。' 같은 본문 문장
                lab = None
            if lab and not (cls == 'picture' and r.height > 30):
                caps.append(dict(label=lab, rect=r, text=t))
            elif cls in ('picture', 'table'):
                pool.append(dict(rect=r, cls=cls, used=False))
            elif cls == 'formula':
                # 레이아웃 엔진이 작은 도식을 formula로 분류하기도 함 → 그림 캡션 바로 위일 때만 그림으로 사용
                pool.append(dict(rect=r, cls=cls, used=False, formula=True))
                texts.append(r)
                formulas.append(dict(rect=r, text=t))
            elif cls in ('text', 'list-item', 'section-header', 'title'):
                texts.append(r)

        caps = _merge_bilingual_captions(caps)
        _split_merged_captions(page, caps, pool)

        # 표 캡션은 'Table 2' 줄 + 바로 아래 제목 줄로 나뉘는 경우가 많다(Elsevier/IEEE) → 합친다
        for c in caps:
            if c['label'].startswith('Table'):
                for t in list(texts):
                    if 0 <= t.y0 - c['rect'].y1 <= 10 and _hov(t, c['rect']) > 0 and t.height < 100:
                        c['rect'] = c['rect'] | t
                        texts.remove(t)

        def blocked(y_a: float, y_b: float, xr: pymupdf.Rect) -> bool:
            """두 y 사이에 본문 문단(한 줄 초과)이나 다른 캡션이 있으면 같은 그림으로 보지 않는다."""
            lo, hi = min(y_a, y_b), max(y_a, y_b)
            for t in texts:
                if t.height > 22 and t.y0 >= lo - 1 and t.y1 <= hi + 1 and _hov(t, xr) > 0.3 * min(t.width, xr.width):
                    return True
            for c2 in caps:
                r2 = c2['rect']
                if r2.y0 >= lo + 1 and r2.y1 <= hi - 1 and _hov(r2, xr) > 0.3 * min(r2.width, xr.width):
                    return True
            return False

        # (1) 시각 요소 → 가장 가까운 캡션 (그림: 캡션이 아래, 표: 캡션이 위)
        #     표 상자는 조건에 맞는 표 캡션이 있으면 거리와 상관없이 표 캡션에 먼저 배정하고,
        #     그림 캡션은 표 캡션이 없는 표 상자만 가져갈 수 있다.
        def nearest(v, want):
            vr, best, bestd = v['rect'], None, 1e9
            for i, c in enumerate(caps):
                if not want(c):
                    continue
                cr = c['rect']
                if _hov(cr, vr) < 0.3 * min(cr.width, vr.width):
                    continue
                if c['label'].startswith('Table'):
                    if v.get('formula') or vr.y0 < cr.y0 or blocked(cr.y1, vr.y0, vr):
                        continue
                    d = vr.y0 - cr.y1
                else:
                    if vr.y1 > cr.y1 + 2:
                        continue
                    d = cr.y0 - vr.y1
                    if (v.get('formula') and d > 20) or blocked(vr.y1, cr.y0, vr):
                        continue
                if -15 <= d < bestd:
                    best, bestd = i, d
            return best

        assigned = {i: [] for i in range(len(caps))}
        for v in pool:
            best = None
            if v['cls'] == 'table':
                best = nearest(v, lambda c: c['label'].startswith('Table'))
            if best is None:
                best = nearest(v, lambda c: True)
            if best is not None:
                assigned[best].append(v)
                v['used'] = True

        # (2) 옆으로 붙은 나머지(분할된 표 열, 좌우 부분그림) 흡수
        for i in assigned:
            changed = bool(assigned[i])
            while changed:
                changed = False
                u = _union(v['rect'] for v in assigned[i])
                for v in pool:
                    if v['used'] or v.get('formula'):
                        continue
                    r = v['rect']
                    vov = min(r.y1, u.y1) - max(r.y0, u.y0)
                    hgap = max(r.x0 - u.x1, u.x0 - r.x1)
                    if vov > 0.5 * min(r.height, u.height) and hgap <= SIDE_GAP:
                        v['used'] = True
                        assigned[i].append(v)
                        changed = True

        # (3) 캡션별 결과. 영역을 못 찾은 캡션은 '띠 영역'으로 대체
        for i, c in enumerate(caps):
            kind = 'figure' if c['label'].startswith('Fig') else 'table'
            got = assigned[i]
            cr = c['rect']
            full = cr.width > 0.55 * W
            x0, x1 = (36, W - 36) if full else ((36, W / 2) if (cr.x0 + cr.x1) / 2 < W / 2 else (W / 2, W - 36))
            col = pymupdf.Rect(x0, 0, x1, 1)
            if got:
                r = _union(v['rect'] for v in got)
                if kind == 'table' and r.width < 0.6 * (x1 - x0):      # 표가 한 열만 잡힌 경우 단 폭으로 확장
                    r = pymupdf.Rect(min(r.x0, x0), r.y0, max(r.x1, x1), r.y1)
                out.append(dict(page=pg['page_number'], label=c['label'], rect=r, how='layout',
                                caption=c['text'], kind=kind, caption_split=c.get('split', False),
                                mixed_with=next((v['mixed_with'] for v in got if v.get('mixed_with')), None)))
                continue
            if kind == 'figure':
                lim = max([t.y1 for t in texts if t.height > 22 and t.y1 < cr.y0 - 2 and _hov(t, col) > 20] + [40])
                band = pymupdf.Rect(x0, lim, x1, cr.y0)
            else:
                lim = min([t.y0 for t in texts if t.height > 22 and t.y0 > cr.y1 + 2 and _hov(t, col) > 20] + [H - 40])
                band = pymupdf.Rect(x0, cr.y1, x1, lim)
            ink = [d['rect'] for d in page.get_drawings() if d['rect'].intersects(band)]
            ink += [r for x in page.get_images(full=True) for r in page.get_image_rects(x[0]) if r.intersects(band)]
            if band.height > 25 and len(ink) > 3:
                out.append(dict(page=pg['page_number'], label=c['label'], rect=_union(ink) & band, how='band',
                                caption=c['text'], kind=kind))
            else:
                out.append(dict(page=pg['page_number'], label=c['label'], rect=None, how='none',
                                caption=c['text'], kind=kind))

        # (4) 수식 이미지 (기본값: 기호가 깨진 수식만)
        if formula_images != 'none':
            for f in formulas:
                if any(v['used'] and v['rect'] == f['rect'] for v in pool):
                    continue
                if formula_images == 'all' or GARBLED_MATH.search(f['text'] or ''):
                    m = re.search(r'\((\d{1,3})\)\s*$', (f['text'] or '').strip()) or re.search(r'\((\d{1,3})\)', f['text'] or '')
                    out.append(dict(page=pg['page_number'], label=f"Eq ({m.group(1)})" if m else 'Eq',
                                    rect=f['rect'], how='layout', caption=(f['text'] or '')[:160], kind='formula'))

        # (5) 캡션 없는 시각 요소 (저자 사진, 로고, 아이콘 조각 등)
        for v in pool:
            if not v['used'] and not v.get('formula'):
                out.append(dict(page=pg['page_number'], label=None, rect=v['rect'], how='orphan',
                                caption='', kind='orphan'))
    return out


# --------------------------------------------------------------------------------------
# 3. 파일명
# --------------------------------------------------------------------------------------
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f()\[\]{}]+')


def safe_stem(source: Path, max_len: int = 90) -> str:
    """Windows 금지 문자·괄호를 '_'로 바꾸고, 접미사를 붙이기 '전에' 본문 이름만 자른다."""
    s = unicodedata.normalize('NFC', source.stem)
    s = _BAD.sub('_', s)
    s = re.sub(r'\s+', '_', s)
    s = re.sub(r'_+', '_', s).strip('._ ')
    return s[:max_len].rstrip('._ ') or 'document'


def output_names(pdf_path: Path, out_dir: Path, sha: str, stem_max: int = 90) -> tuple[str, Path, Path]:
    """(stem, <stem>.md, <stem>_images.pdf). 같은 이름으로 '다른' 원본이 이미 변환되어 있으면
    해시 6자리로 구분하고, 같은 원본이면 덮어쓴다. OCR 경로(worker)도 같은 규칙을 쓴다."""
    stem = safe_stem(Path(pdf_path), stem_max)
    md_path = Path(out_dir) / f'{stem}.md'
    if md_path.exists() and sha not in md_path.read_text(encoding='utf-8', errors='ignore')[:3000]:
        stem = f'{stem[:stem_max - 7]}_{sha[:6]}'
    return stem, Path(out_dir) / f'{stem}.md', Path(out_dir) / f'{stem}_images.pdf'


# --------------------------------------------------------------------------------------
# 4. 텍스트(레이아웃 분석 1회 + MD 정리)
# --------------------------------------------------------------------------------------
PICTURE_TEXT = re.compile(r'<!-- Start of picture text -->.*?<!-- End of picture text -->\n?', re.S)


def _parse_once(doc: pymupdf.Document):
    """레이아웃 분석은 큰 문서에서 수 분이 걸릴 수 있으므로 1회만 수행해 영역 찾기와 MD 생성에 같이 쓴다.
    반환: (layout_dict, page_chunks, strip_picture_text_safe)
    - 내부 API(parse_document)를 쓰면 수식 상자를 본문 텍스트로 바꿔 MD에 남길 수 있다.
    - 버전 차이로 내부 API가 실패하면 공개 API 2회 호출로 대체한다. 이 경우 수식 텍스트가
      그림 텍스트 블록에 섞여 나오므로 그림 텍스트 블록을 지우지 않는다(내용 손실 방지)."""
    try:
        from pymupdf4llm.helpers.document_layout import parse_document
        parsed = parse_document(doc, use_ocr=False, write_images=False, embed_images=False, force_text=True)
        layout = json.loads(parsed.to_json())
        for pg in parsed.pages:
            for b in pg.boxes:
                if b.boxclass == 'formula':
                    b.boxclass = 'text'
        chunks = parsed.to_markdown(header=False, footer=False, write_images=False, page_chunks=True)
        return layout, chunks, True
    except Exception:
        lj = pymupdf4llm.to_json(doc, use_ocr=False)
        layout = json.loads(lj) if isinstance(lj, str) else lj
        chunks = pymupdf4llm.to_markdown(doc, use_ocr=False, header=False, footer=False,
                                         write_images=False, page_chunks=True)
        return layout, chunks, False


def tidy_md(md: str) -> str:
    """기울임 표시만 남은 구두점 정리: '6 _._ 76' → '6.76', '_·_' → '·'."""
    md = re.sub(r'(\d)\s*_\._\s*(\d)', r'\1.\2', md)
    md = re.sub(r'_([^\w\s_])_', r'\1', md)
    md = re.sub(r'\n{4,}', '\n\n\n', md)
    return md


# 중국어 공백 정리. 반각 한글(U+FFA0–FFDC)은 전각 영역(U+FF00–FFEF) 안에 있지만 한글이므로 뺀다.
_HAN = '\u3400-\u4dbf\u4e00-\u9fff'
_CJK = _HAN + '\u3040-\u30ff\u3000-\u303f\uff00-\uff9f\uffdd-\uffef'
_CJK_SPACE = re.compile(rf'(?<=[{_CJK}])[ \t]+(?=[{_CJK}])')
_DIGIT_HAN = re.compile(rf'(?<=[0-9])[ \t]+(?=[{_HAN}])|(?<=[{_HAN}])[ \t]+(?=[0-9])')
_BEFORE_CLOSE = re.compile(r'(?<=[^\s\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f])[ \t]+(?=[。，、；：！？）」』】])')


def cjk_spacing(md: str) -> str:
    """한자·가나·CJK 구두점·전각 문자 사이, 숫자와 한자 사이, CJK 닫는 구두점 앞의 공백을 지운다
    (줄바꿈은 그대로). 한글이 붙은 공백은 건드리지 않는다."""
    md = _CJK_SPACE.sub('', md)
    md = _DIGIT_HAN.sub('', md)
    return _BEFORE_CLOSE.sub('', md)


def _caption_anchor(md: str, label: str) -> int | None:
    """쪽 MD 안에서 캡션 줄의 시작 위치를 찾는다(그림 링크를 캡션 바로 앞에 넣기 위함)."""
    kind, num = label.split(' ', 1)
    num_re = re.escape(num).replace(r'\.', r'\.\s?')
    if kind == 'Fig':
        pat = rf'^[ \t>*_#]*(?:Fig\.?|Figure|FIGURE)\s*{num_re}\s*[\.:]'
    elif kind == 'Table':
        pat = rf'^[ \t>*_#]*(?:TABLE|Table)\s+{num_re}\b'
    else:
        return None
    m = re.search(pat, md, re.M)
    if m is None:
        # 중국어 캡션: 전각 숫자·구두점도 허용 (MD 원문은 NFKC 전 상태)
        digits = ''.join(f'[{ch}{chr(ord(ch) + 0xFEE0)}]' if ch.isdigit() else f'[{re.escape(ch)}．－]'
                         for ch in num)
        word = '表' if kind == 'Table' else '[图圖]'
        # pymupdf4llm은 번호만 굵게 쓰기도 한다: '图 **1** 大规模…'
        m = re.search(rf'^[ \t>*_#]*{word}\s*\**\s*{digits}\**(?=[\s\u3000.:：．]|$)', md, re.M)
    return m.start() if m else None


def _squeeze(s: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', s or ''))


def _restore_split_captions(md: str, items) -> str:
    """그림 상자에서 분리한 캡션은 '그림 안 글자' 블록과 함께 지워지므로, 그 블록 자리에 캡션 문장을 넣는다."""
    for it in items:
        key = _squeeze(it.caption)[:10]
        if not key or key in _squeeze(PICTURE_TEXT.sub('', md)):
            continue
        for m in PICTURE_TEXT.finditer(md):
            if key in _squeeze(m.group(0)):
                md = md[:m.start()] + f'\n{it.caption}\n\n' + md[m.end():]
                break
        else:
            md = md.rstrip() + f'\n\n{it.caption}\n'
    return md


def _mark_mixed_table(md: str, it) -> str:
    """표 상자에 다른 표가 섞였을 때, MD 텍스트 표에도 그 표의 캡션 행이 있으면 표 앞에 알림을 넣는다."""
    pos = _caption_anchor(md, it.label)
    if pos is None:
        return md
    m = re.search(r'^\|', md[pos:], re.M)
    if m is None:
        return md
    start = pos + m.start()
    end = md.find('\n\n', start)
    table = md[start:end if end >= 0 else len(md)]
    for row in table.splitlines():
        cells = ' '.join(c.strip() for c in row.strip().strip('|').split('|') if c.strip())
        if label_of(cells) == it.mixed_with:
            num = it.mixed_with.split(' ', 1)[1]
            name = f'表{num}' if cells.lstrip().startswith('表') else it.mixed_with
            return md[:start] + f'※ 표 텍스트에 {name} 내용이 섞여 있음, 이미지 모음 PDF 참조\n\n' + md[start:]
    return md


# --------------------------------------------------------------------------------------
# 5. 변환 본체
# --------------------------------------------------------------------------------------
HEADER_FONT = 'kr'   # pymupdf.Font('korea')(내장 CJK, 영문 비례 폭)를 PDF에 넣어 쓴다


def _header_line(page, font: pymupdf.Font, pos, text: str, size: float, width: float) -> None:
    """한 줄 머리글. fontname='korea'(비내장)는 영문이 전각 폭이라 파일명이 잘렸다 → 내장 폰트 + 폭 맞춤."""
    while font.text_length(text, size) > width and size > 6:
        size -= 0.5
    while len(text) > 1 and font.text_length(text, size) > width:
        text = text[:-2] + '…'
    page.insert_text(pos, text, fontname=HEADER_FONT, fontsize=size)


def _header_box(page, font: pymupdf.Font, rect: pymupdf.Rect, text: str) -> None:
    """여러 줄 머리글. insert_textbox는 넘치면 아무것도 쓰지 않으므로 글자 크기를 줄이고, 그래도 넘치면 말줄임."""
    kw = dict(fontname=HEADER_FONT)
    for size in (8, 7, 6.5, 6):
        if page.insert_textbox(rect, text, fontsize=size, **kw) >= 0:
            return
    while len(text) > 10:
        text = text[:int(len(text) * 0.85)].rstrip() + '…'
        if page.insert_textbox(rect, text, fontsize=6, **kw) >= 0:
            return
@dataclass
class FigureItem:
    index: int
    label: str | None
    kind: str
    page: int
    how: str
    caption: str
    file_name: str
    jpg: bytes = field(repr=False)
    px: tuple = (0, 0)
    caption_split: bool = False     # 캡션 줄을 그림·표 상자에서 분리했음
    mixed_with: str | None = None   # 이 표 상자에 다른 표(라벨)가 섞여 있었음


def convert_pdf(pdf_path, out_dir, *, dpi: int = 150, jpg_quality: int = 80, pad: float = 3.0,
                stem_max: int = 90, include_orphans: bool = False, orphan_min_area: float = 0.04,
                formula_images: str = 'garbled', save_image_files: bool = False, progress=None,
                stem: str | None = None, output_name: str | None = None) -> dict:
    """
    PDF 1개 변환. 반환값: dict(md, images_pdf, items, report)
    save_image_files: True면 MD 링크와 같은 경로(<out_dir>/images/<파일명>.jpg)에 개별 이미지도 저장
    progress: 선택. progress(stage: str, done: int, total: int) 콜백 (GUI 진행 표시용)
    stem: 호출 측(pdf_naming)이 이름·중복·경로 길이를 이미 정했으면 그 이름을 그대로 쓴다
    output_name: 사용자가 바꾼 출력 이름. 주어지면 앞머리에 output_name 항목으로 기록
    """
    pdf_path, out_dir = Path(pdf_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = pdf_path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if stem:
        md_path, img_pdf_path = out_dir / f'{stem}.md', out_dir / f'{stem}_images.pdf'
    else:
        stem, md_path, img_pdf_path = output_names(pdf_path, out_dir, sha, stem_max)

    doc = pymupdf.open(stream=raw, filetype='pdf')
    total = doc.page_count
    if progress:
        progress('layout', 0, total)
    layout, chunks, strip_ok = _parse_once(doc)
    if progress:
        progress('regions', 0, total)

    # --- 영역 → 이미지 ---------------------------------------------------------------------
    regions = find_regions(doc, layout, formula_images=formula_images)
    items: list[FigureItem] = []
    skipped_orphans = 0
    short = stem[:40].rstrip('._ ')
    for r in regions:
        if r['rect'] is None:
            continue
        page = doc[r['page'] - 1]
        if r['kind'] == 'orphan':
            if not include_orphans or r['rect'].get_area() < orphan_min_area * page.rect.get_area():
                skipped_orphans += 1
                continue
        clip = (r['rect'] + (-pad, -pad, pad, pad)) & page.rect
        pix = page.get_pixmap(clip=clip, dpi=dpi)
        # 번호 사이 구분자는 남긴다: 'Fig 3-2' → Fig3-2 (Fig32와 구분), 'Fig A.1' → FigA.1
        tag = re.sub(r'[^0-9A-Za-z.\-]+', '', r['label'] or 'Image')
        idx = len(items) + 1
        items.append(FigureItem(index=idx, label=r['label'], kind=r['kind'], page=r['page'], how=r['how'],
                                caption=(r['caption'] or '').strip(),
                                caption_split=r.get('caption_split', False), mixed_with=r.get('mixed_with'),
                                file_name=f'{short}_p{r["page"]:03d}_{idx:02d}_{tag}.jpg',
                                jpg=pix.tobytes('jpg', jpg_quality=jpg_quality), px=(pix.width, pix.height)))
        if progress:
            progress('render', r['page'], total)

    # --- MD 조립 ---------------------------------------------------------------------------
    by_page: dict = {}
    for it in items:
        by_page.setdefault(it.page, []).append(it)
    front = {
        'source_file': pdf_path.name, 'source_sha256': sha,
        **({'output_name': output_name} if output_name else {}),
        'pages': total,
        'images_pdf': img_pdf_path.name if items else '', 'images': len(items),
        'converter': f'pdf_pipeline_reference / pymupdf {pymupdf.VersionBind} / '
                     f'pymupdf4llm {getattr(pymupdf4llm, "__version__", "?")}',
        'converted_at': _dt.datetime.now().isoformat(timespec='seconds'),
    }
    parts = ['---\n' + '\n'.join(f'{k}: {json.dumps(v, ensure_ascii=False)}' for k, v in front.items()) + '\n---\n']
    for n, ch in enumerate(chunks, start=1):
        md = ch['text'] if isinstance(ch, dict) else str(ch)
        page_items = by_page.get(n, [])
        if strip_ok:
            md = _restore_split_captions(md, [it for it in page_items if it.caption_split and it.kind == 'figure'])
            md = PICTURE_TEXT.sub('', md)
        md = tidy_md(md)
        for it in page_items:
            if it.mixed_with:
                md = _mark_mixed_table(md, it)
        for it in page_items:
            link = f'![{it.label or "Image"}](images/{it.file_name})\n\n'
            pos = _caption_anchor(md, it.label) if it.label and it.kind in ('figure', 'table') else None
            md = (md[:pos] + link + md[pos:]) if pos is not None else (md.rstrip() + '\n\n' + link)
        # 공백 정리는 마지막에: 캡션 위치 찾기는 '图 1 …'(번호 뒤 공백)를 기준으로 하기 때문
        md = cjk_spacing(md)
        parts.append(f'\n<!-- page: {n} -->\n\n{md.strip()}\n')
    if not items:   # 그림 없는 문서(스캔 PDF 포함): 빈 PDF는 저장할 수 없으므로 이미지 모음 PDF를 만들지 않는다
        parts.append('\n---\n\n## 이미지 파일 목록\n\n이 문서에서 추출한 그림·표·수식 이미지가 없습니다.\n')
    else:
        parts.append('\n---\n\n## 이미지 파일 목록\n\n'
                     f"이 문서의 이미지는 이미지 모음 PDF('{img_pdf_path.name}')에 한 쪽에 1개씩 들어 있습니다. "
                     '각 쪽 상단에 본문 이미지 링크와 같은 파일명이 표시되어 있습니다.\n\n'
                     '| 파일명 | 이미지 모음 PDF | 원문 위치 | 라벨 | 영역 판정 | 설명 |\n|---|---|---|---|---|---|\n')
    for it in items:
        cap = re.sub(r'\s+', ' ', it.caption).replace('|', '/')[:200]
        parts.append(f'| {it.file_name} | {img_pdf_path.name} {it.index}쪽 | {it.page}쪽 | '
                     f'{it.label or "-"} | {it.how} | {cap} |\n')
    md_path.write_text(''.join(parts), encoding='utf-8', newline='\n')

    # --- 이미지 모음 PDF (쪽당 1개, 머리글: 파일명 / 원문 위치 / 라벨·판정 / 설명) ------------------
    out = pymupdf.open()
    A4 = pymupdf.paper_rect('a4')
    font = pymupdf.Font('korea')
    text_w = A4.width - 72
    for it in items:
        p = out.new_page(width=A4.width, height=A4.height)
        p.insert_font(fontname=HEADER_FONT, fontbuffer=font.buffer)
        _header_line(p, font, (36, 42), f'[{it.index}/{len(items)}] {it.file_name}', 10, text_w)
        _header_line(p, font, (36, 58), f'원문 위치: {it.page}쪽', 9, text_w)
        _header_line(p, font, (36, 72), f'라벨: {it.label or "-"} (영역 판정: {it.how})', 9, text_w)
        cap_text = re.sub(r'\s+', ' ', it.caption)[:400]
        _header_box(p, font, pymupdf.Rect(36, 78, A4.width - 36, 118), '설명: ' + cap_text)
        p.draw_line((36, 122), (A4.width - 36, 122), width=0.5)
        avail = pymupdf.Rect(36, 130, A4.width - 36, A4.height - 36)
        w, h = it.px
        s = min(avail.width / w, avail.height / h, 72 / dpi * 1.6)   # 작은 그림의 지나친 확대 방지
        p.insert_image(pymupdf.Rect(avail.x0, avail.y0, avail.x0 + w * s, avail.y0 + h * s), stream=it.jpg)
    if save_image_files:
        (out_dir / 'images').mkdir(exist_ok=True)
        for it in items:
            (out_dir / 'images' / it.file_name).write_bytes(it.jpg)
    out.set_metadata({'title': f'{stem}_images - 이미지 모음', 'subject': pdf_path.name, 'keywords': sha})
    if items:
        out.subset_fonts()
        out.save(img_pdf_path, garbage=3, deflate=True)
    else:
        img_pdf_path.unlink(missing_ok=True)   # 이전 변환에서 남은 모음 PDF가 새 MD와 어긋나지 않게

    report = dict(source=pdf_path.name, pages=total,
                  figures=sum(1 for i in items if i.kind == 'figure'),
                  tables=sum(1 for i in items if i.kind == 'table'),
                  formulas=sum(1 for i in items if i.kind == 'formula'),
                  band_regions=[i.label for i in items if i.how == 'band'],
                  captions_without_region=[r['label'] for r in regions if r['rect'] is None and r['label']],
                  skipped_orphans=skipped_orphans, text_api='internal' if strip_ok else 'public-fallback',
                  md_bytes=md_path.stat().st_size,
                  images_pdf_bytes=img_pdf_path.stat().st_size if items else 0,
                  source_bytes=len(raw))
    return dict(md=md_path, images_pdf=img_pdf_path if items else None, items=items, report=report)


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        print('사용법: python pdf_pipeline_reference.py <출력폴더> <원본1.pdf> [원본2.pdf ...]')
        sys.exit(2)
    for f in sys.argv[2:]:
        r = convert_pdf(f, sys.argv[1])
        print(json.dumps(r['report'], ensure_ascii=False))
