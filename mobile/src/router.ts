import { createRouter, createWebHistory } from 'vue-router'
import HomePage from './pages/HomePage.vue'
import ChatPage from './pages/ChatPage.vue'
import PlanPage from './pages/PlanPage.vue'
import CheckInPage from './pages/CheckInPage.vue'
import AccountLinkPage from './pages/AccountLinkPage.vue'
import GoalsPage from './pages/GoalsPage.vue'
import TrendsPage from './pages/TrendsPage.vue'
import PrivacyPage from './pages/PrivacyPage.vue'
import ProfilePage from './pages/ProfilePage.vue'
import ProfileEditPage from './pages/ProfileEditPage.vue'
import SettingsPage from './pages/SettingsPage.vue'
import RecordsPage from './pages/RecordsPage.vue'
import DietRecordsPage from './pages/DietRecordsPage.vue'
import ExerciseRecordsPage from './pages/ExerciseRecordsPage.vue'
import InsightsPage from './pages/InsightsPage.vue'
import ReportPage from './pages/ReportPage.vue'
import WorkoutPage from './pages/WorkoutPage.vue'
import ExerciseDetailPage from './pages/ExerciseDetailPage.vue'
import StatePage from './pages/StatePage.vue'
import EvaluationPage from './pages/EvaluationPage.vue'
import CapabilitiesPage from './pages/CapabilitiesPage.vue'
import AISettingsPage from './pages/AISettingsPage.vue'
import PolicyOverviewPage from './pages/PolicyOverviewPage.vue'
import PolicyProtocolPage from './pages/PolicyProtocolPage.vue'
import PolicyEpisodePage from './pages/PolicyEpisodePage.vue'
import PolicyReviewPage from './pages/PolicyReviewPage.vue'
import PolicyHistoryPage from './pages/PolicyHistoryPage.vue'
import ScanPage from './pages/ScanPage.vue'
import MediaPage from './pages/MediaPage.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/home' },
    { path: '/home', name: 'home', component: HomePage },
    { path: '/chat', name: 'chat', component: ChatPage },
    { path: '/plan', name: 'plan', component: PlanPage },
    { path: '/checkin', name: 'checkin', component: CheckInPage },
    { path: '/account-link', name: 'account-link', component: AccountLinkPage },
    { path: '/goals', name: 'goals', component: GoalsPage },
    { path: '/trends', name: 'trends', component: TrendsPage },
    { path: '/settings/privacy', name: 'privacy', component: PrivacyPage },
    { path: '/profile', name: 'profile', component: ProfilePage },
    { path: '/profile/edit', name: 'profile-edit', component: ProfileEditPage },
    { path: '/settings', name: 'settings', component: SettingsPage },
    { path: '/records', name: 'records', component: RecordsPage },
    { path: '/records/diet', name: 'diet-records', component: DietRecordsPage },
    { path: '/records/exercise', name: 'exercise-records', component: ExerciseRecordsPage },
    { path: '/insights', name: 'insights', component: InsightsPage },
    { path: '/report', name: 'report', component: ReportPage },
    { path: '/workout', name: 'workout', component: WorkoutPage },
    { path: '/exercise-detail', name: 'exercise-detail', component: ExerciseDetailPage },
    { path: '/state', name: 'state', component: StatePage },
    { path: '/evaluation', name: 'evaluation', component: EvaluationPage },
    { path: '/settings/capabilities', name: 'capabilities', component: CapabilitiesPage },
    { path: '/settings/ai', name: 'ai-settings', component: AISettingsPage },
    { path: '/policy', name: 'policy', component: PolicyOverviewPage },
    { path: '/policy/protocol', name: 'policy-protocol', component: PolicyProtocolPage },
    { path: '/policy/episode', name: 'policy-episode', component: PolicyEpisodePage },
    { path: '/policy/review', name: 'policy-review', component: PolicyReviewPage },
    { path: '/policy/history', name: 'policy-history', component: PolicyHistoryPage },
    { path: '/scan', name: 'scan', component: ScanPage },
    { path: '/media', name: 'media', component: MediaPage },
  ],
})
