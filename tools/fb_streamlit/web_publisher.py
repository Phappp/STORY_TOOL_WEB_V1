"""Local Playwright publisher for the admin-only website.

No HTTP publishing API. B1 caption -> Title; B2 image -> Featured Image;
B3 markdown -> Description. SEO Title/Keywords/Description are NEVER filled.

Selectors follow the supplied Vue/TinyMCE admin HTML. No URL is inferred
from the slug. Ambiguous saves are NOT auto-retried.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urljoin
import re
import unicodedata
from difflib import SequenceMatcher

import formats
import stories

DEFAULT_SITE = 'https://usnewspv247.danhngon.pro'
ADMIN_URL = DEFAULT_SITE + '/admin/posts'
# P37d: giới hạn domain *.danhngon.pro (trước ép 2 constants) đã bỏ — anh thêm site tùy ý trong
# Cấu hình ▸ Website (news_sites.json). DEFAULT_SITE chỉ là fallback khi event webpub_* không
# mang `site` (modal News luôn gửi site từ danh sách cấu hình).
CONFIG_NAME = '.web-publisher-selectors.json'
PROFILE_NAME = '.web-publisher-browser'

DEFAULT_SELECTORS = {
    'new_post': 'button:has-text("New post")',
    'title': 'label.field-wrapper:has(> span:text-is("Title")) input',
    'image_file': '.post-image-upload-surface input[type="file"][accept="image/*"]',
    'image_choose': 'label:has-text("Choose image")',
    'description': 'body#tinymce[contenteditable="true"], body#tinymce',
    'description_iframe': '.tox-edit-area__iframe',
    'published': 'label:has-text("Published") button[role="checkbox"]',
    'save': 'form.post-editor-form button[type="submit"]:has-text("Save")',
    'posts_rows': 'table.posts-responsive-table tbody tr',
    'copy_link_in_row': '.posts-row-actions button:has(svg.lucide-clipboard)',
    'public_link_in_row': '.posts-row-actions a[title="View post"][href]',
}



def normalized_site(raw: str) -> str:
    """Accept a website origin, /login, /admin/dashboard or /admin/posts URL.

    P37d: KHÔNG giới hạn domain *.danhngon.pro nữa — anh thêm site tùy ý trong Cấu hình ▸ Website
    (news_sites.json). Chỉ còn yêu cầu HTTPS + host hợp lệ + không có user/pass/query/port/fragment.
    """
    raw = (raw or '').strip()
    if not raw:
        raise PublishError('Chưa nhập website.')
    if '://' not in raw:
        raw = 'https://' + raw
    parsed = urlparse(raw)
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not host or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment:
        raise PublishError('Website phải là URL HTTPS hợp lệ, không chứa mật khẩu, query hay port.')
    return f'https://{host}'


def posts_url(site: str) -> str:
    return normalized_site(site) + '/admin/posts'


def login_url(site: str) -> str:
    return normalized_site(site) + '/login'


def profile_name(site: str) -> str:
    host = urlparse(normalized_site(site)).hostname or ''
    # P37d: giữ đúng login profile cũ của site mặc định (DEFAULT_SITE), site khác theo host riêng.
    default_host = urlparse(DEFAULT_SITE).hostname or ''
    return PROFILE_NAME if host == default_host else PROFILE_NAME + '-' + re.sub(r'[^a-z0-9.-]', '_', host)


class PublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class Materials:
    sid: str
    title: str
    article: str
    image: Path


def materials(root: Path, sid: str) -> Materials:
    story = stories.load_story(root, sid)
    if story.get('link'):
        raise PublishError(f'{sid}: đã có Link, không xuất bản lại.')
    title = stories.step_text(root, sid, 'b1').strip()
    article = stories.step_text(root, sid, 'b3').strip()
    image = stories.image_of(root, sid)
    missing = [name for name, value in [('B1 Caption', title), ('B2 Image', image), ('B3 SEO article', article)] if not value]
    if missing:
        raise PublishError(f'{sid}: thiếu {", ".join(missing)}.')
    if not image.is_file():
        raise PublishError(f'{sid}: không tìm thấy ảnh {image}.')
    return Materials(sid, title, article, image)


def selectors_path(root: Path) -> Path:
    return Path(root) / CONFIG_NAME


def load_selectors(root: Path) -> dict[str, str]:
    path = selectors_path(root)
    override = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(override, dict) or any(k not in DEFAULT_SELECTORS or not isinstance(v, str) for k, v in override.items()):
        raise PublishError('File selector JSON không hợp lệ.')
    return {**DEFAULT_SELECTORS, **override}


def validate_public_url(candidate: str) -> str:
    """P37d: KHÔNG ép *.danhngon.pro — chỉ kiểm tra URL HTTPS hợp lệ + không có user/pass.

    Trước (P28a) ép host phải endswith .danhngon.pro vì chỉ 2 site cố định. Nay anh tự thêm site
    trong Cấu hình ▸ Website (news_sites.json) nên không thể hardcode domain. Link được đối chiếu
    theo CHUỖI (title / slug) chứ không theo domain, nên bỏ domain là không tăng nguy cơ đăng trùng.
    """
    url = (candidate or '').strip()
    parsed = urlparse(url)
    if (parsed.scheme != 'https' or not parsed.hostname
            or parsed.username or parsed.password):
        raise PublishError('Không xác minh được URL bài công khai; không tự đoán link.')
    return url


def browser_context(root: Path, playwright, headed: bool = False, *, site: str = DEFAULT_SITE):
    """Persistent local login, deliberately outside workspace story folders."""
    profile = Path(root) / profile_name(site)
    profile.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        str(profile), headless=not headed, accept_downloads=False,
        viewport={'width': 1440, 'height': 900}, locale='en-US',
    )


def login(root: Path, *, site: str = DEFAULT_SITE, headed: bool = True) -> None:
    """Launch visible browser for the operator to log in; close when finished.

    Note: close browser AFTER login, then press the publisher button in Streamlit.
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        context = browser_context(root, p, headed=headed, site=site)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(login_url(site), wait_until='domcontentloaded', timeout=45000)
            # Operator interacts with actual browser; no password/cookie transmitted to Streamlit.
            try:
                page.wait_for_event('close', timeout=0)
            except Exception:
                # Closing the browser window also closes its persistent context.
                pass
        finally:
            try:
                context.close()
            except Exception:
                pass


