import type { KbDocument, KnowledgeItem, Role } from '@/types/kb'

export const canManageKb = (role?: Role) => role === 'owner' || role === 'admin'
export const canEditKb = (role?: Role) => canManageKb(role) || role === 'editor'
export const canIncludeDrafts = canEditKb

export function canPublishDocument(document: KbDocument, items: KnowledgeItem[]) {
  return (
    ['AWAITING_REVIEW', 'IN_REVIEW'].includes(document.status) &&
    document.vector_sync_status === 'SYNCED' &&
    !items.some(
      (item) =>
        item.document_id === document.id &&
        ['PENDING', 'NEEDS_CHANGES'].includes(item.review_status),
    )
  )
}
