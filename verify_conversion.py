"""
verify_conversion.py — 원본 PDF ↔ (MD + 이미지 모음 PDF) 정합성 검증

사용법
  # 1쌍
  python verify_conversion.py --orig 원본.pdf --md 변환.md --images 변환_images.pdf
  # 폴더 일괄 (원본 폴더와 출력 폴더가 같아도 됨)
  python verify_conversion.py --orig-dir ./원본 --out-dir ./출력 [--json report.json]
  종료 코드: 모두 합격 0, 하나라도 불합격 1

측정 기준이 되는 원문 '본문 블록' = 2줄 이상이고 줄 평균 40자 이상인 텍스트 블록 (한자가 절반 넘는 줄은 20자 기준)
(그림 안 눈금·범례 글자, 머리글·바닥글은 제외됨)

합격 조건 (하나라도 어기면 FAIL)
  coverage  ≥ 97%  원문 본문을 12자 단위로 잘라 MD에 있는 비율 (공백·하이픈·마크다운 기호 무시, 순서 무관)
  order     ≥ 80%  원문에서 이어지는 두 줄이 MD에서도 이어지는 비율 (2단 섞임 검출)
  glued     ≤ 1%   22자 이상 붙어 버린 영단어 비율 (공백 소실 검출)
  pages     = 쪽수  MD의 <!-- page: N --> 표시 수
  formulas         깨진 수식 문자가 있으면 이미지 모음 PDF에 수식 이미지(라벨 'Eq …')가 1개 이상 있어야 함
  figures/tables   원문 캡션 라벨이 이미지 모음 PDF에 모두 있어야 함
  labels           라벨 중복 없음, 라벨의 원문 쪽이 캡션 쪽과 ±1쪽 이내
경고 (판정에는 영향 없음, 표시만)
  garbled 개수, 3번 이상 반복되는 30자 이상 줄(머리글·스탬프), 캡션 없는 이미지, 작은/빈 이미지, 'band' 판정 영역
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import statistics
import sys
import unicodedata
from pathlib import Path

import pymupdf

FIG_RE = re.compile(r'^\s*(Fig\.?|FIG\.?|Figure|FIGURE)\s*((?:[A-Z]\.\s?)?\d+)\s*[\.:]')
TAB_RE = re.compile(r'^\s*(TABLE|Table)\s+((?:[A-Z]\.\s?)?\d+|[IVX]+)(?=\s*[\.:]|\s*$|\s+[A-Z][A-Z\s]{3,})')
# 중국어 캡션(NFKC 후): 图/圖/表 + 번호(1, 3-2, 3.2) 바로 뒤에 공백·마침표·쌍점·줄 끝 ('如图1所示' 제외)
ZH_CAP_RE = re.compile(r'^\s*(图|圖|表)\s*(\d+(?:[.\-]\d+)*)(?=[\s.:]|$)')
HAN = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]')
BODY_LINE_AVG = 40      # 본문 블록: 줄 평균 글자 수 기준 (영문 줄)
BODY_LINE_AVG_HAN = 20  # 한자가 절반 넘는 줄의 기준
GARBLED = re.compile(r'\(cid:\d+\)|¼|[-�]')

THRESHOLDS = dict(coverage=0.97, order=0.80, glued_max=0.01)


def label_of(text: str) -> str | None:
    t = (text or '').strip()
    m = FIG_RE.match(t)
    if m:
        return 'Fig ' + m.group(2).replace(' ', '')
    m = TAB_RE.match(t)
    if m:
        return 'Table ' + m.group(2).replace(' ', '')
    m = ZH_CAP_RE.match(unicodedata.normalize('NFKC', t))
    if m:
        return ('Table ' if m.group(1) == '表' else 'Fig ') + m.group(2)
    return None


def _line_threshold(line: str) -> int:
    chars = re.sub(r'\s+', '', line)
    return BODY_LINE_AVG_HAN if chars and len(HAN.findall(chars)) * 2 > len(chars) else BODY_LINE_AVG


def is_body_block(lines: list[str]) -> bool:
    """2줄 이상, 줄 평균 글자 수가 줄별 기준(영문 40, 한자가 절반 넘는 줄 20)의 평균 이상."""
    if len(lines) < 2:
        return False
    return sum(len(x) for x in lines) / len(lines) >= sum(_line_threshold(x) for x in lines) / len(lines)


def clean(s: str) -> str:
    s = re.sub(r'</?(?:sup|sub|br|b|i|em|strong|span)\s*/?>', '', s)   # pymupdf4llm이 넣는 위·아래첨자, 줄바꿈 태그
    s = unicodedata.normalize('NFKC', s)
    return re.sub(r'[\s\-‐‑–—\u00ad*_#|`>~]+', '', s).lower()


def _natkey(s: str):
    return [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', s)]


# ---------------------------------------------------------------- 원문
def read_original(path: Path) -> dict:
    doc = pymupdf.open(path)
    blocks, captions = [], {}
    for p in doc:
        for b in p.get_text('dict')['blocks']:
            if b.get('type') != 0:
                continue
            lines = [''.join(s['text'] for s in l['spans']).strip() for l in b['lines']]
            lines = [x for x in lines if x]
            if not lines:
                continue
            lab = label_of(lines[0]) or label_of(' '.join(lines[:2]))
            if not lab:   # 캡션 앞에 그림 안의 짧은 글자(축 라벨 등)가 같은 블록으로 붙은 경우
                for j in range(1, min(6, len(lines))):
                    if all(len(x) < 25 for x in lines[:j]) and label_of(lines[j]):
                        lab = label_of(lines[j])
                        break
            if lab and lab not in captions:
                captions[lab] = p.number + 1
            if is_body_block(lines):
                blocks.append(lines)
    return dict(pages=doc.page_count, blocks=blocks, captions=captions, bytes=path.stat().st_size)


# ---------------------------------------------------------------- MD
def split_md(md: str):
    body = re.sub(r'\A---\n.*?\n---\n', '', md, flags=re.S)                      # 앞머리(front matter)
    body = re.split(r'\n##\s*이미지 파일 목록', body)[0]                           # 매핑표
    body = re.split(r'이 문서의 이미지는 이미지 모음 PDF', body)[0]
    plain = re.sub(r'^!\[.*?\]\(.*?\)\s*$', '', body, flags=re.M)
    plain = re.sub(r'<!--.*?-->', '', plain, flags=re.S)
    return body, plain


def text_metrics(orig: dict, md: str) -> dict:
    _, plain = split_md(md)
    nb = clean(plain)
    allt = clean(''.join(''.join(b) for b in orig['blocks']))
    sh = [allt[i:i + 12] for i in range(0, max(0, len(allt) - 12), 12)]
    coverage = sum(1 for s in sh if s in nb) / max(1, len(sh))
    pairs = [(clean(b[j]), clean(b[j + 1])) for b in orig['blocks'] for j in range(len(b) - 1)
             if len(clean(b[j])) >= 25 and len(clean(b[j + 1])) >= 25]
    order = sum(1 for a, c in pairs if a + c in nb) / max(1, len(pairs))
    words = [w for w in re.findall(r'[A-Za-z][A-Za-z\-–—,\.]*', plain) if 'http' not in w and 'doi' not in w.lower()]
    glued = sum(1 for w in words if len(re.sub(r'[^A-Za-z]', '', w)) >= 22) / max(1, len(words))
    lines = [l.strip() for l in plain.split('\n') if len(l.strip()) >= 30]
    rep = {l for l in set(lines) if lines.count(l) >= 3}
    return dict(coverage=coverage, order=order, glued=glued,
                page_markers=len(re.findall(r'<!--\s*page:\s*\d+\s*-->', md)),
                garbled=len(GARBLED.findall(plain)), repeated_lines=len(rep),
                repeated_example=(sorted(rep)[0][:80] if rep else ''))


# ---------------------------------------------------------------- 이미지 PDF
def read_images_pdf(path: Path) -> list[dict]:
    items = []
    doc = pymupdf.open(path)
    for p in doc:
        t = p.get_text()
        page_m = re.search(r'원문 위치:\s*(\d+)\s*쪽', t)
        lab_m = re.search(r'라벨:\s*([^\n(]+)', t)
        how_m = re.search(r'영역 판정:\s*(\w+)', t)
        cap_m = re.search(r'설명:\s*(.*)', t, re.S)
        label = None
        if lab_m and lab_m.group(1).strip() not in ('-', ''):
            label = lab_m.group(1).strip()
        elif cap_m:
            label = label_of(cap_m.group(1))
        imgs = p.get_images(full=True)
        px = (imgs[0][2], imgs[0][3]) if imgs else (0, 0)
        blank = None
        if imgs:
            try:
                pix = pymupdf.Pixmap(doc, imgs[0][0])
                if pix.n - pix.alpha >= 3:
                    sample = pix.samples[::max(1, len(pix.samples) // 20000)]
                    blank = statistics.pstdev(sample) < 4
            except Exception:
                pass
        items.append(dict(page=int(page_m.group(1)) if page_m else None, label=label, px=px, blank=blank,
                          how=how_m.group(1) if how_m else None))
    return items


def figure_metrics(orig: dict, items: list[dict]) -> dict:
    exp = orig['captions']
    got = [i['label'] for i in items if i['label']]
    figs_e = {k for k in exp if k.startswith('Fig')}
    tabs_e = {k for k in exp if k.startswith('Table')}
    capt = [l for l in got if l.startswith(('Fig', 'Table'))]
    return dict(
        fig_expected=len(figs_e), fig_found=len(figs_e & set(got)),
        fig_missing=sorted(figs_e - set(got), key=_natkey),
        table_expected=len(tabs_e), table_found=len(tabs_e & set(got)),
        table_missing=sorted(tabs_e - set(got), key=_natkey),
        duplicate_labels=sorted({l for l in capt if capt.count(l) > 1}, key=_natkey),
        label_page_mismatch=sorted({i['label'] for i in items if i['label'] in exp and i['page']
                                    and abs(i['page'] - exp[i['label']]) > 1}, key=_natkey),
        formula_items=sum(1 for l in got if l.startswith('Eq')),
        band_items=[i['label'] for i in items if i['how'] == 'band'],
        uncaptioned_items=sum(1 for i in items if not i['label']),
        tiny_images=sum(1 for i in items if i['px'][0] < 120 and i['px'][1] < 120),
        blank_images=sum(1 for i in items if i['blank']), items=len(items))


# ---------------------------------------------------------------- 평가
def evaluate(orig_path: Path, md_path: Path, img_path: Path | None) -> dict:
    orig = read_original(orig_path)
    md = md_path.read_text(encoding='utf-8', errors='replace')
    r = dict(original=orig_path.name, md=md_path.name, images_pdf=img_path.name if img_path else None,
             pages=orig['pages'])
    r.update(text_metrics(orig, md))
    items = read_images_pdf(img_path) if img_path and img_path.exists() else []
    r.update(figure_metrics(orig, items))
    out_bytes = md_path.stat().st_size + (img_path.stat().st_size if img_path and img_path.exists() else 0)
    r['source_bytes'], r['output_bytes'] = orig['bytes'], out_bytes
    r['size_ratio'] = out_bytes / orig['bytes']
    checks = {
        'coverage': r['coverage'] >= THRESHOLDS['coverage'],
        'order': r['order'] >= THRESHOLDS['order'],
        'glued': r['glued'] <= THRESHOLDS['glued_max'],
        'pages': r['page_markers'] >= orig['pages'],
        'formulas': r['garbled'] == 0 or r['formula_items'] > 0,
        'figures': not r['fig_missing'],
        'tables': not r['table_missing'],
        'labels': not r['duplicate_labels'] and not r['label_page_mismatch'],
    }
    r['checks'] = checks
    r['pass'] = all(checks.values())
    return r


def find_mds(out_dir: Path, recursive: bool = False) -> list[Path]:
    """recursive: 하위 폴더(GUI 문서별 폴더)까지 찾되 '_old_format' 폴더 아래는 제외."""
    if not recursive:
        return sorted(Path(out_dir).glob('*.md'))
    return sorted(p for p in Path(out_dir).rglob('*.md')
                  if '_old_format' not in p.relative_to(out_dir).parts)


def pair_outputs(orig_dir: Path, out_dir: Path, recursive: bool = False):
    """MD마다 (원본 PDF, MD, 이미지 PDF)를 찾는다. 원본과 출력이 같은 폴더에 있어도 된다.
    새 형식: MD 앞머리의 source_file / images_pdf 사용.
    이전 형식: 이미지 PDF를 이름 규칙으로 먼저 찾고, 나머지 PDF 중 이름 앞부분이 가장 길게 일치하는 것을 원본으로 본다.
    recursive: 이미지 PDF는 out_dir이 아니라 각 MD와 같은 폴더에서 찾는다."""
    all_pdfs = [Path(p) for p in glob.glob(str(orig_dir / '*.pdf'))]
    norm = lambda s: re.sub(r'[^0-9A-Za-z가-힣]+', '_', s).strip('_').lower()
    for md_path in find_mds(out_dir, recursive):
        img_dir = md_path.parent if recursive else Path(out_dir)
        md = md_path.read_text(encoding='utf-8', errors='replace')
        img = None
        m = re.search(r'^images_pdf:\s*"?(.*?)"?\s*$', md, re.M) or re.search(r"이미지 모음 PDF\('([^']+)'\)", md)
        names = []
        if m:
            names += [m.group(1), re.sub(r'[()]', '_', m.group(1))]
        names += [md_path.stem + s + '.pdf' for s in ('_images', '_image', '_ima', '')]
        for n in names:
            c = img_dir / n
            if c.exists():
                img = c
                break
        orig = None
        src = re.search(r'^source_file:\s*"?(.*?)"?\s*$', md, re.M)
        if src and (orig_dir / src.group(1)).exists():
            orig = orig_dir / src.group(1)
        if orig is None:
            key = norm(md_path.stem)
            cands = [p for p in all_pdfs if (img is None or p.resolve() != img.resolve())
                     and (norm(p.stem).startswith(key) or key.startswith(norm(p.stem)))
                     and not re.search(r'_(images?|ima)$', p.stem)]
            orig = max(cands, key=lambda p: len(p.stem)) if cands else None
        if orig is None:
            print(f'[건너뜀] 원본을 찾지 못함: {md_path.name}')
            continue
        yield orig, md_path, img


def print_table(rows):
    h = (f"{'원본':34s} {'cover':>6} {'order':>6} {'glued':>6} {'page':>7} {'garb':>5} {'Eq':>3} {'rep':>4} "
         f"{'그림':>7} {'표':>5} {'size':>6}  판정")
    print(h)
    print('-' * len(h))
    for r in rows:
        verdict = 'PASS' if r['pass'] else 'FAIL: ' + ','.join(k for k, v in r['checks'].items() if not v)
        print(f"{r['original'][:34]:34s} {r['coverage']*100:5.1f}% {r['order']*100:5.1f}% {r['glued']*100:5.2f}% "
              f"{r['page_markers']:>3}/{r['pages']:<3} {r['garbled']:>5} {r['formula_items']:>3} {r['repeated_lines']:>4} "
              f"{r['fig_found']:>3}/{r['fig_expected']:<3} {r['table_found']:>2}/{r['table_expected']:<2} "
              f"{r['size_ratio']*100:5.1f}%  {verdict}")
        d = []
        if r['fig_missing']: d.append('그림 누락 ' + ', '.join(r['fig_missing']))
        if r['table_missing']: d.append('표 누락 ' + ', '.join(r['table_missing']))
        if r['duplicate_labels']: d.append('라벨 중복 ' + ', '.join(r['duplicate_labels']))
        if r['label_page_mismatch']: d.append('라벨-쪽 불일치 ' + ', '.join(r['label_page_mismatch']))
        if r['band_items']: d.append('확인 필요(band 영역) ' + ', '.join(x or '?' for x in r['band_items']))
        if r['tiny_images'] or r['blank_images']: d.append(f"작은 이미지 {r['tiny_images']} / 빈 이미지 {r['blank_images']}")
        if r['uncaptioned_items']: d.append(f"캡션 없는 이미지 {r['uncaptioned_items']}")
        if r['repeated_example']: d.append(f"반복 줄 예: {r['repeated_example']}")
        for x in d:
            print(f"    └ {x}")
    n = len(rows)
    if n:
        so, sb = sum(r['output_bytes'] for r in rows), sum(r['source_bytes'] for r in rows)
        print(f"\n합계 {n}편: 합격 {sum(r['pass'] for r in rows)}편 | 그림 {sum(r['fig_found'] for r in rows)}/"
              f"{sum(r['fig_expected'] for r in rows)} | 표 {sum(r['table_found'] for r in rows)}/"
              f"{sum(r['table_expected'] for r in rows)} | 용량 {sb/1e6:.1f} MB → {so/1e6:.1f} MB ({so/sb*100:.1f}%)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--orig'); ap.add_argument('--md'); ap.add_argument('--images')
    ap.add_argument('--orig-dir'); ap.add_argument('--out-dir'); ap.add_argument('--json')
    ap.add_argument('--recursive', action='store_true',
                    help="out-dir 하위 폴더까지 *.md 검색('_old_format' 제외), 이미지 PDF는 각 MD 폴더에서 찾음")
    a = ap.parse_args(argv)
    rows = []
    if a.orig and a.md:
        rows.append(evaluate(Path(a.orig), Path(a.md), Path(a.images) if a.images else None))
    elif a.orig_dir and a.out_dir:
        for o, m, i in pair_outputs(Path(a.orig_dir), Path(a.out_dir), a.recursive):
            rows.append(evaluate(o, m, i))
    else:
        ap.error('--orig/--md 또는 --orig-dir/--out-dir 를 지정하세요')
    print_table(rows)
    if a.json:
        Path(a.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    return 0 if rows and all(r['pass'] for r in rows) else 1


if __name__ == '__main__':
    sys.exit(main())