def _one(page, css: str, label: str):
    if not css:
        raise PublishError(f'Chưa cấu hình selector: {label}')
    matches = page.locator(css)
    if matches.count() != 1:
        raise PublishError(f'{label}: tìm thấy {matches.count()} phần tử (cần đúng 1). Hãy chỉnh selector JSON theo HTML thật.')
    return matches.first


def _fill_editor(page, sel: dict[str, str], markdown: str) -> None:
    """Fill B3 into the Vue/TinyMCE editor and trigger both editor and form events.

    The hidden textarea is not the editable surface. The TinyMCE instance must
    be initialized and the resulting content must survive the editor -> form sync.
    """
    html = formats.to_html(markdown)
    if not html.strip():
        raise PublishError('B3 trống sau khi chuyển sang HTML; chưa bấm Save.')
    iframe = page.locator(sel['description_iframe'])
    iframe.first.wait_for(state='visible', timeout=45000)
    if iframe.count() != 1:
        raise PublishError('Không xác định được chính xác iframe TinyMCE.')
    page.wait_for_function("""() => {
      if (!window.tinymce) return false;
      const iframe = document.querySelector('.tox-edit-area__iframe');
      if (!iframe) return false;
      const ed = window.tinymce.get(iframe.id.replace(/_ifr$/, ''));
      return Boolean(ed && ed.initialized && ed.getBody());
    }""", timeout=45000)
    # Select the editor by iframe ID rather than activeEditor (which can be stale).
    result = page.evaluate("""html => {
      const iframe = document.querySelector('.tox-edit-area__iframe');
      const editorId = iframe && iframe.id.replace(/_ifr$/, '');
      const ed = editorId && window.tinymce && window.tinymce.get(editorId);
      if (!ed || !ed.initialized) return {ok:false, reason:'editor not initialized'};
      ed.setContent(html);
      ed.setDirty(true);
      ed.fire('input');
      ed.fire('change');
      ed.save();
      const source = document.getElementById(editorId);
      if (source) {
        source.dispatchEvent(new Event('input', {bubbles:true}));
        source.dispatchEvent(new Event('change', {bubbles:true}));
      }
      const current = ed.getContent();
      return {ok: Boolean(current && current.trim()), length:current.length,
              text:ed.getBody().innerText || ''};
    }""", html)
    if not result or not result.get('ok'):
        raise PublishError(f'TinyMCE chưa nhận B3: {result}')
    # A real editor buffer check, not merely a successful JS call.
    expected = len(re.sub(r'\s+', '', markdown))
    actual = len(re.sub(r'\s+', '', result.get('text') or ''))
    if actual < 30 or (expected > 300 and actual < expected * .25):
        raise PublishError(f'Nội dung B3 trong editor quá ngắn ({actual} ký tự); chưa bấm Save.')
    page.wait_for_timeout(500)  # allow Vue's debounced state updates to complete
    verified = page.evaluate("""() => {
      const iframe = document.querySelector('.tox-edit-area__iframe');
      const editorId = iframe && iframe.id.replace(/_ifr$/, '');
      const ed = editorId && window.tinymce && window.tinymce.get(editorId);
      return ed ? ed.getContent().trim().length : 0;
    }""")
    if verified < 30:
        raise PublishError('TinyMCE mất nội dung B3 sau khi đồng bộ; chưa bấm Save.')


