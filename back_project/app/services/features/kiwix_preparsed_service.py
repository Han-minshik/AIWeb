from __future__ import annotations

import asyncio
import base64
import io
import html
import json
import mimetypes
import re
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Dict

from fastapi import HTTPException

from app.config import settings
from app.repositories.transaction import db_transaction
from app.services.common.minio_key_builder import KbStorageScope, artifact_location
from app.services.common.minio_storage_service import get_minio_storage_service
from app.services.features import minio_upload_service


def normalize_asset_path(value: str) -> str:
    raw = str(value or '').replace(chr(92), '/').strip().strip('<>')
    while raw.startswith('./'):
        raw = raw[2:]
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {'', '.', '..'} for part in path.parts):
        raise HTTPException(status_code=422, detail='INVALID_KIWIX_ASSET_PATH')
    parts = list(path.parts)
    if parts and parts[0].lower() == 'assets':
        parts = parts[1:]
    if not parts:
        raise HTTPException(status_code=422, detail='INVALID_KIWIX_ASSET_PATH')
    return PurePosixPath(*parts).as_posix()


_MARKDOWN_IMAGE = re.compile(r'(!\[[^\]\n]*\]\(\s*)(<[^>\n]+>|[^)\n]+?)(\s*\))')
_HTML_IMAGE_SOURCE = re.compile(
    r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])([^"\']+)\2',
    re.IGNORECASE,
)


def _normalized_asset_reference(value: str, assets: Dict[str, bytes]) -> str | None:
    source = html.unescape(str(value or '').strip().strip('<>'))
    if not source or source.startswith('#') or source.lower().startswith(
        ('http://', 'https://', 'data:', 'blob:')
    ):
        return None
    decoded = urllib.parse.unquote(source.split('#', 1)[0].split('?', 1)[0])
    asset_path = normalize_asset_path(decoded)
    if asset_path not in assets:
        raise RuntimeError(f'KIWIX_ASSET_NOT_FOUND:{asset_path}')
    encoded = '/'.join(urllib.parse.quote(part, safe='') for part in asset_path.split('/'))
    return f'assets/{encoded}'


def normalize_markdown_asset_references(
    markdown_bytes: bytes,
    assets: Dict[str, bytes] | None = None,
) -> bytes:
    try:
        text = markdown_bytes.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise RuntimeError('KIWIX_MARKDOWN_ENCODING_ERROR') from exc
    safe_assets = {normalize_asset_path(key): value for key, value in (assets or {}).items()}

    def replace_markdown(match: re.Match[str]) -> str:
        target = match.group(2).strip()
        wrapped = target.startswith('<') and target.endswith('>')
        raw_target = target[1:-1] if wrapped else target
        title = ''
        title_match = re.match(r'^(.*?)(\s+(?:"[^"]*"|\'[^\']*\'))$', raw_target)
        if title_match:
            raw_target, title = title_match.group(1), title_match.group(2)
        normalized = _normalized_asset_reference(raw_target, safe_assets)
        if normalized is None:
            return match.group(0)
        output_target = f'<{normalized}>' if wrapped else normalized
        return f'{match.group(1)}{output_target}{title}{match.group(3)}'

    def replace_html(match: re.Match[str]) -> str:
        normalized = _normalized_asset_reference(match.group(3), safe_assets)
        if normalized is None:
            return match.group(0)
        return f'{match.group(1)}{match.group(2)}{normalized}{match.group(2)}'

    text = _MARKDOWN_IMAGE.sub(replace_markdown, text)
    text = _HTML_IMAGE_SOURCE.sub(replace_html, text)
    return text.encode('utf-8')


_PAGE_MARKER = re.compile(r'^\s*<!--\s*PAGE\s*:\s*\d+\s*-->\s*$', re.IGNORECASE)
_IMAGE_LINE = re.compile(r'^\s*!\[([^]]*)\]\(([^)]+)\)\s*$')


def _pdf_font_name() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        settings.KIWIX_PDF_FONT_PATH,
        r'C:\Windows\Fonts\malgun.ttf',
        r'C:\Windows\Fonts\arial.ttf',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            try:
                pdfmetrics.registerFont(TTFont('KiwixDocumentFont', candidate))
                return 'KiwixDocumentFont'
            except Exception:
                continue
    return 'Helvetica'


def _convert_markdown_to_pdf_legacy(markdown_bytes: bytes, assets: Dict[str, bytes] | None = None) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise RuntimeError('KIWIX_PDF_CONVERTER_NOT_INSTALLED') from exc
    try:
        text = markdown_bytes.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise RuntimeError('KIWIX_MARKDOWN_ENCODING_ERROR') from exc
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    font_name = _pdf_font_name()
    font_size = 10
    line_height = 14
    left = 40
    right = A4[0] - 40
    y = A4[1] - 40
    pdf.setFont(font_name, font_size)

    def new_page() -> None:
        nonlocal y
        pdf.showPage()
        pdf.setFont(font_name, font_size)
        y = A4[1] - 40

    def draw_text(value: str) -> None:
        nonlocal y
        value = re.sub(r'^\s{0,3}(#{1,6}|[-*+] |\d+[.] )\s*', '', value).replace('`', '')
        if not value:
            y -= line_height
            return
        line = ''
        for char in value:
            candidate = line + char
            if line and pdfmetrics.stringWidth(candidate, font_name, font_size) > right - left:
                if y < 40:
                    new_page()
                pdf.drawString(left, y, line)
                y -= line_height
                line = char
            else:
                line = candidate
        if y < 40:
            new_page()
        pdf.drawString(left, y, line)
        y -= line_height

    safe_assets = {normalize_asset_path(key): value for key, value in (assets or {}).items()}
    for line in text.splitlines():
        if _PAGE_MARKER.match(line):
            new_page()
            continue
        image_match = _IMAGE_LINE.match(line)
        if image_match:
            target = image_match.group(2).strip().strip('<>').split(' "', 1)[0]
            if target.lower().startswith(('http://', 'https://', 'data:')):
                draw_text(image_match.group(1) or target)
                continue
            asset_path = normalize_asset_path(target)
            payload = safe_assets.get(asset_path)
            if payload is None:
                raise RuntimeError(f'KIWIX_ASSET_NOT_FOUND:{asset_path}')
            try:
                image = ImageReader(io.BytesIO(payload))
                width, height = image.getSize()
                scale = min((right - left) / width, 360 / height, 1.0)
                draw_width, draw_height = width * scale, height * scale
                if y - draw_height < 40:
                    new_page()
                pdf.drawImage(image, left, y - draw_height, draw_width, draw_height, preserveAspectRatio=True)
                y -= draw_height + line_height
            except Exception as exc:
                raise RuntimeError(f'KIWIX_ASSET_RENDER_FAILED:{asset_path}') from exc
            continue
        draw_text(line)
    pdf.save()
    return output.getvalue()


def convert_markdown_to_html(markdown_bytes: bytes) -> str:
    try:
        import markdown
    except ImportError as exc:
        raise RuntimeError('KIWIX_MARKDOWN_CONVERTER_NOT_INSTALLED') from exc
    try:
        text = markdown_bytes.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise RuntimeError('KIWIX_MARKDOWN_ENCODING_ERROR') from exc
    text = _PAGE_MARKER.sub('\n<div data-page-break="true"></div>\n', text)
    fragment = markdown.markdown(
        text,
        extensions=['extra', 'sane_lists', 'nl2br'],
        output_format='xhtml',
    )
    return (
        '<html><head><meta charset="utf-8" />'
        '<style>'
        'body{font-family:sans-serif;font-size:10pt;line-height:1.45;}'
        'h1,h2,h3,h4,h5,h6{page-break-after:avoid;}'
        'table{border-collapse:collapse;width:100%;}'
        'th,td{border:1px solid #777;padding:4px;}'
        'pre,code{font-family:monospace;}'
        'img{max-width:100%;height:auto;}'
        '</style></head><body>' + fragment + '</body></html>'
    )


def embed_html_assets(html_document: str, assets: Dict[str, bytes] | None = None) -> str:
    safe_assets = {normalize_asset_path(key): value for key, value in (assets or {}).items()}
    image_source = re.compile(
        r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])([^"\']+)\2',
        re.IGNORECASE,
    )

    def replace(match: re.Match[str]) -> str:
        source = html.unescape(match.group(3)).strip()
        if source.lower().startswith(('http://', 'https://', 'data:')):
            return match.group(0)
        decoded = urllib.parse.unquote(source.split('#', 1)[0].split('?', 1)[0])
        path = normalize_asset_path(decoded)
        payload = safe_assets.get(path)
        if payload is None:
            raise RuntimeError(f'KIWIX_ASSET_NOT_FOUND:{path}')
        mime_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        encoded = base64.b64encode(payload).decode('ascii')
        data_uri = f'data:{mime_type};base64,{encoded}'
        return f'{match.group(1)}{match.group(2)}{data_uri}{match.group(2)}'

    return image_source.sub(replace, html_document)


def _inline_html(element: ET.Element) -> str:
    parts = [html.escape(element.text or '')]
    allowed = {'strong': 'b', 'b': 'b', 'em': 'i', 'i': 'i', 'u': 'u', 'sup': 'super', 'sub': 'sub'}
    for child in element:
        tag = child.tag.lower()
        if tag == 'code':
            inner = f'<font name="Courier">{_inline_html(child)}</font>'
        elif tag == 'a':
            href = html.escape(str(child.attrib.get('href') or ''), quote=True)
            inner = f'<a href="{href}" color="blue">{_inline_html(child)}</a>'
        elif tag == 'br':
            inner = '<br/>'
        elif tag == 'img':
            inner = html.escape(str(child.attrib.get('alt') or ''))
        elif tag in allowed:
            mapped = allowed[tag]
            inner = f'<{mapped}>{_inline_html(child)}</{mapped}>'
        else:
            inner = _inline_html(child)
        parts.append(inner)
        parts.append(html.escape(child.tail or ''))
    return ''.join(parts)


def _asset_image(src: str, alt: str, assets: Dict[str, bytes]):
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet

    raw = urllib.parse.unquote(str(src or '').split('#', 1)[0].split('?', 1)[0])
    if raw.lower().startswith(('http://', 'https://', 'data:')):
        return Paragraph(html.escape(alt or raw), getSampleStyleSheet()['BodyText'])
    path = normalize_asset_path(raw)
    payload = assets.get(path)
    if payload is None:
        raise RuntimeError(f'KIWIX_ASSET_NOT_FOUND:{path}')
    try:
        reader = ImageReader(io.BytesIO(payload))
        width, height = reader.getSize()
        scale = min(515 / max(width, 1), 650 / max(height, 1), 1.0)
        return Image(io.BytesIO(payload), width=width * scale, height=height * scale)
    except Exception as exc:
        raise RuntimeError(f'KIWIX_ASSET_RENDER_FAILED:{path}') from exc


def convert_html_to_pdf(html_document: str, assets: Dict[str, bytes] | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, PageTemplate, PageBreak, Paragraph, Preformatted,
        Spacer, Table, TableStyle,
    )

    safe_assets = {normalize_asset_path(key): value for key, value in (assets or {}).items()}
    html_document = html_document.replace('&nbsp;', '&#160;')
    html_document = re.sub(r'<(img|br|hr)(\b[^>]*?)(?<!/)>', r'<\1\2 />', html_document, flags=re.IGNORECASE)
    try:
        root = ET.fromstring(html_document)
    except ET.ParseError as exc:
        raise RuntimeError('KIWIX_HTML_PARSE_FAILED') from exc
    body = root.find('body')
    if body is None:
        raise RuntimeError('KIWIX_HTML_BODY_NOT_FOUND')

    styles = getSampleStyleSheet()
    font_name = _pdf_font_name()
    body_style = ParagraphStyle('KiwixBody', parent=styles['BodyText'], fontName=font_name, fontSize=10, leading=15, spaceAfter=6)
    quote_style = ParagraphStyle('KiwixQuote', parent=body_style, leftIndent=12, textColor=colors.HexColor('#444444'))
    code_style = ParagraphStyle('KiwixCode', parent=styles['Code'], fontName='Courier', fontSize=8, leading=11, backColor=colors.HexColor('#f2f2f2'), borderPadding=6)
    heading_styles = {
        f'h{level}': ParagraphStyle(
            f'KiwixH{level}', parent=styles[f'Heading{min(level, 4)}'],
            fontName=font_name, fontSize=max(11, 22 - level * 2), leading=max(14, 26 - level * 2),
            spaceBefore=10, spaceAfter=6,
        )
        for level in range(1, 7)
    }
    story = []

    def append_paragraph(element: ET.Element, style=body_style, prefix='') -> None:
        images = [child for child in element if child.tag.lower() == 'img']
        if not images:
            markup = prefix + _inline_html(element).strip()
            if markup:
                story.append(Paragraph(markup, style))
            return
        leading = html.escape(element.text or '')
        if leading.strip():
            story.append(Paragraph(prefix + leading, style))
            prefix = ''
        for child in element:
            if child.tag.lower() == 'img':
                story.append(_asset_image(child.attrib.get('src', ''), child.attrib.get('alt', ''), safe_assets))
                story.append(Spacer(1, 4))
                if (child.tail or '').strip():
                    story.append(Paragraph(html.escape(child.tail or ''), style))

    def append_block(element: ET.Element, list_prefix='') -> None:
        tag = element.tag.lower()
        if tag == 'div' and element.attrib.get('data-page-break') == 'true':
            if story and not isinstance(story[-1], PageBreak):
                story.append(PageBreak())
            return
        if tag in heading_styles:
            append_paragraph(element, heading_styles[tag])
            return
        if tag == 'p':
            append_paragraph(element)
            return
        if tag == 'blockquote':
            for child in element:
                if child.tag.lower() == 'p':
                    append_paragraph(child, quote_style)
                else:
                    append_block(child)
            return
        if tag == 'pre':
            story.append(Preformatted(''.join(element.itertext()), code_style))
            story.append(Spacer(1, 5))
            return
        if tag in {'ul', 'ol'}:
            ordered = tag == 'ol'
            for index, item in enumerate([child for child in element if child.tag.lower() == 'li'], 1):
                prefix = f'{index}. ' if ordered else '• '
                append_paragraph(item, body_style, prefix)
            return
        if tag == 'table':
            rows = []
            for row in element.iter('tr'):
                cells = []
                for cell in list(row):
                    if cell.tag.lower() in {'th', 'td'}:
                        cells.append(Paragraph(_inline_html(cell) or ' ', body_style))
                if cells:
                    rows.append(cells)
            if rows:
                columns = max(len(row) for row in rows)
                for row in rows:
                    row.extend([''] * (columns - len(row)))
                table = Table(rows, repeatRows=1, hAlign='LEFT')
                table.setStyle(TableStyle([
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#777777')),
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eeeeee')),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 4),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ]))
                story.extend([table, Spacer(1, 7)])
            return
        if tag == 'hr':
            story.append(Spacer(1, 8))
            return
        if tag == 'img':
            story.append(_asset_image(element.attrib.get('src', ''), element.attrib.get('alt', ''), safe_assets))
            story.append(Spacer(1, 4))
            return
        for child in element:
            append_block(child, list_prefix)

    for child in body:
        append_block(child)
    if not story:
        story.append(Paragraph(' ', body_style))

    output = io.BytesIO()
    document = BaseDocTemplate(
        output, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title='KIWIX Document',
    )
    document.addPageTemplates([
        PageTemplate(
            id='main',
            frames=[Frame(document.leftMargin, document.bottomMargin, document.width, document.height, id='main-frame')],
        )
    ])
    document.build(story)
    return output.getvalue()


def convert_markdown_to_pdf(markdown_bytes: bytes, assets: Dict[str, bytes] | None = None) -> bytes:
    return convert_html_to_pdf(convert_markdown_to_html(markdown_bytes), assets)


def build_manifest(
    *, content_key: str, content_version: str, viewer: Dict[str, Any],
    assets: list[Dict[str, Any]], document_metadata: Dict[str, Any] | None = None,
    rendered_html: Dict[str, str] | None = None,
) -> bytes:
    return json.dumps({
        'schema_version': '1',
        'ingestion_mode': 'preparsed',
        'source_format': 'markdown',
        'document_metadata': {
            'schema': (document_metadata or {}).get('schema'),
            'stage': (document_metadata or {}).get('stage'),
        },
        'source_metadata': {},
        'content': {'object_key': content_key, 'version_id': content_version},
        'rendered_html': rendered_html or {},
        'viewer': viewer,
        'assets': assets,
    }, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def complete_preparsed_jobs(
    *, object_id: int, revision_id: int, user_id: int,
    artifact_bucket: str, artifact_key: str, artifact_version: str,
    artifact_sha256: str, artifact_size: int,
) -> Dict[str, int]:
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''SELECT ai.ai_document_id,dr.rev_no
                     FROM ai_document_info ai
                     JOIN doc_revisions dr ON dr.doc_id=ai.object_id
                    WHERE ai.object_id=%s AND dr.revision_id=%s FOR UPDATE''',
                (object_id, revision_id),
            )
            row = cursor.fetchone()
            if not row:
                raise RuntimeError('KIWIX_DOCUMENT_REVISION_NOT_FOUND')
            action = 'update' if int(row.get('rev_no') or 1) > 1 else 'add'
            cursor.execute(
                '''INSERT INTO ai_index_jobs(
                    ai_document_id,object_id,revision_id,action,stage,status,retry_count,
                    external_task_id,started_at,finished_at,scheduled_at,created_by,created_at,updated_at
                ) VALUES(%s,%s,%s,%s,'parse','completed',0,'internal:preparsed_markdown',
                         NOW(),NOW(),NOW(),%s,NOW(),NOW())
                    RETURNING job_id''',
                (row['ai_document_id'], object_id, revision_id, action, user_id),
            )
            parse_job_id = int(cursor.fetchone()["job_id"])
            cursor.execute(
                '''INSERT INTO ai_parse_artifacts(
                    revision_id,producer_job_id,artifact_type,bucket_name,object_key,
                    minio_version_id,artifact_status,checksum_sha256,size_bytes,
                    parser_version,verified_at,created_at,updated_at
                ) VALUES(%s,%s,'manifest',%s,%s,%s,'READY',%s,%s,
                         'preparsed_markdown',NOW(),NOW(),NOW())
                    RETURNING artifact_id''',
                (revision_id, parse_job_id, artifact_bucket, artifact_key,
                 artifact_version, artifact_sha256, artifact_size),
            )
            artifact_id = int(cursor.fetchone()["artifact_id"])
            cursor.execute(
                '''INSERT INTO ai_index_jobs(
                    ai_document_id,object_id,revision_id,artifact_id,action,stage,status,
                    retry_count,scheduled_at,created_by,created_at,updated_at
                ) VALUES(%s,%s,%s,%s,%s,'index','pending',0,NOW(),%s,NOW(),NOW())
                RETURNING job_id''',
                (row['ai_document_id'], object_id, revision_id, artifact_id, action, user_id),
            )
            index_job_id = int(cursor.fetchone()["job_id"])
    return {'parse_job_id': parse_job_id, 'artifact_id': artifact_id, 'index_job_id': index_job_id}


def _upload_bytes(storage, bucket: str, key: str, payload: bytes, content_type: str, metadata: Dict[str, str]):
    return storage.upload_stream(
        bucket, key, io.BytesIO(payload), length=len(payload),
        content_type=content_type, metadata=metadata,
    )


def _validate_target(parent_node_id: int, user_id: int) -> None:
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''SELECT tn.is_folder,tn.kb_type,u.is_admin
                     FROM tree_nodes tn
                     JOIN users u ON u.user_id=%s
                    WHERE tn.node_id=%s''',
                (int(user_id), int(parent_node_id)),
            )
            row = cursor.fetchone()
            if not row or not bool(row.get('is_folder')):
                raise HTTPException(status_code=400, detail='INVALID_PARENT_FOLDER')
            if str(row.get('kb_type') or '').lower() in {'personal', 'person'}:
                raise HTTPException(status_code=409, detail='KIWIX_SE_KB_REQUIRED')
            if not bool(row.get('is_admin')):
                raise HTTPException(status_code=403, detail='SE_KB_ADMIN_REQUIRED')


def _existing_preparsed_result(object_id: int, revision_id: int) -> Dict[str, Any] | None:
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''SELECT p.job_id AS parse_job_id,a.artifact_id,i.job_id AS index_job_id,
                          i.status AS index_status
                     FROM ai_index_jobs p
                     JOIN ai_parse_artifacts a ON a.producer_job_id=p.job_id
                     LEFT JOIN ai_index_jobs i ON i.artifact_id=a.artifact_id AND i.stage='index'
                    WHERE p.object_id=%s AND p.revision_id=%s AND p.stage='parse'
                      AND p.status='completed' AND a.artifact_type='manifest'
                    ORDER BY p.job_id DESC LIMIT 1''',
                (int(object_id), int(revision_id)),
            )
            row = cursor.fetchone()
    return dict(row) if row else None


def _remove_failed_registration(created: Dict[str, Any]) -> None:
    object_id = int(created['object_id'])
    revision_id = int(created['revision_id'])
    storage = get_minio_storage_service()
    try:
        storage.permanent_delete_object(created['bucket_name'], created['object_key'])
    except Exception:
        pass
    try:
        prefix = artifact_location(
            KbStorageScope.SE_KB, object_id, revision_id, 'manifest.json'
        ).object_key.rsplit('/', 1)[0] + '/'
        storage.permanent_delete_prefix(settings.MINIO_SE_KB_ARTIFACT_BUCKET, prefix)
    except Exception:
        pass
    with db_transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute('UPDATE ai_index_jobs SET artifact_id=NULL WHERE object_id=%s', (object_id,))
            cursor.execute('DELETE FROM ai_parse_artifacts WHERE revision_id=%s', (revision_id,))
            cursor.execute('DELETE FROM ai_index_jobs WHERE object_id=%s', (object_id,))
            cursor.execute('DELETE FROM acl_bindings WHERE node_id IN (SELECT node_id FROM tree_nodes WHERE doc_id=%s)', (object_id,))
            cursor.execute('DELETE FROM tree_nodes WHERE doc_id=%s', (object_id,))
            cursor.execute('DELETE FROM ai_document_info WHERE object_id=%s', (object_id,))
            cursor.execute('DELETE FROM doc_revisions WHERE doc_id=%s', (object_id,))
            cursor.execute('DELETE FROM storage_objects WHERE object_id=%s', (object_id,))


async def ingest_markdown(
    *, upload_request_id: str, parent_node_id: int, source_file_name: str,
    markdown_bytes: bytes, assets: Dict[str, bytes], user_id: int,
    kiwix_metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if not minio_upload_service.is_kiwix_preparsed_markdown('kiwix', source_file_name):
        raise HTTPException(status_code=422, detail='KIWIX_MARKDOWN_REQUIRED')
    safe_assets = {normalize_asset_path(path): payload for path, payload in assets.items()}
    metadata = minio_upload_service.normalize_kiwix_metadata(kiwix_metadata)
    await asyncio.to_thread(_validate_target, parent_node_id, user_id)
    try:
        normalized_markdown = await asyncio.to_thread(
            normalize_markdown_asset_references, markdown_bytes, safe_assets
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail={
            'code': 'KIWIX_MARKDOWN_PREPARATION_FAILED',
            'file_name': source_file_name,
            'reason': str(exc)[:300],
            'retryable': True,
        }) from exc

    display_name = f'{PurePosixPath(source_file_name).stem}.md'
    created = None
    created_by_this_request = False
    try:
        created = await asyncio.to_thread(
            minio_upload_service.initialize_upload,
            upload_request_id=upload_request_id,
            parent_node_id=parent_node_id,
            file_name=display_name,
            file_size=len(normalized_markdown),
            content_type='text/markdown',
            branch_type='kiwix',
            user_id=user_id,
            kiwix_metadata=metadata,
            allow_kiwix_preparsed_markdown=True,
        )
        created_by_this_request = not bool(created.get('idempotent_replay'))
        if created.get('idempotent_replay'):
            existing = await asyncio.to_thread(
                _existing_preparsed_result, created['object_id'], created['revision_id']
            )
            if existing:
                return {
                    **created, **existing, 'file_name': display_name,
                    'source_file_name': source_file_name, 'parse_status': 'completed',
                    'parse_completion_source': 'preparsed_markdown',
                    'idempotent_replay': True,
                }
            if str(created.get('storage_status') or '').upper() != 'READY':
                raise HTTPException(status_code=409, detail='KIWIX_UPLOAD_IN_PROGRESS')

        async def chunks():
            yield normalized_markdown

        source = await minio_upload_service.upload_content(
            revision_id=int(created['revision_id']),
            user_id=user_id,
            content_length=len(normalized_markdown),
            chunks=chunks(),
            create_parse_job=False,
        )
        storage = get_minio_storage_service()
        storage_metadata = {
            'object-id': str(created['object_id']),
            'revision-id': str(created['revision_id']),
            'ingestion-mode': 'preparsed',
        }
        content_location = artifact_location(
            KbStorageScope.SE_KB, created['object_id'], created['revision_id'], 'content.md'
        )
        stored_content = await asyncio.to_thread(
            _upload_bytes,
            storage, content_location.bucket_name, content_location.object_key,
            normalized_markdown, 'text/markdown', storage_metadata,
        )
        asset_entries = []
        for path, payload in safe_assets.items():
            location = artifact_location(
                KbStorageScope.SE_KB, created['object_id'], created['revision_id'], f'assets/{path}'
            )
            stored = await asyncio.to_thread(
                _upload_bytes,
                storage, location.bucket_name, location.object_key,
                payload, mimetypes.guess_type(path)[0] or 'application/octet-stream', storage_metadata,
            )
            asset_entries.append({
                'source_path': path, 'object_key': stored.object_key,
                'version_id': stored.version_id,
            })
        manifest = build_manifest(
            content_key=stored_content.object_key,
            content_version=stored_content.version_id,
            viewer={
                'bucket_name': created['bucket_name'],
                'object_key': created['object_key'],
                'version_id': source['minio_version_id'],
                'media_type': 'text/markdown',
                'file_ext': 'md',
            },
            assets=asset_entries,
            document_metadata={
                'schema': metadata.get('schema'),
                'stage': metadata.get('stage'),
            },
        )
        manifest_location = artifact_location(
            KbStorageScope.SE_KB, created['object_id'], created['revision_id'], 'manifest.json'
        )
        stored_manifest = await asyncio.to_thread(
            _upload_bytes,
            storage, manifest_location.bucket_name, manifest_location.object_key,
            manifest, 'application/json', storage_metadata,
        )
        jobs = await asyncio.to_thread(
            complete_preparsed_jobs,
            object_id=int(created['object_id']),
            revision_id=int(created['revision_id']),
            user_id=user_id,
            artifact_bucket=stored_manifest.bucket_name,
            artifact_key=stored_manifest.object_key,
            artifact_version=stored_manifest.version_id,
            artifact_sha256=stored_manifest.sha256,
            artifact_size=stored_manifest.size_bytes,
        )
        return {
            **source, **jobs, 'file_name': display_name,
            'source_file_name': source_file_name,
            'parse_status': 'completed',
            'parse_completion_source': 'preparsed_markdown',
            'index_status': 'pending',
        }
    except HTTPException:
        if created and created_by_this_request:
            try:
                await asyncio.to_thread(_remove_failed_registration, created)
            except Exception:
                pass
        raise
    except Exception as exc:
        if created and created_by_this_request:
            try:
                await asyncio.to_thread(_remove_failed_registration, created)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail={
            'code': 'KIWIX_PREPARSED_UPLOAD_FAILED',
            'file_name': source_file_name,
            'reason': str(exc)[:300],
            'retryable': True,
        }) from exc
