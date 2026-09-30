from app.db.database import SessionLocal
from app.models.document import Document
import json

db = SessionLocal()
docs = db.query(Document).filter(Document.document_type.in_(["salary_slip", "form_16"])).order_by(Document.id.desc()).limit(10).all()
print(f"Total matching docs: {len(docs)}")
for d in docs:
    print(f"ID: {d.id} | App: {d.application_id} | Type: {d.document_type} | Orig: {d.original_name} | Proc: {d.processed} | TextLen: {len(d.extracted_text or '')} | Path: {d.file_path}")
    print(f"Structured: {json.dumps(d.structured_data, indent=2)}")
    print("-" * 50)
db.close()