def _uploaded_image_url(page) -> str:
    """Read the uploaded image from either Vue form state rendered in the DOM.

    Before upload, the input has type=url. After upload, this website replaces
    it with an untyped readonly input and an img preview. Never depend on the
    original input[type=url] remaining mounted.
    """
    return page.locator('.post-image-upload-surface').evaluate("""surface => {
        const field = surface.querySelector('input[readonly]') ||
                      surface.querySelector('input[type="url"]');
        const preview = surface.querySelector('img[alt="Featured image URL"]');
        const values = [field?.value, preview?.getAttribute('src')];
        for (const val of values) {
            try {
                const u = new URL((val || '').trim());
                if (u.protocol === 'https:' && u.hostname) return u.href;
            } catch (_) {}
        }
        return '';
    }""")


def _upload_image(page, sel: dict[str, str], image: Path) -> None:
    """Upload B2, then verify the *post-upload* DOM, not the old URL selector."""
    surface = page.locator('.post-image-upload-surface')
    if surface.count() != 1:
        raise PublishError('Không xác định được vùng Featured Image.')
    if _uploaded_image_url(page):
        raise PublishError('Featured Image đã có URL trước khi upload; không ghi đè ảnh không rõ nguồn.')
    files = page.locator(sel['image_file'])
    if files.count() == 1:
        files.set_input_files(str(image))
    elif sel.get('image_choose'):
        with page.expect_file_chooser(timeout=12000) as chooser:
            _one(page, sel['image_choose'], 'Choose image').click()
        chooser.value.set_files(str(image))
    else:
        raise PublishError('Không tìm được đúng input upload ảnh B2.')
    try:
        page.wait_for_function("""() => {
            const surface = document.querySelector('.post-image-upload-surface');
            if (!surface) return false;
            const preview = surface.querySelector('img[alt="Featured image URL"]');
            const readonly = surface.querySelector('input[readonly]');
            // The post-upload Vue component changes input[type=url] to input[readonly].
            // Require the rendered uploaded state and a remote HTTPS URL.
            if (!preview || !readonly) return false;
            const url = (readonly.value || preview.getAttribute('src') || '').trim();
            try {
                const parsed = new URL(url);
                return parsed.protocol === 'https:' && !!parsed.hostname &&
                    (preview.complete && preview.naturalWidth > 0);
            } catch (_) { return false; }
        }""", timeout=120000, polling=500)
    except Exception as exc:
        raise PublishError(
            'Upload ảnh B2 chưa được xác nhận sau 120 giây: cần ảnh xem trước đã tải '
            'và ô URL readonly xuất hiện. Kiểm tra HTML thực tế hoặc lỗi tải ảnh.'
        ) from exc
    uploaded_url = _uploaded_image_url(page)
    if not uploaded_url.startswith('https://'):
        raise PublishError('Ảnh đã hiển thị nhưng chưa lấy được URL HTTPS; không Save.')


def _form_diagnostics(page, sel: dict[str, str]) -> str:
    """Readable, non-secret summary of the site's client-side validation state."""
    try:
        return page.evaluate("""(saveSelector) => {
            const f = document.querySelector('form.post-editor-form');
            const b = f?.querySelector('button[type="submit"]');
            const surface = document.querySelector('.post-image-upload-surface');
            const image = surface?.querySelector('input[readonly], input[type="url"]');
            const preview = surface?.querySelector('img[alt="Featured image URL"]');
            const title = Array.from(f?.querySelectorAll('label.field-wrapper') || [])
                .find(el => el.querySelector('span')?.textContent?.trim() === 'Title')?.querySelector('input');
            const ed = window.tinymce?.activeEditor;
            const bad = Array.from(f?.querySelectorAll(':invalid') || [])
                .map(el => ({field: el.getAttribute('name') || el.getAttribute('placeholder') || el.tagName,
                             message: el.validationMessage})).slice(0, 6);
            return JSON.stringify({url:location.href, saveFound:!!b, saveDisabled:!!b?.disabled,
                titleLength:title?.value?.length||0, imageUrlPresent:!!(image?.value?.trim() || preview?.getAttribute('src')),
                descriptionChars:ed?.getBody()?.innerText?.trim()?.length||0, invalidFields:bad});
        }""", sel['save'])
    except Exception as exc:
        return f'Không đọc được trạng thái form: {type(exc).__name__}: {exc}'


