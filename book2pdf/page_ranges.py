"""One-based range syntax shared by CLI, viewer exports and printing."""
import re


class PageRangeError(ValueError):
    pass


def parse_pages(text: str, total: int | None = None) -> list[int]:
    if not text or not text.strip() or len(text) > 4096:
        raise PageRangeError('יש להזין טווח עמודים, למשל 1-5,8,10-15')
    pages = set()
    for part in text.split(','):
        match = re.fullmatch(r'\s*([0-9]+)\s*(?:-\s*([0-9]+)\s*)?', part)
        if not match:
            raise PageRangeError('טווח לא תקין. השתמשו במספרים, פסיקים ומקפים בלבד.')
        first = int(match[1])
        last = int(match[2] or match[1])
        if first < 1 or last < first:
            raise PageRangeError('מספרי העמודים מתחילים ב־1, וסוף הטווח חייב להיות אחרי תחילתו.')
        if last > (total if total is not None else 1_000_000):
            raise PageRangeError(f'עמוד מחוץ לטווח. בספר יש {total} עמודים.' if total is not None else 'טווח העמודים גדול מדי.')
        pages.update(range(first, last + 1))
    return sorted(pages)


def normalize_pages(pages, total):
    if not pages or any(not isinstance(p, int) or isinstance(p, bool) or p < 1 or p > total for p in pages):
        raise PageRangeError('נבחרו עמודים שאינם קיימים בספר.')
    return sorted(set(pages))
