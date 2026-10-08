export async function motionCorrectionIdempotencyKey(
  action: 'confirm' | 'reanalyze',
  mediaId: number,
  parentRunId: number,
  correction: { canonical_id?: string; novel_label_zh?: string },
  cloudReviewMode: string,
) {
  const material = JSON.stringify({
    media_id: mediaId,
    parent_run_id: parentRunId,
    canonical_id: correction.canonical_id || '',
    novel_label_zh: correction.novel_label_zh || '',
    cloud_review_mode: cloudReviewMode,
  })
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(material))
  const fingerprint = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
  return `motion:${mediaId}:${parentRunId}:${action}:${fingerprint}`
}