def _submit_and_confirm(page, sel: dict[str, str]) -> None:
    """Click the actual submit button; do not navigate away from a rejected form."""
    save = page.locator(sel['save'])
    save.wait_for(state='visible', timeout=15000)
    if save.count() != 1:
        raise PublishError(f'Nút Save không duy nhất ({save.count()}). {_form_diagnostics(page, sel)}')
    if not save.is_enabled():
        raise PublishError('Nút Save đang bị vô hiệu hóa. ' + _form_diagnostics(page, sel))
    form_valid = page.locator('form.post-editor-form').evaluate('(f) => f.checkValidity()')
    if not form_valid:
        raise PublishError('Form có trường không hợp lệ, chưa bấm Save. ' + _form_diagnostics(page, sel))
    try:
        save.click(timeout=15000)
    except Exception as exc:
        raise PublishError('Không thể bấm nút Save: ' + _form_diagnostics(page, sel)) from exc
    # Vue may keep the form open while the request is in flight. Never assume
    # that click == successful publication, and never navigate away prematurely.
    try:
        page.locator('form.post-editor-form').wait_for(state='hidden', timeout=45000)
    except Exception as exc:
        diag = _form_diagnostics(page, sel)
        try:
            visible = page.locator('body').inner_text(timeout=3000)
            visible = ' '.join(visible.split())[-700:]
        except Exception:
            visible = '(không đọc được nội dung trang)'
        raise PublishError('Đã bấm Save nhưng form Create post chưa đóng sau 45 giây. '
                           f'Có thể website từ chối lưu. {diag}; nội dung hiển thị cuối: {visible}') from exc

def _publish_checkbox(page, sel: dict[str, str]) -> None:
    check = _one(page, sel['published'], 'Published')
    if check.get_attribute('role') == 'checkbox':
        if check.get_attribute('aria-checked') != 'true':
            check.click()
        if check.get_attribute('aria-checked') != 'true':
            raise PublishError('Không bật được Published.')
    elif check.get_attribute('type') == 'checkbox':
        check.check()
    else:
        raise PublishError('Published không phải checkbox hợp lệ.')


def _normalize_title(value: str) -> str:
    """Normalize visible text without assuming the admin retained whitespace/emoji."""
    return ' '.join(unicodedata.normalize('NFKC', value or '').replace('\ufe0f', '').split()).casefold()


def _title_key(value: str) -> str:
    """Compare textual caption content when the website changes punctuation/emoji."""
    normalized = unicodedata.normalize('NFKC', value or '').casefold()
    return ' '.join(''.join(ch if ch.isalnum() else ' ' for ch in normalized).split())


def _title_match_quality(expected: str, actual: str) -> str | None:
    expected_norm, actual_norm = _normalize_title(expected), _normalize_title(actual)
    if not expected_norm or not actual_norm:
        return None
    if expected_norm == actual_norm:
        return 'exact'
    expected_key, actual_key = _title_key(expected), _title_key(actual)
    if expected_key and expected_key == actual_key:
        return 'normalized'
    if len(actual_key) >= 120 and len(expected_key) > len(actual_key) and expected_key.startswith(actual_key):
        if len(actual_key) >= 900:
            return 'truncated'
    if min(len(expected_key), len(actual_key)) >= 120:
        ratio = SequenceMatcher(None, expected_key, actual_key, autojunk=False).ratio()
        length_gap = abs(len(expected_key) - len(actual_key)) / max(len(expected_key), len(actual_key))
        if ratio >= .965 and length_gap <= .04:
            return 'near_exact'
    return None


def _row_link(page, row, sel: dict[str, str]) -> str | None:
    """Read View post href from the SAME <tr> as Caption B1; never guess slug."""
    anchor = row.locator(sel['public_link_in_row'])
    if anchor.count() != 1:
        return None
    try:
        return validate_public_url(urljoin(page.url, anchor.get_attribute('href') or ''))
    except PublishError:
        return None


