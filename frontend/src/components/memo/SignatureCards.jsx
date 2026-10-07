import React from 'react';
import { Stamp } from 'lucide-react';

import { columnsFor, DEPARTMENT_FITS_UP_TO } from './memoLabels';
import { Check } from 'lucide-react';

const fmt = (iso) => (iso ? new Date(iso).toLocaleString(undefined, {
  year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
}) : '—');

// Tone per block state. The stamp WORD carries the meaning; colour only
// reinforces it, so the section reads correctly in greyscale, on a monochrome
// printer and to a colour-blind reader.
const TONES = {
  done: 'done', active: 'pending', pending: 'pending', rejected: 'rejected', skipped: 'skipped',
};

/**
 * The approval certification blocks: Created By → Recommended By → Supported By →
 * Approved By, one compact block each.
 *
 * Phase 26 replaced web-style profile cards (avatar circles, rounded corners) with
 * the ruled certification blocks used on bank memoranda. Phase 30 compacted them:
 * the whole section now sits in a single horizontal row whenever the chain is four
 * steps or fewer, key labels are gone (the caption bar already says what the block
 * certifies), and the electronic-authorisation wording appears once beneath the
 * section instead of under every signature.
 *
 * The blocks come from the server (`memo.signatures`, built by
 * workflow.signature_blocks) rather than being assembled here from the matrix.
 * That is deliberate: the PDF renders the same list, and when the screen and the
 * paper each derived the approval section independently they drifted — the PDF
 * ended up with no "Created By" block at all.
 *
 * @param {{ blocks:Array<Object> }} props
 */
const SignatureCards = ({ blocks = [] }) => {
  if (!blocks.length) return null;

  const columns = columnsFor(blocks.length);
  const showDepartment = columns <= DEPARTMENT_FITS_UP_TO;
  const anyVerified = blocks.some((block) => block.verified);

  return (
    <div className="memo-panel">
      <h3 className="memo-panel-title">
        <Stamp size={15} aria-hidden="true" /> Official Approvals
      </h3>
      <div className="memo-cert-grid" style={{ '--cert-cols': columns }}>
        {blocks.map((block, index) => {
          const tone = TONES[block.state] || 'pending';
          return (
            <article key={`${block.key}-${index}`} className={`memo-cert is-${tone}`}>
              <header className="memo-cert-caption">{block.heading}</header>

              {/* The stamp band. `stamp` is role-specific — a signed recommender
                  reads RECOMMENDED, not APPROVED — which is why it is a separate
                  field from status_label rather than a re-use of it. */}
              <div className="memo-cert-stamp-wrap">
                <span className="memo-cert-stamp">{block.stamp || block.status_label}</span>
              </div>

              <div className="memo-cert-rows">
                <div className="memo-cert-name">{block.name}</div>
                <div className="memo-cert-line">{block.designation || '—'}</div>
                {showDepartment && (
                  <div className="memo-cert-line">{block.department || '—'}</div>
                )}
                <div className="memo-cert-line is-date">{fmt(block.at)}</div>
              </div>

              {block.remarks && <p className="memo-cert-remarks">“{block.remarks}”</p>}

              {/* The verification mark means "this person actually signed off", so it
                  never appears on a block that is merely queued. */}
              {block.verified ? (
                <p className="memo-cert-verify">
                  <Check size={14} aria-hidden="true" /> Verified
                  {block.verification_id && (
                    <span className="memo-cert-vid">{block.verification_id}</span>
                  )}
                </p>
              ) : (
                <p className="memo-cert-unverified">Not yet signed</p>
              )}
            </article>
          );
        })}
      </div>
      {anyVerified && (
        <p className="memo-cert-footnote">
          {/* Names the WORD, not the symbol (Phase UI-PRODUCTION-V1). The mark
              is now an icon, and an icon has no reading — a screen reader
              announced "Blocks marked, were authorised", with a gap where the
              glyph had been. "Blocks marked Verified" survives the change and
              was always the clearer sentence. */}
          Blocks marked <strong>Verified</strong> were authorised electronically; no wet
          signature is required.
          Quote the verification ID to confirm a signature against the system record.
        </p>
      )}
    </div>
  );
};

export default SignatureCards;
