// intent.js
//
// Lightweight client-side intent detection for the auto-Validate flow.
//
// When the user's question reads as "please validate / verify / fact-check
// this claim", we want to fire the existing /api/axioms/validate/ pipeline
// automatically once the response stream finishes — so the user sees the
// XBRL-grounded verdict in the same turn instead of having to click the
// Validate button.
//
// Scope is deliberately narrow: keyword + verb match, no NLP. False
// negatives are cheap (the manual Validate button is still there); false
// positives just send one extra POST that the backend handles gracefully
// when the registry is empty.

const VALIDATION_INTENT_RE =
    /\b(?:validate|verify|fact[\s-]?check|double[\s-]?check|sanity[\s-]?check|cross[\s-]?check|is\s+it\s+(?:true|right|correct|accurate)|is\s+(?:that|this)\s+(?:true|right|correct|accurate)|check\s+(?:this|that|the)\s+(?:claim|number|figure|stat|statistic|ratio|margin))\b/i;

function looksLikeValidationRequest(text) {
    if (typeof text !== 'string' || !text.trim()) return false;
    return VALIDATION_INTENT_RE.test(text);
}

export { looksLikeValidationRequest };