def _read_post_rows(page, sel: dict[str, str]) -> list[dict[str, str]]:
    """Extract from live DOM, tolerate Vue rendering differences and custom selector overrides.

    Critically, don't silently discard a real row because one selector is overly strict.
    Preserve the SAME-row link/title relationship.  No guessed slugs.
    """
    js = r"""(preferred) => {
      const queries = [preferred, 'table.posts-responsive-table tbody tr',
        '.posts-table-wrapper tbody tr', '.posts-responsive-table tr',
        'table tbody tr'];
      let rows = [];
      for (const q of queries) {
        try { const found = [...document.querySelectorAll(q)];
          if (found.length > rows.length) rows = found;
        } catch (_) {}
      }
      return rows.map(row => {
        let title = '';
        const button = row.querySelector('button[aria-label^="Copy title"]') ||
          [...row.querySelectorAll('button')].find(b => /copy title/i.test(b.getAttribute('aria-label')||''));
        if (button) title = (button.getAttribute('aria-label')||'').replace(/^Copy title\s*:\s*/i,'').trim() || button.innerText.trim();
        if (!title) {
          const postCell = row.querySelector('td:nth-child(2)');
          title = (postCell?.querySelector('button')?.innerText || '').trim();
        }
        const anchor = row.querySelector('a[title="View post"][href]') ||
          row.querySelector('.posts-row-actions a[href*="/blog/"]') ||
          row.querySelector('a[href*="/blog/"]');
        const image = row.querySelector('td img[src]');
        return {title, url: anchor?.href || '', image: image?.src || ''};
      });
    }"""
    raw = page.evaluate(js, sel.get('posts_rows', ''))
    output = []
    for item in raw:
        if not item.get('title') or not item.get('url'):
            continue
        try:
            url = validate_public_url(item['url'])
        except PublishError:
            continue
        output.append({'title': item['title'], 'url': url, 'image': item.get('image') or ''})
    return output


def _posts_dom_diagnostics(page, sel: dict[str, str]) -> dict:
    """Report raw DOM counts separately from rows accepted for matching."""
    return page.evaluate(r"""(preferred) => {
      const safeCount = q => {try { return document.querySelectorAll(q).length; } catch(e){return -1;}};
      const rows = [...document.querySelectorAll('table tbody tr')];
      return {url:location.href, ready:document.readyState,
        preferredRows:safeCount(preferred), tables:safeCount('table'),
        allTableRows:rows.length, postTableRows:safeCount('table.posts-responsive-table tbody tr'),
        titleButtons:safeCount('button[aria-label^="Copy title"]'),
        viewLinks:safeCount('a[title="View post"][href]'),
        blogLinks:safeCount('a[href*="/blog/"]'),
        bodyExcerpt:(document.body?.innerText||'').slice(0,250)};
    }""",sel.get('posts_rows',''))


def _await_posts_data(page, sel: dict[str, str], timeout_ms: int = 45000) -> None:
    """Wait until at least one valid same-row title/link pair is present, not just an empty table."""
    try:
        page.wait_for_function(r"""() => {
          const rows = document.querySelectorAll('table tbody tr');
          return [...rows].some(row =>
            row.querySelector('a[href*="/blog/"]') &&
            (row.querySelector('button[aria-label^="Copy title"]') ||
             row.querySelector('td:nth-child(2) button')));
        }""", timeout=timeout_ms)
    except Exception as exc:
        diag = _posts_dom_diagnostics(page,sel)
        raise PublishError('Danh sách Posts hiển thị nhưng không đọc được hàng có Title + URL sau '
                           f'{timeout_ms//1000}s. DOM: {diag}') from exc


def _visible_rows(page, sel: dict[str, str], expected: str) -> list[tuple[str, str, str]]:
    return [(r['url'], quality, r['title']) for r in _read_post_rows(page, sel)
            if (quality := _title_match_quality(expected, r['title']))]


def recent_post_candidates(root: Path, *, site: str, limit: int = 60) -> list[dict[str, str]]:
    """Read-only list for manual reconciliation; does NOT create or save a post."""
    sel = load_selectors(Path(root))
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        context = browser_context(Path(root), p, site=site)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(posts_url(site), wait_until='domcontentloaded', timeout=45000)
            _await_posts_data(page, sel, timeout_ms=45000)
            return [{'title': r['title'], 'url': r['url'], 'image': r.get('image', '')}
                    for r in _read_post_rows(page, sel)[:limit]]
        finally:
            context.close()


def _pagination_next(page):
    """Find a real enabled Next control; don't assume one specific pagination library."""
    choices = [
        'button[aria-label="Next Page"]',
        'button[aria-label="Go to next page"]',
        'button[aria-label="Next page"]',
        'button[title="Next page"]',
        'button:has-text("Next")',
        'a[aria-label="Next Page"]',
        'a[aria-label="Next page"]',
        'a:has-text("Next")',
    ]
    for css in choices:
        nodes = page.locator(css)
        if nodes.count() == 1:
            node = nodes.first
            try:
                if node.is_visible() and node.is_enabled() and node.get_attribute('aria-disabled') != 'true':
                    return node
            except Exception:
                pass
    return None


