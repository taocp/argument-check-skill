#!/usr/bin/env python3
"""Export the same analysis as Markdown, one PNG long image, and/or a PDF."""

import argparse
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path


STYLE = """
* { box-sizing: border-box; }
html, body { margin: 0; background: #fff; color: #20252b; }
body { font-family: 'Noto Sans CJK SC', 'Microsoft YaHei', 'PingFang SC',
       sans-serif; font-size: 26px; line-height: 1.8; }
main { padding: 48px 60px; display: flow-root; overflow-wrap: anywhere; }
p { margin: 0 0 22px; }
strong { color: #b42318; font-weight: 700; }
blockquote { margin: 24px 0; padding: 18px 24px; border-left: 5px solid #9ca9b8;
             background: #f3f5f7; color: #414c58; }
blockquote > :last-child, li > :last-child { margin-bottom: 0; }
ul, ol { margin: 0 0 24px; padding-left: 1.5em; }
li { padding-left: 5px; margin-bottom: 16px; }
li::marker { color: #66717f; }
hr { border: 0; border-top: 2px solid #dce1e6; margin: 36px 0; }
a { color: #245a91; text-decoration: underline; text-underline-offset: 4px; }
pre { white-space: pre-wrap; padding: 20px; background: #f3f5f7; }
code { font-family: 'Noto Sans Mono CJK SC', monospace; font-size: .92em; }
table { border-collapse: collapse; width: 100%; margin-bottom: 24px; }
th, td { border: 1px solid #cbd2da; padding: 12px; text-align: left; }
main > :last-child { margin-bottom: 0; }
@page { size: A4; margin: 15mm; }
@media print {
  * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  body { font-size: 12pt; line-height: 1.75; }
  main { padding: 0; }
  p { margin-bottom: 4mm; orphans: 3; widows: 3; }
  p:has(> strong:only-child) { break-after: avoid; }
  blockquote { margin: 4mm 0; padding: 3mm 4mm; border-left-width: 1mm;
               break-inside: avoid; }
  ul, ol { margin-bottom: 4mm; }
  li { margin-bottom: 3mm; break-inside: avoid; }
  hr { margin: 6mm 0; }
}
"""


def document(content):
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<style>""" + STYLE + """</style></head><body><main>""" + content + """</main>
<script>
window.addEventListener('load', async () => {
  await document.fonts.ready;
  const main = document.querySelector('main');
  document.documentElement.dataset.renderHeight = Math.ceil(main.getBoundingClientRect().bottom);
  document.documentElement.dataset.overflow = main.scrollWidth > main.clientWidth;
});
</script></body></html>
"""


def run_browser(browser, profile, arguments):
    result = subprocess.run(
        [browser, '--headless', '--disable-gpu', '--no-first-run',
         '--no-default-browser-check', '--disable-background-networking',
         '--hide-scrollbars', '--virtual-time-budget=1500',
         f'--user-data-dir={profile}', *arguments],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode:
        raise RuntimeError(f'Browser exited {result.returncode}: {result.stderr[-2500:]}')
    return result.stdout


def screenshot(browser, profile, source, destination, width):
    measured = run_browser(browser, profile, [f'--window-size={width},800',
                                             '--dump-dom', source.as_uri()])
    match = re.search(r'data-render-height="(\d+)"', measured)
    if not match:
        raise RuntimeError('Browser did not finish measuring the rendered text.')
    if 'data-overflow="true"' in measured:
        raise RuntimeError('Content overflows horizontally; increase --width.')
    height = int(match.group(1))
    if height > 24000:
        raise RuntimeError('Image exceeds the renderer\'s 24000-pixel height limit; increase --width.')
    run_browser(browser, profile, [f'--window-size={width},{height}',
                                  f'--screenshot={destination}', source.as_uri()])
    with destination.open('rb') as png:
        header = png.read(24)
    if header[:8] != b'\x89PNG\r\n\x1a\n' or struct.unpack('>II', header[16:24]) != (width, height):
        raise RuntimeError('Screenshot size does not match the complete measured content.')


def print_pdf(browser, profile, source, destination):
    run_browser(browser, profile, [f'--print-to-pdf={destination}',
                                  '--no-pdf-header-footer', source.as_uri()])
    with destination.open('rb') as pdf:
        if pdf.read(5) != b'%PDF-':
            raise RuntimeError('Browser did not produce a valid PDF file.')


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog='Markdown export uses Python only. PNG/PDF require Python-Markdown, Chromium/Chrome, and fonts covering the source text (including Chinese).',
    )
    parser.add_argument('source', type=Path, help='Final Markdown text, encoded in UTF-8')
    parser.add_argument('--format', dest='formats', required=True, nargs='+', choices=['markdown', 'png', 'pdf'],
                        help='Formats to export, for example: --format png pdf')
    parser.add_argument('--output', required=True, type=Path, help='New directory containing only the requested export formats')
    parser.add_argument('--width', type=int, default=1080, help='PNG width in pixels (default: %(default)s)')
    parser.add_argument('--browser', help='Chromium/Chrome executable for PNG/PDF; otherwise discovered on PATH')
    args = parser.parse_args()
    if args.width < 480:
        parser.error('--width must be at least 480 pixels')
    output = args.output.expanduser().resolve()
    if output.exists():
        parser.error('--output must be a new directory; existing results are never overwritten')
    source_bytes = args.source.expanduser().read_bytes()
    source_text = source_bytes.decode('utf-8')
    if not source_text.strip():
        parser.error('source is empty')
    formats = set(args.formats)
    if formats & {'png', 'pdf'}:
        try:
            from markdown import Markdown
        except ImportError:
            parser.error('Python-Markdown is required; follow the software installation policy before installing it')
        browser = shutil.which(args.browser) if args.browser else next(
            (path for name in ('chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable')
             if (path := shutil.which(name))), None)
        if not browser:
            parser.error('Chromium/Chrome was not found; set --browser to an installed executable')
        md = Markdown(extensions=['tables', 'fenced_code', 'sane_lists'], tab_length=2)
        # Quoted HTML is text, not an instruction to load resources or run scripts.
        md.preprocessors.deregister('html_block')
        md.inlinePatterns.deregister('html')
        rendered = md.convert(source_text)
        if re.search(r'<img\b', rendered):
            parser.error('This renderer accepts text only; Markdown images are not supported')
        if not rendered.strip():
            parser.error('source contains no renderable text')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='argumentcheck-render-', dir=output.parent) as temporary:
        work = Path(temporary)
        result = work / 'result'
        result.mkdir()
        if 'markdown' in formats:
            (result / 'analysis.md').write_bytes(source_bytes)
        if formats & {'png', 'pdf'}:
            page = work / 'analysis.html'
            page.write_text(document(rendered), encoding='utf-8')
            if 'png' in formats:
                screenshot(browser, work / 'browser-profile', page, result / 'analysis.png', args.width)
            if 'pdf' in formats:
                print_pdf(browser, work / 'browser-profile', page, result / 'analysis.pdf')
        result.rename(output)
    for path in sorted(output.iterdir()):
        print(path)


if __name__ == '__main__':
    main()
