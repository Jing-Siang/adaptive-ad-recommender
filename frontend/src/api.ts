import type {
  BatchRecommendationResponse,
  CampaignCreateRequest,
  CampaignResponse,
  ChatMessage,
  ModerationRequest,
  OnboardingCheckpointResponse,
  PerformanceResponse,
  Reaction,
  ReportCategory,
  UserResponse,
} from './types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`${init?.method ?? 'GET'} ${path} failed: ${response.status} ${body}`)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return response.json()
}

// --------------------------------------------------------------------------
// Serving: ads, feed, reactions
// --------------------------------------------------------------------------

export function fetchRecommendationBatch(userId: string, batchSize = 10): Promise<BatchRecommendationResponse> {
  return request('/recommend/batch', {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, batch_size: batchSize }),
  })
}

export function logImpression(userId: string, adId: string): Promise<void> {
  return request('/events/impression', {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, ad_id: adId }),
  })
}

export function sendReaction(userId: string, adId: string, reaction: Reaction): Promise<void> {
  return request('/events/reaction', {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, ad_id: adId, reaction }),
  })
}

export function clearReaction(userId: string, adId: string): Promise<void> {
  return request('/events/reaction', {
    method: 'DELETE',
    body: JSON.stringify({ user_id: userId, ad_id: adId }),
  })
}

export function sendReport(userId: string, adId: string, category: ReportCategory, reason?: string): Promise<void> {
  return request('/events/report', {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, ad_id: adId, category, reason }),
  })
}

export function doNotShowAgain(userId: string, adId: string): Promise<void> {
  return request(`/users/${encodeURIComponent(userId)}/do-not-show`, {
    method: 'POST',
    body: JSON.stringify({ ad_id: adId }),
  })
}

// --------------------------------------------------------------------------
// Users / profiles
// --------------------------------------------------------------------------

export function createUser(userId: string, interestSummary: string): Promise<UserResponse> {
  return request('/users', {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, interest_summary: interestSummary }),
  })
}

export function getUser(userId: string): Promise<UserResponse> {
  return request(`/users/${encodeURIComponent(userId)}`)
}

// --------------------------------------------------------------------------
// Onboarding chat
// --------------------------------------------------------------------------

export function onboardingCheckpoint(userId: string, messages: ChatMessage[]): Promise<OnboardingCheckpointResponse> {
  return request('/onboarding/checkpoint', {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, messages }),
  })
}

/**
 * Streams the onboarding chat reply, calling onDelta with each text chunk as
 * it arrives. readyToFinish tells the model to wrap up and guide the user to
 * their feed instead of asking another question. No candidates param -- the
 * reply doesn't change based on whether candidates are shown this turn (see
 * OnboardingChatRequest's docstring on the backend for why).
 */
export async function streamOnboardingChat(
  messages: ChatMessage[],
  readyToFinish: boolean,
  onDelta: (chunk: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/onboarding/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, ready_to_finish: readyToFinish }),
    signal,
  })
  if (!response.ok || !response.body) {
    throw new Error(`onboarding/chat failed: ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    onDelta(decoder.decode(value, { stream: true }))
  }
}

// --------------------------------------------------------------------------
// Performance dashboard
// --------------------------------------------------------------------------

export function fetchPerformance(): Promise<PerformanceResponse> {
  return request('/performance')
}

// --------------------------------------------------------------------------
// Campaigns: submission + moderation
// --------------------------------------------------------------------------

export function createCampaign(payload: CampaignCreateRequest): Promise<CampaignResponse> {
  return request('/campaigns', { method: 'POST', body: JSON.stringify(payload) })
}

export function listCampaigns(status?: string): Promise<CampaignResponse[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  return request(`/campaigns${query}`)
}

export function moderateCampaign(id: number, payload: ModerationRequest): Promise<CampaignResponse> {
  return request(`/campaigns/${id}/moderate`, { method: 'POST', body: JSON.stringify(payload) })
}