def _find_url_after_save(page, sel: dict[str, str], title: str, *, site: str = DEFAULT_SITE,
                         max_pages: int = 50, image_url: str = '') -> str:
    """Reconcile from actual Posts DOM. Never publish/retry Save here.

    Prefer an unambiguous title match; use the just-uploaded image URL as an
    independent check when available. Never auto-select among ambiguous matches.
    """
    page.goto(posts_url(site), wait_until='domcontentloaded', timeout=45000)
    _await_posts_data(page, sel, timeout_ms=45000)
    matches: dict[str, tuple[str, str]] = {}
    image_matches: dict[str, str] = {}
    # Preserve row-specific image identity for duplicated titles.
    images_by_url: dict[str, str] = {}
    samples = []
    scanned_pages = 0
    last_dom = {}
    seen_fingerprints = set()
    expected_key = _title_key(title)
    normalized_image = (image_url or '').split('?', 1)[0].strip()
    for idx in range(max_pages):
        _await_posts_data(page, sel, timeout_ms=20000)
        rows = _read_post_rows(page, sel)
        last_dom = _posts_dom_diagnostics(page, sel)
        fingerprint = tuple(r['url'] for r in rows)
        if not fingerprint or fingerprint in seen_fingerprints:
            break
        seen_fingerprints.add(fingerprint)
        scanned_pages += 1
        for r in rows:
            images_by_url[r['url']] = (r.get('image') or '').split('?', 1)[0].strip()
            quality = _title_match_quality(title, r['title'])
            if quality:
                matches[r['url']] = (quality, r['title'])
            if normalized_image and (r['image'] or '').split('?', 1)[0] == normalized_image:
                # Require substantial title agreement to avoid reused featured images.
                actual_key = _title_key(r['title'])
                similarity = SequenceMatcher(None, expected_key, actual_key, autojunk=False).ratio()
                if min(len(expected_key), len(actual_key)) > 60 and similarity >= .80:
                    image_matches[r['url']] = r['title']
            if len(samples) < 120:
                samples.append(r)
        # No need to paginate when the current page already identifies one
        # exact unique caption; continue scanning if the match is only fuzzy.
        exact = [u for u, (q, _) in matches.items() if q in ('exact', 'normalized')]
        if len(exact) == 1 and scanned_pages == 1 and (not normalized_image or images_by_url.get(exact[0]) == normalized_image):
            return exact[0]
        nxt = _pagination_next(page)
        if nxt is None:
            break
        old = fingerprint
        nxt.click(timeout=15000)
        try:
            page.wait_for_function("""({sel, old}) => {
                const urls = Array.from(document.querySelectorAll('table tbody tr'))
                    .map(row => row.querySelector('a[href*="/blog/"]')?.href || '').filter(Boolean);
                return urls.length && JSON.stringify(urls) !== JSON.stringify(old);
            }""", arg={'sel': sel['posts_rows'], 'old': list(old)}, timeout=15000)
        except Exception:
            break

    exact = [u for u, (q, _) in matches.items() if q in ('exact', 'normalized')]
    # With repeated captions, a UNIQUE exact uploaded-image match is a reliable
    # second identifier. Never use ordering/first row to resolve ambiguity.
    exact_image = [u for u in exact if normalized_image and images_by_url.get(u) == normalized_image]
    if len(exact_image) == 1:
        return exact_image[0]
    if len(exact_image) > 1:
        raise PublishError(f'Có {len(exact_image)} bài trùng cả Caption B1 và ảnh B2; cần chọn Link thủ công.')
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        diagnostic = '; '.join(f'{u} [image={images_by_url.get(u) or "none"}]' for u in exact[:5])
        raise PublishError(f'Có {len(exact)} URL trùng Caption B1 nhưng không có duy nhất một URL khớp ảnh B2. '
                           f'Ảnh B2 đã lưu: {normalized_image or "(không có)"}. Các ứng viên: {diagnostic}. '
                           'Hãy chọn Link thủ công; không đăng lại.')
    fuzzy_urls = list(matches)
    fuzzy_with_image = [u for u in fuzzy_urls if normalized_image and images_by_url.get(u) == normalized_image]
    if len(fuzzy_with_image) == 1:
        return fuzzy_with_image[0]
    if len(fuzzy_urls) == 1:
        return fuzzy_urls[0]
    if len(fuzzy_urls) > 1:
        raise PublishError(f'Có {len(fuzzy_urls)} URL gần giống Caption B1; hãy đối soát thủ công.')
    if len(image_matches) == 1:
        return next(iter(image_matches))
    if len(image_matches) > 1:
        raise PublishError(f'Có {len(image_matches)} URL cùng ảnh B2; hãy đối soát thủ công.')
    closest = sorted(samples, key=lambda r: SequenceMatcher(None, expected_key, _title_key(r['title']),
                     autojunk=False).ratio(), reverse=True)[:3]
    previews = '; '.join(f"{r['title'][:90]}..." for r in closest)
    raise PublishError(
        f'Không tìm thấy bài khớp sau khi đọc {len(samples)} hàng trên {scanned_pages} trang Posts. '
        f'URL hiện tại: {page.url}. DOM: {last_dom}. Title gần nhất: {previews or "(không đọc được hàng)"}. '
        'Không đăng lại; dùng nút đối soát hoặc kiểm tra trang phân trang.'
    )


