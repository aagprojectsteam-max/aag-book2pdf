import json
import os
from pathlib import Path


STATUS_LABELS = {
    'TURBOSUN_DATASET_INCOMPLETE': 'אוסף TurboSun אינו שלם או שחסר רכיב קריאת קטלוג',
    'TURBOSUN_CATALOGUE_MISSING': 'קטלוג TurboSun חסר',
    'TURBOSUN_INDEX_MISSING': 'אינדקס העמודים חסר או אינו עקבי',
    'TURBOSUN_CONTAINER_MISSING': 'קובץ סריקות מקושר חסר',
    'TURBOSUN_PAGE_DECODE_FAILED': 'פענוח עמוד TurboSun נכשל',
    'TURBOSUN_UNSUPPORTED_VARIANT': 'גרסת TurboSun זו אינה נתמכת',
    'TURBOSUN_LIMIT_EXCEEDED': 'אוסף TurboSun חורג ממגבלות המשאבים',

    'DEPENDENCY_MISSING': 'רכיב תוכנה נדרש אינו מותקן; יש להריץ בדיקת סביבה',
    'RUNTIME_LOAD_FAILED': 'רכיב תוכנה לא נטען; יש לבדוק את ההתקנה',
    'LIMIT_EXCEEDED': 'הפעולה חרגה ממגבלות המשאבים',
    'DOCUMENT_INVALID': 'המסמך שפוענח אינו עובר אימות',
    'STALE_CACHE': 'המטמון נוצר בגרסת מפענח ישנה',

    "PASS_DECODED": "הושלם — מעטפת פוענחה ואומתה",
    "NEW_VARIANT_DETECTED": "זוהה מבנה חדש — דוח ניתוח זמין",
    "PREVIEW_AVAILABLE_WITH_ASSUMPTIONS": "זמינה תצוגה משוחזרת עם הנחות מפורשות",
    "DECODER_REQUIRED": "נדרש מפענח מאומת למעטפת; שיטת הקידוד אינה ידועה",
    "KEY_REQUIRED": "הפורמט מציין הצפנה — נדרש מפתח מורשה",
    "UNSUPPORTED_AFTER_ANALYSIS": "הניתוח הושלם ללא מסלול שחזור בטוח",
    "PASS_EXACT": "הושלם — תוכן המקור נשמר במדויק", "PASS_REPAIRED": "הושלם — מבנה שוחזר עם מגבלות מתועדות",
    "RECONSTRUCTED_PREVIEW": "תצוגה משוחזרת — חסר מידע שהושלם לפי הנחת משתמש",
    "SKIPPED_EXISTING_VALID": "דולג — כבר אומת",
    "FAIL_INVALID_SOURCE": "נכשל — קובץ מקור לא תקין", "FAIL_REPAIR": "נכשל — לא ניתן להשלים המרה",
    "FAIL_VALIDATION": "נכשל — בדיקת PDF לא עברה",
    "UNSUPPORTED_OR_AMBIGUOUS": "לא נתמך — מבנה קובץ לא חד־משמעי", "CANCELLED": "בוטל",
}


class Report:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.json = path.open("x", encoding="utf-8")
        try:
            self.log = path.with_suffix(path.suffix + ".log").open("x", encoding="utf-8")
        except Exception:
            self.json.close()
            raise

    def write(self, result):
        self.json.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
        # repr prevents newline-containing filenames from impersonating log records.
        self.log.write(f"{result.timestamp} {result.status} {result.source_path!r}: {result.error or STATUS_LABELS.get(result.status,result.status)}\n")
        if result.warning:
            self.log.write(f'  Warning: {result.warning}\n')
        if result.assumptions:
            self.log.write(f'  Assumptions: {result.assumptions!r}\n')
        if result.affected_pages:
            self.log.write(f'  Affected pages: {result.affected_pages!r}\n')
        for item in result.reconstructed_objects:
            if isinstance(item, dict):
                self.log.write(f"  Reconstructed object {item.get('object', 'document structure')}: {item.get('replacement')!r}; {item.get('uncertainty', '')}\n")
            else:
                # Earlier generic provenance used descriptive strings. Retain
                # their content when exporting or reopening those PDFs.
                self.log.write(f"  Reconstructed structure: {item!r}; see recovery provenance and assumptions\n")
        for stream in (self.json, self.log):
            stream.flush()
            os.fsync(stream.fileno())

    def finish(self, summary):
        self.json.write(json.dumps({"type": "summary", **summary}, ensure_ascii=False) + "\n")
        self.log.write(" ".join(f"{key}={value}" for key, value in summary.items()) + "\n")
        for stream in (self.json, self.log):
            stream.flush()
            os.fsync(stream.fileno())

    def close(self):
        self.json.close()
        self.log.close()
