// --------------------------------------------------------------------------
// Auth: Google OAuth login, our own JWT session (see docs/auth_plan.md)
// --------------------------------------------------------------------------

export type Role = 'end_user' | 'advertiser' | 'moderator'

export interface Account {
  id: number
  email: string
  display_name: string
  avatar_url: string | null
  role: Role
  onboarding_completed: boolean
}

// --------------------------------------------------------------------------
// Serving: ads, feed, reactions
// --------------------------------------------------------------------------

export interface Ad {
  ad_id: string
  headline: string
  description: string
  category: string
  price: number | null
}

export interface AdCandidate extends Ad {
  similarity_score: number
}

export interface FeedItem extends AdCandidate {
  relevance_score: number
  justification: string
}

export interface BatchRecommendationResponse {
  user_id: string
  items: FeedItem[]
}

export type Reaction = 'like' | 'dislike' | 'interested'

export type ReportCategory = 'misleading' | 'offensive' | 'irrelevant' | 'spam' | 'other'

// --------------------------------------------------------------------------
// Onboarding chat
// --------------------------------------------------------------------------

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface OnboardingCheckpointResponse {
  show_candidates: boolean
  ready_to_finish: boolean
  interest_summary: string
  candidates: AdCandidate[]
}

// --------------------------------------------------------------------------
// Performance dashboard
// --------------------------------------------------------------------------

export interface PerformanceTotals {
  impressions: number
  likes: number
  dislikes: number
  conversions: number
  reports: number
  ctr: number
  engagement_rate: number
  dislike_rate: number
  total_spend: number
  avg_cpa: number | null
}

export interface PerformanceTrendPoint {
  date: string
  impressions: number
  conversions: number
  ctr: number
}

export interface CampaignPerformance {
  campaign_id: number
  headline: string
  status: string
  impressions: number
  likes: number
  dislikes: number
  conversions: number
  reports: number
  ctr: number
  spend: number
}

export interface PerformanceResponse {
  totals: PerformanceTotals
  trend: PerformanceTrendPoint[]
  campaigns: CampaignPerformance[]
}

// --------------------------------------------------------------------------
// Campaigns: submission + moderation
// --------------------------------------------------------------------------

export interface CampaignCreateRequest {
  advertiser_name: string
  headline: string
  description: string
  category: string
  objective: string
  budget_total: number
  start_date: string
  end_date: string
  excluded_categories?: string[]
}

export interface CampaignResponse {
  id: number
  advertiser_id: number
  headline: string
  description: string
  category: string
  objective: string
  budget_total: number
  budget_spent: number
  start_date: string
  end_date: string
  excluded_categories: string[]
  status: string
  review_reason: string | null
  research_notes: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  created_at: string
}

export interface ModerationRequest {
  outcome: 'approved' | 'rejected'
  reason: string
  reviewed_by: string
}