def reconcile_auto(root: Path, sid: str, *, site: str | None = None, headed: bool = False) -> str:
    """Recover public URL without republishing a post already saved on site."""
    root = Path(root)
    story = stories.load_story(root, sid)
    if story.get('link'):
        return validate_public_url(story['link'])
    pub = story.get('web_publish') or {}
    if pub.get('state') not in ('saving', 'needs_review'):
        raise PublishError(f'{sid}: không ở trạng thái cần đối soát; không tự tạo bài.')
    # Published site must match the original publish attempt to avoid wrong-site links.
    original_site = normalized_site(pub.get('site') or DEFAULT_SITE)
    if site and normalized_site(site) != original_site:
        raise PublishError(f'{sid}: đã Save trên {original_site}; hãy chọn đúng site này để đối soát.')
    title = stories.step_text(root, sid, 'b1').strip()
    if not title:
        raise PublishError(f'{sid}: thiếu Caption B1 để tìm bài.')
    sel = load_selectors(root)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        context = browser_context(root, p, headed=headed, site=original_site)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            url = _find_url_after_save(page, sel, title, site=original_site, image_url=pub.get('image_url') or '')
        finally:
            context.close()
    stories.update_story(root, sid, link=url,
                         web_publish={'state': 'done', 'site': original_site, 'url': url, 'at': time.time()})
    return url


def republish(root: Path, sid: str, *, site: str = DEFAULT_SITE) -> str:
    """Đăng lại một bài đã có link: giữ link cũ trong history, xóa link hiện tại,
    tạo bài mới trên website và ghi link mới.

    `web_publish` history được append 1 entry để truy vết lần đăng cũ.
    Trạng thái chuyển: done -> saving -> done (link mới).
    """
    root = Path(root)
    site = normalized_site(site)
    story = stories.load_story(root, sid)
    old_link = story.get('link') or ''
    if not old_link:
        raise PublishError(f'{sid}: chưa có link nào để đăng lại — dùng Đăng mới (webpub_start).')
    pub = dict(story.get('web_publish') or {})
    history = list(story.get('web_publish_history') or [])
    history.append({'state': pub.get('state', 'done'), 'url': old_link,
                    'site': pub.get('site', site), 'at': pub.get('at', 0),
                    'action': 'republish_requested'})
    # Xóa link + state để publish_one coi đây là bài mới
    stories.update_story(root, sid, link='',
                         web_publish={'state': 'ready', 'site': site, 'at': time.time(),
                                      'previous_link': old_link, 'republishing': True},
                         web_publish_history=history[-30:])
    url = publish_one(root, sid, site=site)
    return url


def reset_needs_review_for_new_post(root: Path, sid: str, *, site: str) -> None:
    """Explicit operator-requested NEW publication for an unresolved SID.

    The prior attempt may already exist on the site; retain it in history.
    This NEVER happens during normal batch processing.
    """
    root = Path(root)
    story = stories.load_story(root, sid)
    if story.get('link'):
        raise PublishError(f'{sid}: đã có Link; không thể reset để đăng mới.')
    previous = story.get('web_publish') or {}
    if previous.get('state') != 'needs_review':
        raise PublishError(f'{sid}: chỉ có thể làm mới trạng thái needs_review.')
    site = normalized_site(site)
    past = story.get('web_publish_history') or []
    if not isinstance(past, list):
        past = []
    past.append({**previous, 'abandoned_at': time.time(), 'action': 'new_post_requested'})
    stories.update_story(root, sid, web_publish_history=past[-30:],
                         web_publish={'state': 'ready', 'site': site, 'at': time.time(),
                                      'previous_attempt_may_exist': True})


