import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft, CheckCircle2, AlertTriangle, XCircle, Save,
  RefreshCw, FileText, ShieldAlert, Eye, Download, User,
  Users, Lock, CheckSquare, Square, Info, Sparkles, Database
} from 'lucide-react';
import toast from 'react-hot-toast';
import {
  getReportReview,
  editParticularReview,
  toggleEvidenceSelection,
  revalidateReportReview,
  generateReportPdf
} from '../services/api';
import LoadingSpinner from '../components/ui/LoadingSpinner';

export default function ReportReviewPage() {
  const { appId } = useParams();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [reviewState, setReviewState] = useState(null);
  const [selectedPid, setSelectedPid] = useState('P1');
  const [editedTexts, setEditedTexts] = useState({});
  const [savingPid, setSavingPid] = useState(null);
  const [revalidating, setRevalidating] = useState(false);
  const [generatingPdf, setGeneratingPdf] = useState(false);
  const [filterSection, setFilterSection] = useState('all');

  useEffect(() => {
    loadReview();
  }, [appId]);

  const loadReview = async () => {
    try {
      setLoading(true);
      const res = await getReportReview(appId);
      setReviewState(res.data);
      // Initialize editedTexts with current_text
      const texts = {};
      Object.entries(res.data.particular_reviews || {}).forEach(([pid, prev]) => {
        texts[pid] = prev.current_text || '';
      });
      setEditedTexts(texts);
    } catch (err) {
      toast.error('Failed to load report review session: ' + (err.response?.data?.detail || err.message));
    } finally {
      setLoading(false);
    }
  };

  const handleTextChange = (pid, text) => {
    setEditedTexts(prev => ({ ...prev, [pid]: text }));
  };

  const handleSaveParticular = async (pid) => {
    const newText = editedTexts[pid];
    try {
      setSavingPid(pid);
      const res = await editParticularReview(appId, {
        particular_id: pid,
        new_text: newText,
        edited_by: 'Reviewing Officer',
        reason: 'Officer manual review adjustment'
      });
      setReviewState(res.data);
      toast.success(`Particular ${pid} updated (Rev #${res.data.revision})`);
    } catch (err) {
      toast.error(`Failed to save edit: ` + (err.response?.data?.detail || err.message));
    } finally {
      setSavingPid(null);
    }
  };

  const handleRevalidate = async () => {
    try {
      setRevalidating(true);
      const res = await revalidateReportReview(appId);
      setReviewState(res.data);
      if (res.data.validation_status === 'INVALID') {
        toast.error(`Revalidation failed: ${res.data.blocking_issues.length} blocking issue(s) detected.`);
      } else if (res.data.validation_status === 'VALID_WITH_WARNINGS') {
        toast('Revalidated with warnings.', { icon: '⚠️' });
      } else {
        toast.success('Report successfully revalidated and compliant.');
      }
    } catch (err) {
      toast.error('Revalidation failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setRevalidating(false);
    }
  };

  const handleToggleEvidence = async (pid, evidenceId, currentIncluded) => {
    try {
      const res = await toggleEvidenceSelection(appId, {
        particular_id: pid,
        evidence_id: evidenceId,
        included: !currentIncluded
      });
      setReviewState(res.data);
      toast.success(`Evidence ${!currentIncluded ? 'included' : 'excluded'} for ${pid}`);
    } catch (err) {
      toast.error('Failed to toggle evidence: ' + (err.response?.data?.detail || err.message));
    }
  };

  const handleGeneratePdf = async (e) => {
    if (e && e.preventDefault) e.preventDefault();
    if (!reviewState?.can_export_pdf || generatingPdf) return;

    try {
      setGeneratingPdf(true);
      const res = await generateReportPdf(appId);

      // Create Blob URL and trigger download without navigating away
      const blob = new Blob([res.data], { type: 'application/pdf' });
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `SmartVerify_Report_${appId}.pdf`);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);

      toast.success('PDF generated successfully.');
    } catch (err) {
      let errorMsg = 'PDF generation failed.';
      const status = err.response?.status;

      if (err.response?.data instanceof Blob) {
        try {
          const text = await err.response.data.text();
          const json = JSON.parse(text);
          if (json.detail) {
            if (typeof json.detail === 'string') {
              errorMsg = json.detail;
            } else if (json.detail.error) {
              const reasons = json.detail.gate_reasons?.length
                ? `: ${json.detail.gate_reasons.join(', ')}`
                : '';
              errorMsg = `${json.detail.error}${reasons}`;
            } else {
              errorMsg = JSON.stringify(json.detail);
            }
          }
        } catch (parseErr) {
          // fallback to default
        }
      } else if (err.response?.data?.detail) {
        errorMsg = typeof err.response.data.detail === 'string'
          ? err.response.data.detail
          : JSON.stringify(err.response.data.detail);
      }

      if (status === 422) {
        toast.error(errorMsg || 'Export validation failed.');
      } else {
        toast.error('PDF generation failed.');
      }
    } finally {
      setGeneratingPdf(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <LoadingSpinner />
      </div>
    );
  }

  if (!reviewState) {
    return (
      <div className="p-8 text-center">
        <p className="text-slate-500">Report review session could not be loaded.</p>
        <button
          onClick={() => navigate(`/report/${appId}`)}
          className="mt-4 px-4 py-2 bg-slate-800 text-white rounded-lg text-sm"
        >
          Return to Report View
        </button>
      </div>
    );
  }

  const selectedReview = reviewState.particular_reviews?.[selectedPid] || null;

  // Group particulars by section
  const sections = {};
  Object.values(reviewState.particular_reviews || {}).forEach(p => {
    if (!sections[p.section_id]) {
      sections[p.section_id] = { id: p.section_id, title: p.section_title, items: [] };
    }
    sections[p.section_id].items.push(p);
  });

  const getValidationBadge = (status) => {
    if (status === 'VALID') {
      return (
        <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 border border-emerald-300">
          <CheckCircle2 className="w-3.5 h-3.5" /> VALID
        </span>
      );
    }
    if (status === 'VALID_WITH_WARNINGS') {
      return (
        <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300">
          <AlertTriangle className="w-3.5 h-3.5" /> VALID WITH WARNINGS
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-red-100 text-red-800 border border-red-300">
        <XCircle className="w-3.5 h-3.5" /> INVALID
      </span>
    );
  };

  const getPartyBadge = (party) => {
    if (party === 'APPLICANT') {
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold bg-blue-100 text-blue-800">
          <User className="w-3 h-3" /> Applicant
        </span>
      );
    }
    if (party === 'GUARANTOR') {
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold bg-purple-100 text-purple-800">
          <Users className="w-3 h-3" /> Guarantor
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold bg-teal-100 text-teal-800">
        Shared
      </span>
    );
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 space-y-6">
      {/* ── Top Header ────────────────────────────────────────────── */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <button
            onClick={() => navigate(`/report/${appId}`)}
            className="inline-flex items-center gap-2 text-xs font-medium text-slate-500 hover:text-slate-800 mb-2"
          >
            <ArrowLeft className="w-4 h-4" /> Back to Report View
          </button>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold text-slate-900">
              SmartVerify Report Review & Preview Editor
            </h1>
            <span className="px-2.5 py-0.5 text-xs font-semibold bg-slate-100 text-slate-700 rounded-md border border-slate-300">
              Revision #{reviewState.revision}
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-1">
            Application: <span className="font-semibold text-slate-700">APP-{String(appId).padStart(6, '0')}</span> • 
            Template: <span className="font-semibold text-slate-700">{reviewState.template_key} ({reviewState.template_version})</span> • 
            Hash: <span className="font-mono text-slate-600">{reviewState.input_hash.substring(0, 10)}...</span>
          </p>
        </div>

        {/* Status and Action Buttons */}
        <div className="flex flex-wrap items-center gap-3">
          {getValidationBadge(reviewState.validation_status)}

          <button
            onClick={handleRevalidate}
            disabled={revalidating}
            className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-300 hover:bg-slate-200 transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${revalidating ? 'animate-spin' : ''}`} />
            Revalidate
          </button>

          {/* PDF Export Button (Strict Gate) */}
          <div className="relative group">
            <button
              type="button"
              disabled={!reviewState.can_export_pdf || generatingPdf}
              onClick={handleGeneratePdf}
              className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-semibold text-white transition-all ${
                reviewState.can_export_pdf && !generatingPdf
                  ? 'bg-primary-700 hover:bg-primary-800 shadow-sm cursor-pointer'
                  : 'bg-slate-400 opacity-60 cursor-not-allowed'
              }`}
            >
              {generatingPdf ? (
                <>
                  <RefreshCw className="w-4 h-4 animate-spin" />
                  Generating PDF...
                </>
              ) : (
                <>
                  <Download className="w-4 h-4" />
                  Generate PDF
                </>
              )}
            </button>
            {!reviewState.can_export_pdf && !generatingPdf && (
              <div className="absolute right-0 top-full mt-1.5 hidden group-hover:block z-20 w-64 p-2 bg-slate-900 text-white text-[11px] rounded shadow-lg">
                PDF Export is disabled. Resolve all BLOCKING issues before PDF generation can be enabled.
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Blocking Issues Banner (if any) ────────────────────────── */}
      {reviewState.blocking_issues && reviewState.blocking_issues.length > 0 && (
        <div className="bg-red-50 border-l-4 border-red-500 p-4 rounded-r-xl">
          <div className="flex items-start gap-3">
            <ShieldAlert className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
            <div>
              <h3 className="text-sm font-bold text-red-800">
                {reviewState.blocking_issues.length} Blocking Validation Issue(s) Detected
              </h3>
              <p className="text-xs text-red-700 mt-1">
                The report cannot be exported to PDF until all blocking discrepancies are resolved.
              </p>
              <ul className="mt-2 space-y-1 text-xs text-red-800 list-disc list-inside">
                {reviewState.blocking_issues.map((issue, idx) => (
                  <li key={idx}>
                    <span className="font-semibold">[{issue.particular_id || 'GENERAL'}]</span> {issue.message}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}

      {/* ── Main Two-Column Layout ─────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Left Column: Particulars List & Editor (7 cols) */}
        <div className="lg:col-span-7 space-y-4">
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3 mb-4">
              <h2 className="text-sm font-bold text-slate-800 flex items-center gap-2">
                <FileText className="w-4 h-4 text-primary-700" />
                Report Particulars ({Object.keys(reviewState.particular_reviews || {}).length} Items)
              </h2>
              <div className="flex items-center gap-2 text-xs">
                <span className="text-slate-500">Filter:</span>
                <select
                  value={filterSection}
                  onChange={(e) => setFilterSection(e.target.value)}
                  className="px-2 py-1 bg-slate-50 border border-slate-200 rounded text-xs text-slate-700"
                >
                  <option value="all">All Sections</option>
                  {Object.values(sections).map(s => (
                    <option key={s.id} value={s.id}>{s.title}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Particulars Cards */}
            <div className="space-y-4">
              {Object.values(sections)
                .filter(s => filterSection === 'all' || s.id === filterSection)
                .map(section => (
                  <div key={section.id} className="space-y-3">
                    <div className="bg-slate-100 px-3 py-1.5 rounded-lg text-xs font-bold text-slate-700 uppercase tracking-wider">
                      {section.title}
                    </div>

                    {section.items.map(p => {
                      const isSelected = selectedPid === p.particular_id;
                      const hasBlocking = p.issues?.some(i => i.severity === 'BLOCKING');
                      const hasWarning = p.issues?.some(i => i.severity === 'WARNING');
                      const isDirty = editedTexts[p.particular_id] !== p.current_text;

                      return (
                        <div
                          key={p.particular_id}
                          className={`rounded-xl border p-4 transition-all ${
                            isSelected
                              ? 'border-primary-500 bg-primary-50/10 shadow-sm ring-1 ring-primary-400'
                              : 'border-slate-200 hover:border-slate-300 bg-white'
                          }`}
                        >
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <div className="flex items-center gap-2">
                              <span className="px-2 py-0.5 text-xs font-mono font-bold bg-slate-100 text-slate-800 rounded border border-slate-300">
                                {p.particular_id}
                              </span>
                              <h3 className="text-sm font-semibold text-slate-900">{p.title}</h3>
                            </div>
                            <div className="flex items-center gap-2">
                              {p.is_edited && (
                                <span className="px-2 py-0.5 text-[10px] font-semibold bg-purple-100 text-purple-800 rounded">
                                  Edited ({p.edit_count})
                                </span>
                              )}
                              {hasBlocking ? (
                                <span className="px-2 py-0.5 text-[10px] font-semibold bg-red-100 text-red-800 rounded">
                                  BLOCKING
                                </span>
                              ) : hasWarning ? (
                                <span className="px-2 py-0.5 text-[10px] font-semibold bg-amber-100 text-amber-800 rounded">
                                  WARNING
                                </span>
                              ) : (
                                <span className="px-2 py-0.5 text-[10px] font-semibold bg-emerald-100 text-emerald-800 rounded">
                                  VALID
                                </span>
                              )}
                            </div>
                          </div>

                          {/* Provenance Pills */}
                          <div className="flex flex-wrap items-center gap-2 mb-3 text-[11px] text-slate-500">
                            {p.ai_composed ? (
                              <span className="inline-flex items-center gap-1 text-purple-700 bg-purple-50 px-2 py-0.5 rounded border border-purple-200">
                                <Sparkles className="w-3 h-3" /> AI Composed ({p.model_id || 'Controlled'})
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 text-slate-700 bg-slate-50 px-2 py-0.5 rounded border border-slate-200">
                                <Database className="w-3 h-3" /> Deterministic Baseline
                              </span>
                            )}
                            <span>Facts: {p.fact_ids.length}</span>
                            <span>Evidence: {p.evidence_ids.length}</span>
                          </div>

                          {/* Issues Callout */}
                          {p.issues && p.issues.length > 0 && (
                            <div className="mb-3 space-y-1">
                              {p.issues.map((iss, iIdx) => (
                                <div
                                  key={iIdx}
                                  className={`text-xs p-2 rounded flex items-start gap-1.5 ${
                                    iss.severity === 'BLOCKING'
                                      ? 'bg-red-100 text-red-800 border border-red-300'
                                      : 'bg-amber-100 text-amber-800 border border-amber-300'
                                  }`}
                                >
                                  {iss.severity === 'BLOCKING' ? (
                                    <XCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                                  ) : (
                                    <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                                  )}
                                  <span>{iss.message}</span>
                                </div>
                              ))}
                            </div>
                          )}

                          {/* Verification Details Textarea */}
                          <div className="space-y-1.5">
                            <label className="text-xs font-semibold text-slate-700">
                              Verification Details:
                            </label>
                            <textarea
                              rows={3}
                              value={editedTexts[p.particular_id] ?? ''}
                              onChange={(e) => handleTextChange(p.particular_id, e.target.value)}
                              className="w-full text-xs p-2.5 rounded-lg border border-slate-300 focus:ring-2 focus:ring-primary-500 focus:border-primary-500 bg-white"
                              placeholder="Enter verification details narrative..."
                            />
                          </div>

                          {/* Card Footer Actions */}
                          <div className="flex items-center justify-between pt-3 border-t border-slate-100 mt-3">
                            <button
                              onClick={() => setSelectedPid(p.particular_id)}
                              className={`text-xs font-semibold flex items-center gap-1.5 px-2.5 py-1 rounded transition-colors ${
                                isSelected
                                  ? 'bg-primary-700 text-white'
                                  : 'text-primary-700 hover:bg-primary-50'
                              }`}
                            >
                              <Eye className="w-3.5 h-3.5" />
                              {isSelected ? 'Viewing Evidence' : 'Inspect Evidence'}
                            </button>

                            <button
                              disabled={savingPid === p.particular_id || !isDirty}
                              onClick={() => handleSaveParticular(p.particular_id)}
                              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-white transition-all ${
                                isDirty
                                  ? 'bg-emerald-600 hover:bg-emerald-700 cursor-pointer shadow-sm'
                                  : 'bg-slate-300 cursor-not-allowed opacity-60'
                              }`}
                            >
                              <Save className="w-3.5 h-3.5" />
                              {savingPid === p.particular_id ? 'Saving...' : 'Save Edit'}
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ))}
            </div>
          </div>
        </div>

        {/* Right Column: Evidence Panel & Comparison (5 cols) */}
        <div className="lg:col-span-5 space-y-4 sticky top-6">
          {selectedReview ? (
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-5 space-y-5">
              <div>
                <div className="flex items-center justify-between mb-1">
                  <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                    <CheckSquare className="w-4 h-4 text-primary-700" />
                    Evidence Panel
                  </h2>
                  <span className="font-mono text-xs font-bold px-2 py-0.5 bg-slate-100 text-slate-800 rounded">
                    {selectedReview.particular_id}
                  </span>
                </div>
                <p className="text-xs text-slate-500">
                  Mapped evidence items from Phase 5 for <span className="font-semibold text-slate-700">{selectedReview.title}</span>.
                </p>
              </div>

              {/* Mapped Evidence Items */}
              <div className="space-y-2.5">
                {selectedReview.evidence_items && selectedReview.evidence_items.length > 0 ? (
                  selectedReview.evidence_items.map((item) => (
                    <div
                      key={item.evidence_id}
                      className={`p-3 rounded-lg border text-xs transition-all ${
                        item.included
                          ? 'bg-slate-50 border-slate-200'
                          : 'bg-slate-100/60 border-dashed border-slate-300 opacity-60'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <label className="flex items-start gap-2.5 cursor-pointer flex-1">
                          <input
                            type="checkbox"
                            checked={item.included}
                            onChange={() => handleToggleEvidence(selectedReview.particular_id, item.evidence_id, item.included)}
                            className="mt-0.5 rounded border-slate-300 text-primary-600 focus:ring-primary-500"
                          />
                          <div>
                            <div className="font-semibold text-slate-800 flex items-center gap-1.5">
                              {item.source}
                              <span className="text-[10px] text-slate-400 font-mono">({item.evidence_id})</span>
                            </div>
                            {item.filename && (
                              <div className="text-[11px] text-slate-500 font-mono mt-0.5 truncate max-w-xs">
                                {item.filename}
                              </div>
                            )}
                          </div>
                        </label>
                        <div className="flex flex-col items-end gap-1 flex-shrink-0">
                          {getPartyBadge(item.party)}
                          <span className="text-[10px] font-semibold text-emerald-700 bg-emerald-50 px-1.5 py-0.5 rounded">
                            {item.verification_status}
                          </span>
                        </div>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="p-4 rounded-lg bg-slate-50 border border-dashed border-slate-200 text-center text-xs text-slate-500">
                    No mapped evidence items for this Particular.
                  </div>
                )}
              </div>

              {/* Guardrails Callout */}
              <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg text-[11px] text-blue-800 space-y-1">
                <div className="font-bold flex items-center gap-1">
                  <Lock className="w-3.5 h-3.5" /> Provenance Protection Active
                </div>
                <p>
                  Evidence mappings are strictly read-only from Phase 5. Party ownership and physical files cannot be modified or reassigned.
                </p>
              </div>

              {/* Original AI vs Deterministic Baseline Reference */}
              <div className="border-t border-slate-200 pt-4 space-y-3">
                <h3 className="text-xs font-bold text-slate-700 uppercase tracking-wider">
                  Baseline & AI Reference
                </h3>

                {selectedReview.deterministic_text && (
                  <div className="space-y-1">
                    <div className="text-[11px] font-semibold text-slate-500 flex items-center gap-1">
                      <Database className="w-3 h-3" /> Phase 4 Deterministic Baseline:
                    </div>
                    <div className="text-xs p-2.5 bg-slate-50 border border-slate-200 rounded text-slate-700 leading-relaxed font-mono">
                      {selectedReview.deterministic_text}
                    </div>
                  </div>
                )}

                {selectedReview.original_ai_text && (
                  <div className="space-y-1">
                    <div className="text-[11px] font-semibold text-purple-700 flex items-center gap-1">
                      <Sparkles className="w-3 h-3" /> Phase 6 Original AI Composition:
                    </div>
                    <div className="text-xs p-2.5 bg-purple-50/50 border border-purple-200 rounded text-slate-700 leading-relaxed">
                      {selectedReview.original_ai_text}
                    </div>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-8 text-center text-xs text-slate-500">
              Select a Particular to inspect mapped evidence.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
