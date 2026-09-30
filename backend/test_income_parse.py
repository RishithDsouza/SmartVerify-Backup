from app.db.database import SessionLocal
from app.models.document import Document
from app.services.parsers.income_parser import parse_salary_slip, parse_form_16

db = SessionLocal()
for doc_id in [308, 309, 303, 304]:
    doc = db.query(Document).filter(Document.id == doc_id).first()
    print('='*50)
    print(f"Doc {doc_id} ({doc.document_type.value}): {doc.original_name}")
    if doc.document_type.value == 'salary_slip':
        parsed = parse_salary_slip(doc.extracted_text)
    else:
        parsed = parse_form_16(doc.extracted_text)
    for k, v in parsed.items():
        print(f"  {k}: {v['value']} (conf={v['confidence']})")
db.close()