def publish_one(root: Path, sid: str, *, headed: bool = False, site: str = DEFAULT_SITE) -> str:
    """Synchronous, conservative single-post publisher.

    Any uncertain save becomes `needs_review` and is blocked on rerun. This
    protects against duplicate posts if website saved but link extraction failed.
    """
    root = Path(root)
    site = normalized_site(site)
    story = stories.load_story(root, sid)
    pub = story.get('web_publish') or {}
    if pub.get('state') in ('saving', 'needs_review', 'done'):
        # republishing=True được set bởi republish() ngay trước khi gọi publish_one
        if not pub.get('republishing'):
            raise PublishError(f'{sid}: trạng thái xuất bản {pub["state"]}; cần đối soát trước khi thử lại.')
    item = materials(root, sid)
    sel = load_selectors(root)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PublishError('Chưa cài Playwright. Chạy: pip install playwright && python -m playwright install chromium') from exc
    with sync_playwright() as p:
        context = browser_context(root, p, headed=headed, site=site)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(posts_url(site), wait_until='domcontentloaded', timeout=45000)
            # The admin is a Vue SPA: DOMContentLoaded often fires before Posts renders.
            # Never treat an immediate locator.count() == 0 as proof of missing login.
            try:
                page.locator(sel['new_post']).first.wait_for(state='visible', timeout=30000)
            except Exception as exc:
                actual_url = page.url
                try:
                    heading = page.title()
                except Exception:
                    heading = '(không đọc được title)'
                try:
                    visible_text = (page.locator('body').inner_text(timeout=5000) or '')[:350].replace('\n', ' ')
                except Exception:
                    visible_text = '(không đọc được nội dung trang)'
                raise PublishError(
                    'Không thấy nút New post sau 30 giây. Không khẳng định là mất đăng nhập. '
                    f'URL thực tế: {actual_url}; tiêu đề tab: {heading}; nội dung: {visible_text}. '
                    'Kiểm tra tài khoản, quyền tạo bài và profile Chromium đang dùng.'
                ) from exc
            _one(page, sel['new_post'], 'New post').click()
            page.locator('form.post-editor-form').wait_for(timeout=30000)
            stage = 'Điền Title B1'
            _one(page, sel['title'], 'Title').fill(item.title)
            # Important: do not block Description on an unfinished image upload.
            stage = 'Nhập Description B3 (TinyMCE)'
            _fill_editor(page, sel, item.article)
            stage = 'Upload Featured Image B2'
            _upload_image(page, sel, item.image)
            uploaded_image_url = _uploaded_image_url(page)
            if not uploaded_image_url:
                raise PublishError('Ảnh B2 chưa được lưu vào form; không Save.')
            stage = 'Bật Published'
            _publish_checkbox(page, sel)
            stage = 'Kiểm tra Description trước Save'
            # Final content sanity check immediately before Save.
            editor_text = page.frame_locator(sel['description_iframe']).locator('body#tinymce').inner_text()
            if len(editor_text.strip()) < 30:
                raise PublishError('Description (B3) trống trước Save; không đăng.')
            # Deliberately do not touch SEO Title / SEO Keywords / SEO Description.
            stage = 'Kiểm tra điều kiện nút Save'
            button = page.locator(sel['save'])
            button.wait_for(state='visible', timeout=15000)
            if button.count() != 1 or not button.is_enabled():
                raise PublishError('Không thể bấm Save: ' + _form_diagnostics(page, sel))
            # Mark attempted BEFORE clicking: after a click network outcome can be
            # ambiguous and needs_review protects against duplicate posts.
            stage = 'Bấm Save và đợi xác nhận'
            stories.update_story(root, sid, web_publish={'state':'saving', 'site':site, 'image_url':uploaded_image_url, 'at':time.time()})
            _submit_and_confirm(page, sel)
            stage = 'Đối soát Link sau Save'
            url = _find_url_after_save(page, sel, item.title, site=site, image_url=uploaded_image_url)
            stories.update_story(root, sid, link=url, web_publish={'state':'done','site':site,'url':url,'at':time.time()})
            return url
        except Exception as exc:
            detail = f'Bước {locals().get("stage", "Mở Posts/Create post")}: {exc}'
            status = (stories.load_story(root, sid).get('web_publish') or {}).get('state')
            if status == 'saving':
                stories.update_story(root, sid, web_publish={'state':'needs_review', 'site':site, 'image_url':(stories.load_story(root, sid).get('web_publish') or {}).get('image_url', ''), 'reason':detail[:1000], 'at':time.time()})
            raise PublishError(detail) from exc
        finally:
            context.close()


def reconcile(root: Path, sid: str, url: str) -> None:
    """Operator verifies public link of an ambiguous publication before resuming."""
    verified = validate_public_url(url)
    stories.update_story(root, sid, link=verified, web_publish={'state':'done', 'url':verified, 'at':time.time()})
