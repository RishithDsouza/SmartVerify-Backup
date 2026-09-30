from app.db.database import SessionLocal
from app.models.document import Document
import json

db = SessionLocal()
for doc in db.query(Document).filter(Document.document_type.in_(['salary_slip', 'form_16'])).order_by(Document.id.desc()).all():
    print('=' * 60)
    print(f'ID: {doc.id}')
    print(f'Application ID: {doc.application_id}')
    print(f'Document Type: {doc.document_type}')
    print(f'Original Name: {doc.original_name}')
    print(f'File Path: {doc.file_path}')
    print(f'Processed Status: {doc.processed}')
    text = doc.extracted_text or ''
    print(f'Extracted Text Length: {len(text)}')
    print(f'Extracted Text Snippet: {repr(text[:150])}')
    try:
        sd = json.loads(doc.structured_data) if doc.structured_data else {}
        if isinstance(sd, str):
            sd = json.loads(sd)
        print('Structured Data Keys:', list(sd.keys()) if isinstance(sd, dict) else type(sd))
        print('Structured Data Snippet:', json.dumps(sd)[:200])
    except Exception as e:
        print('Structured Data raw:', (doc.structured_data or '')[:100], 'err:', e)
db.close()
