import { createRouter, createWebHistory } from 'vue-router'
import AppLayout from '@/layouts/AppLayout.vue'
import { pinia } from '@/stores'
import { useAuthStore } from '@/stores/auth'

const LAST_MEETING_IMPORT_ID = 'latest_meeting_import_id'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/auth', name: 'auth', component: () => import('@/views/AuthView.vue'), meta: { public: true } },
    {
      path: '/',
      component: AppLayout,
      children: [
        { path: '', redirect: '/meetings/import' },
        { path: 'meetings/import', name: 'meeting-import', component: () => import('@/views/meetings/MeetingImportView.vue') },
        { path: 'meetings/import/:importId/review', name: 'meeting-import-review', component: () => import('@/views/meetings/MeetingImportReviewPlaceholderView.vue') },
        { path: 'meeting-review', name: 'meeting-review', component: () => import('@/views/meetings/MeetingVerificationListView.vue') },
        { path: 'meeting-review/:meetingId', name: 'meeting-review-detail', component: () => import('@/views/meetings/MeetingVerificationDetailView.vue') },
        { path: 'meeting-review/:meetingId/analysis', name: 'meeting-analysis', component: () => import('@/views/meetings/MeetingAnalysisView.vue') },
        {
          path: 'meetings/minutes/edit',
          name: 'meeting-minutes-edit',
          redirect: () => {
            const importId = window.localStorage.getItem(LAST_MEETING_IMPORT_ID)
            return importId
              ? { name: 'meeting-import-review', params: { importId } }
              : { name: 'meeting-import' }
          },
        },
        { path: 'knowledge-bases', name: 'knowledge-bases', component: () => import('@/views/kb/KnowledgeBaseListView.vue') },
        { path: 'knowledge-bases/:id', name: 'knowledge-base-detail', component: () => import('@/views/kb/KnowledgeBaseDetailView.vue') },
        { path: 'knowledge-bases/:id/documents/:documentId', name: 'kb-document-detail', component: () => import('@/views/kb/DocumentDetailView.vue') },
        { path: 'benchmarks', name: 'benchmarks', component: () => import('@/views/admin/BenchmarksView.vue') },
        // Keep the legacy URLs as compatibility aliases, but make the review console
        // the only meeting-management entry point.
        { path: 'meetings', name: 'meetings', redirect: { name: 'meeting-review' } },
        { path: 'meetings/new', name: 'meeting-create', redirect: { name: 'meeting-review' } },
        {
          path: 'meetings/:id',
          name: 'meeting-detail',
          redirect: (to) => ({ name: 'meeting-review-detail', params: { meetingId: String(to.params.id) } }),
        },
        {
          path: 'meetings/:id/edit',
          name: 'meeting-edit',
          redirect: (to) => ({ name: 'meeting-review-detail', params: { meetingId: String(to.params.id) } }),
        },
        { path: 'analysis', name: 'analysis-placeholder', component: () => import('@/views/AnalysisPlaceholderView.vue') },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/meetings/import' },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore(pinia)
  await auth.initialize()
  if (!to.meta.public && !auth.authenticated) return { path: '/auth', query: { redirect: to.fullPath } }
  if (to.path === '/auth' && auth.authenticated) return '/meetings/import'
  return true
})

export default router
