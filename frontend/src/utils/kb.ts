import type { Role } from '@/types/kb'

export const canAccessSettings = (role?: Role | string | null) => role === 'admin'
export const canAccessMeetingWorkspace = (role?: Role | string | null) => role !== 'admin'
export const canManageKb = (role?: Role | string | null) => canAccessSettings(role)
export const canEditKb = (role?: Role) => canManageKb(role) || role === 'editor'
export const canIncludeDrafts = canEditKb

export function roleLabel(role?: Role | string | null): string {
  switch (role) {
    case 'owner':
      return '纪要编辑员'
    case 'admin':
      return 'IT管理员'
    case 'editor':
      return '纪要编辑员'
    case 'reviewer':
      return '纪要审核员'
    case 'viewer':
      return '查看员'
    default:
      return '未知角色'
  }
}
