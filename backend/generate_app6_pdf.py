import os
import sys
import io
import pypdf

# Set backend in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db.database import SessionLocal
from app.report_templates import get_template
from app.services.report_context_builder import build_report_context
from app.services.baseline_report_renderer import render_baseline_report
from app.services.evidence_mapper import map_evidence
from app.services.report_composer import compose_report
from app.services.report_validator import validate_report
from app.services.report_review_editor import create_review_state
from app.schemas.pdf_report import PdfGenerationRequest
from app.services.pdf_generator import generate_pdf_report

def main():
    app_id = 6
    db = SessionLocal()
    try:
        print(f"Building report context for Application {app_id}...")
        context = build_report_context(app_id, db)
        template = get_template(context.metadata.template_key, context.metadata.template_version)
        print(f"Template loaded: {template.template_key} {template.template_version}, Particulars: {len(template.particulars)}")
        
        baseline = render_baseline_report(context, template.template_key, template.template_version)
        print(f"Baseline rendered, particulars: {len(baseline.particulars)}")
        
        evidence_map = map_evidence(context, template)
        print(f"Evidence mapped, total evidence: {evidence_map.total_evidence_count}")
        
        composed = compose_report(context, template, evidence_map)
        print(f"Report composed, total particulars: {len(composed.particular_outputs)}")
        
        validated = validate_report(context, template, evidence_map, composed)
        print(f"Report validated: {validated.overall_status}, blocking: {len(validated.blocking_issues)}, warnings: {len(validated.warnings)}")
        
        state = create_review_state(baseline, evidence_map, composed, validated, template)
        print(f"Review state created: can_export={state.can_export_pdf}, reviews={len(state.particular_reviews)}")
        
        req = PdfGenerationRequest(
            review_state=state,
            template=template,
            baseline_report=baseline,
            evidence_map=evidence_map,
            validated_report=validated,
            generation_timestamp="2026-09-26T12:00:00Z",
            generated_by="Officer Rajesh Sharma",
        )
        
        pdf_res = generate_pdf_report(req)
        output_pdf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated_report_app6.pdf")
        with open(output_pdf_path, "wb") as f:
            f.write(pdf_res.pdf_bytes)
        print(f"PDF written to {output_pdf_path} ({len(pdf_res.pdf_bytes)} bytes, {pdf_res.page_count} pages)")
        
        # Verify content
        reader = pypdf.PdfReader(io.BytesIO(pdf_res.pdf_bytes))
        full_text = ""
        for i, page in enumerate(reader.pages):
            txt = page.extract_text()
            full_text += f"\n--- PAGE {i+1} ---\n" + txt
            
        with open("pdf_text_dump.txt", "w", encoding="utf-8") as f:
            f.write(full_text)
            
        print("Verification checks on generated PDF:")
        print("1. 'SECTION 8' present?:", "SECTION 8" in full_text)
        print("2. 'General Information / Opinion' present?:", "General Information / Opinion" in full_text)
        print("3. 'ANNEXURE: MAPPED VERIFICATION' present?:", "ANNEXURE: MAPPED VERIFICATION" in full_text)
        print("4. 'SUPPORTING EVIDENCE' present?:", "SUPPORTING EVIDENCE" in full_text)
        print("5. 'REPORT REVIEW STATUS & STATUTORY AUDIT CERTIFICATION' present?:", "REPORT REVIEW STATUS & STATUTORY AUDIT CERTIFICATION" in full_text)
        print("6. 'NA' present?:", "NA" in full_text)
        print("7. 'Guarantor: Arun Kumar' present?:", "Guarantor: Arun Kumar" in full_text)
        print("8. 'doc:9' present?:", "doc:9" in full_text)
        print("9. 'gov_shot:' present?:", "gov_shot:" in full_text)
        
        # Check order of sections
        sec7_idx = full_text.find("SECTION 7: VEHICLE / COLLATERAL VERIFICATION")
        supp_idx = full_text.find("SUPPORTING EVIDENCE")
        audit_idx = full_text.find("REPORT REVIEW STATUS & STATUTORY AUDIT CERTIFICATION")
        print(f"Section order: Sec 7 ({sec7_idx}) -> Supporting Evidence ({supp_idx}) -> Audit ({audit_idx})")
        assert sec7_idx != -1 and supp_idx != -1 and audit_idx != -1
        assert sec7_idx < supp_idx < audit_idx, "Incorrect section ordering!"
        print("Section ordering confirmed: Sec 1-7 -> SUPPORTING EVIDENCE -> AUDIT CERTIFICATION")

    finally:
        db.close()

if __name__ == "__main__":
    main()
