// Decorate Validate claim spans pre-wrapped by the backend.
//
// The backend post-processor wraps every non-delimited occurrence of a
// reported claim value in <span data-claim-id="...">value</span>. This
// helper joins the Validate status back to all matching spans by id and
// applies one of three status classes to each. Visual-only: per the
// spec (D3), no click handler, no custom tooltip, no focus ring.

const STATUS_CLASS = {
    VERIFIED: 'claim-mark-verified',
    FAILED: 'claim-mark-failed',
    NOT_APPLICABLE: 'claim-mark-na',
};

export function decorateClaimMarks(bubble, claims) {
    if (!bubble || !Array.isArray(claims)) return;
    const missing = [];
    for (const c of claims) {
        if (!c || !c.claim_id) continue;
        const cls = STATUS_CLASS[c.status];
        if (!cls) continue;  // SKIPPED or unknown status: no decoration
        const spans = bubble.querySelectorAll(
            '[data-claim-id="' + CSS.escape(c.claim_id) + '"]'
        );
        if (!spans.length) { missing.push(c.claim_id); continue; }
        spans.forEach((span) => span.classList.add(cls));
    }
    if (missing.length) {
        console.warn('[Validate] no spans for claim_ids', missing);
    }
}
