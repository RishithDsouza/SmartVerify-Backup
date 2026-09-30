from app.db.database import SessionLocal
from app.models.user import User
from app.core.security import create_access_token
import requests

db = SessionLocal()
admin = db.query(User).filter(User.role == 'admin').first() or db.query(User).first()
role_val = admin.role.value if hasattr(admin.role, 'value') else str(admin.role)
token = create_access_token({'sub': str(admin.id), 'role': role_val})
db.close()

headers = {'Authorization': f'Bearer {token}'}
for doc_id in [308, 309]:
    res = requests.post(f'http://127.0.0.1:8000/documents/process/{doc_id}', headers=headers)
    print(f'Doc {doc_id} HTTP status: {res.status_code}')
    try:
        data = res.json()
        print(f"Doc {doc_id} processed: {data.get('processed')}")
        print(f"Doc {doc_id} text len: {len(data.get('extracted_text') or '')}")
        print(f"Doc {doc_id} structured_data: {str(data.get('structured_data'))[:200]}")
    except Exception as e:
        print(f"Doc {doc_id} json error: {res.text}")
