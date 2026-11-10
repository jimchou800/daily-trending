/** Canonical report states accepted and emitted by the publication authority. */
export const REPORT_STATUS = {
  PREVIEW: "sample_preview",
  PUBLISHED: "published",
} as const;

/** Candidate outcomes in the complete ordered selection proof. */
export const SELECTION_OUTCOME = {
  SELECTED: "selected",
  EXCLUDED: "excluded",
  ANALYSIS_FAILED: "analysis_failed",
} as const;

/** Explicit evidence-insufficient classification is distinct from a negative verdict. */
export const UNKNOWN_RELEVANCE = "unknown_relevance";
/** Public wording when no causal source supports a popularity explanation. */
export const UNKNOWN_REASON = "暂未找到明确原因";

/** A settled charge may be provider-reported or the full reserved unknown bound. */
export const USAGE_STATUS = {
  REPORTED: "reported",
  UNAVAILABLE: "unavailable",
} as const;

/** Exceptions permitting a repeat within the 168-hour inclusion window. */
export const REPEAT_BASIS = {
  STARS_DOUBLED: "stars_doubled",
  FORMAL_RELEASE: "new_formal_release",
} as const;
